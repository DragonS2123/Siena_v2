"""Typed model contract: default/unavailable instances must never contain a
fabricated 0%/0 GB/0°C value — only None + a reason. See
system_metrics/models.py's module docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.models import (  # noqa: E402
    NO_GPU_DETECTED,
    CpuMetrics,
    GpuMetrics,
    RamMetrics,
    SystemMetricsSnapshot,
)


def test_cpu_metrics_unavailable_has_no_fake_zero():
    cpu = CpuMetrics(available=False, availability_reason="psutil unavailable")
    d = cpu.to_dict()
    assert d["usage_percent"] is None
    assert d["physical_cores"] is None
    assert d["logical_cores"] is None
    assert d["temperature_c"] is None
    assert d["availability_reason"] == "psutil unavailable"


def test_ram_metrics_unavailable_has_no_fake_zero():
    ram = RamMetrics(available=False, availability_reason="virtual_memory failed")
    d = ram.to_dict()
    assert d["used_bytes"] is None
    assert d["total_bytes"] is None
    assert d["usage_percent"] is None


def test_gpu_metrics_unavailable_has_no_fake_zero():
    gpu = NO_GPU_DETECTED
    d = gpu.to_dict()
    assert d["available"] is False
    assert d["usage_percent"] is None
    assert d["vram_used_bytes"] is None
    assert d["vram_total_bytes"] is None
    assert d["vram_usage_percent"] is None
    assert d["temperature_c"] is None
    assert d["availability_reason"]


def test_gpu_metrics_64bit_vram_value_is_not_truncated():
    twenty_four_gb = 24 * 1024**3
    gpu = GpuMetrics(available=True, vendor="amd", name="Test GPU", vram_total_bytes=twenty_four_gb)
    assert gpu.to_dict()["vram_total_bytes"] == twenty_four_gb
    assert gpu.to_dict()["vram_total_bytes"] > 2**32  # would silently wrap if stored as a 32-bit field


def test_snapshot_to_dict_round_trip():
    snapshot = SystemMetricsSnapshot(
        sampled_at_utc="2026-07-13T00:00:00Z",
        computer_name="TESTHOST",
        stale=False,
        cpu=CpuMetrics(available=True, usage_percent=12.3, physical_cores=8, logical_cores=16),
        ram=RamMetrics(available=True, used_bytes=100, total_bytes=200, usage_percent=50.0),
        gpu=NO_GPU_DETECTED,
        providers={"cpu_ram": "psutil", "gpu": "none"},
        safe_errors=["nvidia: not found"],
    )
    d = snapshot.to_dict()
    assert d["computer_name"] == "TESTHOST"
    assert d["cpu"]["usage_percent"] == 12.3
    assert d["providers"] == {"cpu_ram": "psutil", "gpu": "none"}
    assert d["safe_errors"] == ["nvidia: not found"]
