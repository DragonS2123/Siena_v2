"""Raw ctypes wrapper around PDH (Performance Data Helper) — reads the
`GPU Engine` and `GPU Adapter Memory` counter sets Windows has exposed since
the 2004 update (the same counters Task Manager's GPU tab uses), grouped per
adapter LUID. No admin rights required — these are ordinary user-readable
performance counters, not WMI/kernel driver queries.

`GPU Engine\\Utilization Percentage` has one instance PER (process, engine
type) pair — e.g. a single adapter can have separate "3D", "Copy", "Video
Decode" engines, each independently up to 100%. Summing across DIFFERENT
engine types would produce a meaningless number that can exceed 100% without
representing anything real, so this module sums utilization only WITHIN the
same (LUID, engine type) group (multiple processes legitimately share one
engine and their percentages of it correctly add up), then reports the
maximum across engine-type groups per LUID as that adapter's overall
"usage_percent" — the same approach Task Manager's single "GPU" summary
number uses (dominated by whichever engine is currently busiest).

Every public function here degrades to an empty dict on ANY failure — never
raises, since a PDH/ctypes mistake here must not crash metrics collection.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes

_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2  # unsigned; compare against masked signed return value

_INSTANCE_LUID_RE = re.compile(r"luid_0x([0-9A-Fa-f]+)_0x([0-9A-Fa-f]+)_phys_\d+", re.IGNORECASE)
_ENGTYPE_RE = re.compile(r"engtype_(\w+)", re.IGNORECASE)

# A short settle delay is required: GPU Engine's "Utilization Percentage" is
# derived from a running-time delta between two samples, like "% Processor
# Time" — a single PdhCollectQueryData call always reads back 0.
_SAMPLE_INTERVAL_SECONDS = 0.2


class _PDH_FMT_COUNTERVALUE_ITEM(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


def _pdh():
    pdh = ctypes.windll.pdh
    pdh.PdhOpenQueryW.restype = ctypes.c_ulong
    pdh.PdhAddEnglishCounterW.restype = ctypes.c_ulong
    pdh.PdhCollectQueryData.restype = ctypes.c_ulong
    pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_ulong
    pdh.PdhCloseQuery.restype = ctypes.c_ulong
    return pdh


def _luid_from_instance(name: str) -> int | None:
    match = _INSTANCE_LUID_RE.search(name)
    if not match:
        return None
    high, low = match.groups()
    return ((int(high, 16) & 0xFFFFFFFF) << 32) | (int(low, 16) & 0xFFFFFFFF)


def _read_wildcard_counter(path: str) -> list[tuple[str, float]]:
    """Returns [(instance_name, value), ...] for every matching instance.
    Never raises — returns [] on any PDH failure (missing counter set,
    permission issue, malformed buffer)."""
    pdh = _pdh()
    query = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
        return []
    try:
        counter = wintypes.HANDLE()
        if pdh.PdhAddEnglishCounterW(query, ctypes.c_wchar_p(path), 0, ctypes.byref(counter)) != 0:
            return []

        pdh.PdhCollectQueryData(query)
        time.sleep(_SAMPLE_INTERVAL_SECONDS)
        if pdh.PdhCollectQueryData(query) != 0:
            return []

        buffer_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        result = pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(buffer_size), ctypes.byref(item_count), None
        )
        if (result & 0xFFFFFFFF) != _PDH_MORE_DATA or buffer_size.value == 0:
            return []

        buf = ctypes.create_string_buffer(buffer_size.value)
        result = pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(buffer_size), ctypes.byref(item_count),
            ctypes.cast(buf, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM)),
        )
        if result != 0:
            return []

        items = ctypes.cast(buf, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM))
        out = []
        for i in range(item_count.value):
            item = items[i]
            if item.CStatus == 0 and item.szName:
                out.append((item.szName, item.doubleValue))
        return out
    except Exception:
        return []
    finally:
        pdh.PdhCloseQuery(query)


def gpu_utilization_by_luid() -> dict[int, float]:
    """{luid: usage_percent (0-100)} — max across engine-type groups per
    LUID, each group itself the sum of same-engine-type utilization across
    processes (see module docstring for why summing is scoped this way)."""
    samples = _read_wildcard_counter(r"\GPU Engine(*)\Utilization Percentage")
    if not samples:
        return {}

    # (luid, engtype) -> summed percent
    by_engine: dict[tuple[int, str], float] = {}
    for name, value in samples:
        luid = _luid_from_instance(name)
        if luid is None:
            continue
        engtype_match = _ENGTYPE_RE.search(name)
        engtype = engtype_match.group(1).lower() if engtype_match else "unknown"
        by_engine[(luid, engtype)] = by_engine.get((luid, engtype), 0.0) + value

    by_luid: dict[int, float] = {}
    for (luid, _engtype), percent in by_engine.items():
        clamped = max(0.0, min(100.0, percent))
        by_luid[luid] = max(by_luid.get(luid, 0.0), clamped)
    return by_luid


def gpu_dedicated_vram_used_by_luid() -> dict[int, int]:
    """{luid: used_bytes} from the `GPU Adapter Memory\\Dedicated Usage`
    counter — this is USAGE only; total capacity must come from
    dxgi.py::enumerate_adapters() (DXGI_ADAPTER_DESC1.DedicatedVideoMemory),
    since no PDH/WMI counter exposes adapter VRAM capacity at all."""
    samples = _read_wildcard_counter(r"\GPU Adapter Memory(*)\Dedicated Usage")
    out: dict[int, int] = {}
    for name, value in samples:
        luid = _luid_from_instance(name)
        if luid is None:
            continue
        out[luid] = out.get(luid, 0) + max(0, round(value))
    return out
