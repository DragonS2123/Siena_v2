"""Cross-vendor Windows GPU provider — DXGI for adapter identity (name,
vendor, 64-bit dedicated VRAM capacity, stable LUID) + PDH performance
counters for live usage% and VRAM used (system_metrics/dxgi.py,
system_metrics/pdh.py). Works for NVIDIA/AMD/Intel alike; NvidiaGpuProvider
is preferred over this one when nvidia-smi is available because it can also
report GPU temperature, which neither DXGI nor the public GPU Engine/GPU
Adapter Memory counters expose for any vendor.

GPU temperature is honestly always None from this provider: Windows has no
public, admin-rights-free API for GPU temperature outside vendor SDKs (AMD
ADL, NVAPI) — attempting those via raw ctypes risks crashing the whole
backend process on any struct/ABI mismatch, the same trade-off
core/system_metrics.py already made for AMD VRAM before this module existed.
"""

from __future__ import annotations

from system_metrics.dxgi import DxgiAdapter, enumerate_adapters
from system_metrics.models import GpuMetrics
from system_metrics.pdh import gpu_dedicated_vram_used_by_luid, gpu_utilization_by_luid
from system_metrics.providers.base import GpuProbeResult
from system_metrics.selection import GpuCandidate, pick_active_gpu

_VENDOR_NAMES = {0x1002: "amd", 0x10DE: "nvidia", 0x8086: "intel"}

# AMD APUs report a small "dedicated" carve-out from system RAM (typically
# well under 1 GB); real discrete cards report several GB+. NVIDIA has never
# shipped an x86 integrated GPU, so it's always treated as discrete. Intel
# is treated as always-integrated — a known miss for Intel Arc discrete
# cards, documented honestly as a limitation rather than silently guessed.
_DISCRETE_VRAM_THRESHOLD_BYTES = 1 * 1024**3


def _vendor_name(vendor_id: int) -> str:
    return _VENDOR_NAMES.get(vendor_id, "unknown")


def _classify_discrete(vendor_id: int, dedicated_vram_bytes: int) -> bool:
    if vendor_id == 0x8086:
        return False
    if vendor_id == 0x10DE:
        return True
    return dedicated_vram_bytes >= _DISCRETE_VRAM_THRESHOLD_BYTES


def _to_candidate(adapter: DxgiAdapter, usage_by_luid: dict[int, float], vram_used_by_luid: dict[int, int]) -> GpuCandidate:
    usage_percent = usage_by_luid.get(adapter.luid)
    vram_used_bytes = vram_used_by_luid.get(adapter.luid)
    vram_total_bytes = adapter.dedicated_vram_bytes or None
    vram_usage_percent = (
        round((vram_used_bytes / vram_total_bytes) * 100, 1)
        if vram_total_bytes and vram_used_bytes is not None
        else None
    )

    metrics = GpuMetrics(
        available=True,
        adapter_id=f"dxgi-{adapter.luid:x}",
        vendor=_vendor_name(adapter.vendor_id),
        name=adapter.name,
        usage_percent=round(max(0.0, min(100.0, usage_percent)), 1) if usage_percent is not None else None,
        temperature_c=None,
        vram_used_bytes=vram_used_bytes,
        vram_total_bytes=vram_total_bytes,
        vram_usage_percent=vram_usage_percent,
        availability_reason=None if usage_percent is not None else "GPU Engine performance counter has no data for this adapter",
    )
    return GpuCandidate(
        metrics=metrics,
        is_discrete=_classify_discrete(adapter.vendor_id, adapter.dedicated_vram_bytes),
        is_software=adapter.is_software,
        is_active=adapter.has_output,
    )


class WindowsGpuProvider:
    name = "windows_gpu"

    def probe(self) -> GpuProbeResult:
        adapters = enumerate_adapters()
        if not adapters:
            return GpuProbeResult(self.name, [], reason="DXGI reported no adapters (non-Windows, or dxgi.dll unavailable)")

        usage_by_luid = gpu_utilization_by_luid()
        vram_used_by_luid = gpu_dedicated_vram_used_by_luid()

        candidates = [_to_candidate(a, usage_by_luid, vram_used_by_luid) for a in adapters]
        real_candidates = [c for c in candidates if not c.is_software]
        if not real_candidates:
            return GpuProbeResult(self.name, [], reason="only a software/render-only adapter was found (no physical GPU)")
        return GpuProbeResult(self.name, candidates)

    def pick(self) -> GpuMetrics | None:
        result = self.probe()
        winner = pick_active_gpu(result.candidates)
        return winner.metrics if winner else None
