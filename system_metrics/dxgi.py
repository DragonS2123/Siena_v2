"""Raw ctypes COM interop with dxgi.dll — no comtypes/pywin32/pip dependency,
no admin rights, no subprocess. Enumerates every GPU adapter DXGI knows
about (NVIDIA/AMD/Intel/software) with its real 64-bit dedicated VRAM
capacity — the one field Win32_VideoController.AdapterRAM gets wrong on
large cards (see core/system_metrics.py's module docstring: it truncates a
24GB AMD RX 7900 XTX down to ~4GB). DXGI_ADAPTER_DESC1.DedicatedVideoMemory
is a SIZE_T (64-bit on x64 Windows) and reads correctly.

Validated directly against this development machine's real hardware
(AMD Radeon RX 7900 XTX, 24GB) before being wired into WindowsGpuProvider.

Every public function here returns an empty/None result on ANY failure —
this module must never raise, since a COM/ctypes mistake here would
otherwise crash the whole backend process on every single metrics poll.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

_DXGI_ERROR_NOT_FOUND = 0x887A0002
_DXGI_ADAPTER_FLAG_SOFTWARE = 2


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint32),
        ("DeviceId", ctypes.c_uint32),
        ("SubSysId", ctypes.c_uint32),
        ("Revision", ctypes.c_uint32),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class DxgiAdapter:
    name: str
    vendor_id: int
    device_id: int
    dedicated_vram_bytes: int
    luid: int
    is_software: bool
    has_output: bool


def _make_guid(guid_str: str) -> _GUID:
    guid = _GUID()
    hr = ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), ctypes.byref(guid))
    if hr != 0:
        raise OSError(f"CLSIDFromString failed: {hr:#x}")
    return guid


_IID_IDXGIFactory1 = None  # lazily constructed — avoids doing COM work at import time


def _iid_factory1() -> _GUID:
    global _IID_IDXGIFactory1
    if _IID_IDXGIFactory1 is None:
        _IID_IDXGIFactory1 = _make_guid("{770aae78-f26f-4dba-a829-253c83d1b387}")
    return _IID_IDXGIFactory1


def _vtbl_call(obj_ptr: int, index: int, restype, argtypes: list, *args):
    vtable_addr = ctypes.cast(obj_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
    func_ptr = ctypes.cast(
        vtable_addr + index * ctypes.sizeof(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)
    ).contents.value
    func_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return func_type(func_ptr)(obj_ptr, *args)


def _release(obj_ptr: int | None) -> None:
    if obj_ptr:
        try:
            _vtbl_call(obj_ptr, 2, ctypes.c_uint32, [])
        except Exception:
            pass


def enumerate_adapters() -> list[DxgiAdapter]:
    """Never raises — returns [] on any COM/platform failure (e.g. non-Windows,
    dxgi.dll missing, driver in a bad state)."""
    try:
        return _enumerate_adapters_unsafe()
    except Exception:
        return []


def _enumerate_adapters_unsafe() -> list[DxgiAdapter]:
    dxgi = ctypes.windll.dxgi
    create_factory = dxgi.CreateDXGIFactory1
    create_factory.restype = ctypes.c_int32
    create_factory.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]

    factory_ptr = ctypes.c_void_p()
    hr = create_factory(ctypes.byref(_iid_factory1()), ctypes.byref(factory_ptr))
    if hr != 0 or not factory_ptr.value:
        return []

    factory = factory_ptr.value
    adapters: list[DxgiAdapter] = []
    try:
        index = 0
        while True:
            adapter_ptr = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 — vtable slot 12 (IUnknown 0-2,
            # IDXGIObject 3-6, IDXGIFactory 7-11, EnumAdapters1=12).
            hr = _vtbl_call(
                factory, 12, ctypes.c_int32,
                [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                index, ctypes.byref(adapter_ptr),
            )
            if hr != 0 or not adapter_ptr.value:
                break  # DXGI_ERROR_NOT_FOUND once index exceeds adapter count

            adapter = adapter_ptr.value
            try:
                desc = _DXGI_ADAPTER_DESC1()
                # IDXGIAdapter1::GetDesc1 — vtable slot 10 (IDXGIAdapter 7-9, GetDesc1=10).
                hr2 = _vtbl_call(adapter, 10, ctypes.c_int32, [ctypes.POINTER(_DXGI_ADAPTER_DESC1)], ctypes.byref(desc))
                if hr2 != 0:
                    index += 1
                    continue

                # IDXGIAdapter::EnumOutputs — vtable slot 7. A live display
                # output is the "this adapter is actually driving a screen"
                # heuristic used for GPU selection's is_active flag.
                output_ptr = ctypes.c_void_p()
                hr3 = _vtbl_call(
                    adapter, 7, ctypes.c_int32,
                    [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                    0, ctypes.byref(output_ptr),
                )
                has_output = hr3 == 0
                _release(output_ptr.value)

                luid = ((desc.AdapterLuid.HighPart & 0xFFFFFFFF) << 32) | (desc.AdapterLuid.LowPart & 0xFFFFFFFF)
                adapters.append(
                    DxgiAdapter(
                        name=desc.Description or "Unknown adapter",
                        vendor_id=desc.VendorId,
                        device_id=desc.DeviceId,
                        dedicated_vram_bytes=int(desc.DedicatedVideoMemory),
                        luid=luid,
                        is_software=bool(desc.Flags & _DXGI_ADAPTER_FLAG_SOFTWARE),
                        has_output=has_output,
                    )
                )
            finally:
                _release(adapter)
            index += 1
    finally:
        _release(factory)

    return adapters
