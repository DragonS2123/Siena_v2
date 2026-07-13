"""WindowsGpuProvider (system_metrics/providers/windows_gpu.py) — AMD vendor
detection, discrete/integrated classification, software adapter exclusion,
24GB VRAM handling, and missing-utilization degradation. All exercised
against fake DxgiAdapter/PDH data (monkeypatched) — never calls real
ctypes/COM, so this suite runs on any platform, not just Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.dxgi import DxgiAdapter  # noqa: E402
from system_metrics.providers import windows_gpu as windows_gpu_module  # noqa: E402
from system_metrics.providers.windows_gpu import WindowsGpuProvider  # noqa: E402

_VENDOR_AMD = 0x1002
_VENDOR_NVIDIA = 0x10DE
_VENDOR_INTEL = 0x8086
_VENDOR_MICROSOFT = 0x1414


def _adapter(name, vendor_id, vram_gb, luid, *, is_software=False, has_output=True) -> DxgiAdapter:
    return DxgiAdapter(
        name=name,
        vendor_id=vendor_id,
        device_id=0,
        dedicated_vram_bytes=round(vram_gb * 1024**3),
        luid=luid,
        is_software=is_software,
        has_output=has_output,
    )


def test_no_adapters_reports_safe_reason(monkeypatch):
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: [])
    provider = WindowsGpuProvider()
    result = provider.probe()
    assert result.candidates == []
    assert result.reason


def test_amd_discrete_gpu_vendor_detection_and_24gb_vram(monkeypatch):
    adapters = [_adapter("AMD Radeon RX 7900 XTX", _VENDOR_AMD, 24, luid=0x1639F)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {0x1639F: 15.0})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {0x1639F: round(9 * 1024**3)})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.vendor == "amd"
    assert winner.name == "AMD Radeon RX 7900 XTX"
    assert winner.vram_total_bytes == 24 * 1024**3
    assert winner.vram_total_bytes > 2**32
    assert winner.usage_percent == 15.0
    assert winner.vram_used_bytes == round(9 * 1024**3)


def test_software_adapter_excluded(monkeypatch):
    adapters = [
        _adapter("Microsoft Basic Render Driver", _VENDOR_MICROSOFT, 0, luid=0x999, is_software=True, has_output=False),
        _adapter("AMD Radeon RX 7900 XTX", _VENDOR_AMD, 24, luid=0x1639F),
    ]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.name == "AMD Radeon RX 7900 XTX"


def test_only_software_adapter_reports_no_physical_gpu(monkeypatch):
    adapters = [_adapter("Microsoft Basic Render Driver", _VENDOR_MICROSOFT, 0, luid=0x999, is_software=True, has_output=False)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "software" in result.reason


def test_amd_integrated_apu_classified_as_not_discrete(monkeypatch):
    adapters = [_adapter("AMD Radeon(TM) Graphics", _VENDOR_AMD, 0.5, luid=0x18714, has_output=False)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    result = provider.probe()

    assert result.candidates[0].is_discrete is False


def test_intel_always_classified_as_integrated(monkeypatch):
    adapters = [_adapter("Intel Iris Xe Graphics", _VENDOR_INTEL, 4, luid=0x1, has_output=True)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    result = provider.probe()

    assert result.candidates[0].is_discrete is False


def test_nvidia_always_classified_as_discrete(monkeypatch):
    adapters = [_adapter("NVIDIA GeForce RTX 4090", _VENDOR_NVIDIA, 24, luid=0x2, has_output=True)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    result = provider.probe()

    assert result.candidates[0].is_discrete is True


def test_two_discrete_cards_selection(monkeypatch):
    adapters = [
        _adapter("AMD RX 6600", _VENDOR_AMD, 8, luid=0x10, has_output=True),
        _adapter("NVIDIA RTX 4090", _VENDOR_NVIDIA, 24, luid=0x20, has_output=True),
    ]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.name == "NVIDIA RTX 4090"  # larger VRAM, both active discrete


def test_missing_utilization_counter_data_yields_null_not_zero(monkeypatch):
    adapters = [_adapter("AMD Radeon RX 7900 XTX", _VENDOR_AMD, 24, luid=0x1639F)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {})  # no data for this LUID
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.usage_percent is None
    assert winner.availability_reason


def test_utilization_percent_clamped_0_100(monkeypatch):
    adapters = [_adapter("AMD Radeon RX 7900 XTX", _VENDOR_AMD, 24, luid=0x1639F)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {0x1639F: 187.0})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.usage_percent == 100.0


def test_temperature_always_none_on_windows_gpu_provider(monkeypatch):
    adapters = [_adapter("AMD Radeon RX 7900 XTX", _VENDOR_AMD, 24, luid=0x1639F)]
    monkeypatch.setattr(windows_gpu_module, "enumerate_adapters", lambda: adapters)
    monkeypatch.setattr(windows_gpu_module, "gpu_utilization_by_luid", lambda: {0x1639F: 5.0})
    monkeypatch.setattr(windows_gpu_module, "gpu_dedicated_vram_used_by_luid", lambda: {})

    provider = WindowsGpuProvider()
    winner = provider.pick()

    assert winner.temperature_c is None
