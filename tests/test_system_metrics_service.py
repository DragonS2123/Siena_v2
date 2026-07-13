"""SystemMetricsService orchestration (system_metrics/service.py) —
provider fallback order, a provider raising an exception never taking down
the whole snapshot, timeout degrading to a stale snapshot instead of
hanging, and asyncio.CancelledError propagating instead of being swallowed.
Uses fully fake CPU/RAM/GPU providers — never touches real psutil/ctypes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.models import CpuMetrics, GpuMetrics, RamMetrics  # noqa: E402
from system_metrics.providers.base import GpuProbeResult  # noqa: E402
from system_metrics.selection import GpuCandidate  # noqa: E402
from system_metrics.service import SystemMetricsService  # noqa: E402


class _FakeCpuRamProvider:
    name = "fake_psutil"

    def __init__(self, cpu=None, ram=None):
        self._cpu = cpu or CpuMetrics(available=True, usage_percent=10.0, physical_cores=4, logical_cores=8)
        self._ram = ram or RamMetrics(available=True, used_bytes=1, total_bytes=2, usage_percent=50.0)

    def probe_cpu(self) -> CpuMetrics:
        return self._cpu

    def probe_ram(self) -> RamMetrics:
        return self._ram


class _FakeGpuProvider:
    def __init__(self, name: str, candidates=None, reason=None, raises: Exception | None = None):
        self.name = name
        self._candidates = candidates or []
        self._reason = reason
        self._raises = raises

    def probe(self) -> GpuProbeResult:
        if self._raises is not None:
            raise self._raises
        return GpuProbeResult(self.name, self._candidates, self._reason)


def _gpu_candidate(name: str, vendor: str) -> GpuCandidate:
    metrics = GpuMetrics(available=True, adapter_id=name, vendor=vendor, name=name, vram_total_bytes=1024)
    return GpuCandidate(metrics=metrics, is_discrete=True, is_software=False, is_active=True)


def _run(coro):
    return asyncio.run(coro)


def test_provider_fallback_first_empty_second_succeeds():
    empty = _FakeGpuProvider("nvidia", candidates=[], reason="nvidia-smi not found")
    winning = _FakeGpuProvider("windows_gpu", candidates=[_gpu_candidate("AMD Radeon RX 7900 XTX", "amd")])
    service = SystemMetricsService(_FakeCpuRamProvider(), [empty, winning])

    snapshot = _run(service.get_snapshot())

    assert snapshot.gpu.name == "AMD Radeon RX 7900 XTX"
    assert snapshot.providers["gpu"] == "windows_gpu"
    assert any("nvidia-smi not found" in e for e in snapshot.safe_errors)


def test_all_providers_empty_falls_back_to_none():
    empty1 = _FakeGpuProvider("nvidia", candidates=[], reason="not found")
    empty2 = _FakeGpuProvider("windows_gpu", candidates=[], reason="no adapters")
    service = SystemMetricsService(_FakeCpuRamProvider(), [empty1, empty2])

    snapshot = _run(service.get_snapshot())

    assert snapshot.gpu.available is False
    assert snapshot.providers["gpu"] == "none"
    assert snapshot.gpu.usage_percent is None  # never a fake zero


def test_provider_exception_is_skipped_not_fatal():
    broken = _FakeGpuProvider("nvidia", raises=RuntimeError("boom"))
    winning = _FakeGpuProvider("windows_gpu", candidates=[_gpu_candidate("Test GPU", "amd")])
    service = SystemMetricsService(_FakeCpuRamProvider(), [broken, winning])

    snapshot = _run(service.get_snapshot())

    assert snapshot.gpu.name == "Test GPU"
    assert any("nvidia" in e and "probe_exception" in e for e in snapshot.safe_errors)


def test_cpu_ram_provider_exception_degrades_without_crashing():
    class _BrokenCpuRam:
        name = "broken"

        def probe_cpu(self):
            raise RuntimeError("boom")

        def probe_ram(self):
            raise RuntimeError("boom")

    service = SystemMetricsService(_BrokenCpuRam(), [_FakeGpuProvider("none", candidates=[])])
    snapshot = _run(service.get_snapshot())

    assert snapshot.cpu.available is False
    assert snapshot.ram.available is False
    assert snapshot.cpu.usage_percent is None  # never a fake zero


def test_timeout_returns_stale_snapshot_instead_of_hanging():
    class _SlowCpuRam:
        name = "slow"

        def probe_cpu(self):
            import time

            time.sleep(2)
            return CpuMetrics(available=True, usage_percent=1.0)

        def probe_ram(self):
            return RamMetrics(available=True, used_bytes=1, total_bytes=2, usage_percent=50.0)

    service = SystemMetricsService(_SlowCpuRam(), [_FakeGpuProvider("none", candidates=[])], timeout_seconds=0.05)
    snapshot = _run(service.get_snapshot())

    assert snapshot.stale is True
    assert snapshot.cpu.available is False
    assert "collection_timeout" in snapshot.safe_errors


def test_cancellation_propagates_not_swallowed():
    class _NeverEndingCpuRam:
        name = "never"

        def probe_cpu(self):
            import time

            # Long enough to outlast the cancel below; asyncio.to_thread
            # can't force-kill the underlying OS thread (a real limitation —
            # see service.py's module docstring), so this still keeps the
            # thread pool busy briefly after cancellation, just not 30s of it.
            time.sleep(2)
            return CpuMetrics(available=True)

        def probe_ram(self):
            return RamMetrics(available=True)

    service = SystemMetricsService(_NeverEndingCpuRam(), [_FakeGpuProvider("none", candidates=[])], timeout_seconds=30)

    async def scenario():
        task = asyncio.create_task(service.get_snapshot())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario())


def test_gpu_selection_picks_largest_vram_within_a_provider():
    provider = _FakeGpuProvider(
        "windows_gpu",
        candidates=[_gpu_candidate("Small GPU", "amd"), _gpu_candidate("Big GPU", "amd")],
    )
    # Give the second candidate a larger VRAM so selection is deterministic.
    provider._candidates[1] = GpuCandidate(
        metrics=GpuMetrics(available=True, adapter_id="big", vendor="amd", name="Big GPU", vram_total_bytes=1_000_000),
        is_discrete=True, is_software=False, is_active=True,
    )
    service = SystemMetricsService(_FakeCpuRamProvider(), [provider])

    snapshot = _run(service.get_snapshot())

    assert snapshot.gpu.name == "Big GPU"


def test_snapshot_includes_provider_names():
    winning = _FakeGpuProvider("windows_gpu", candidates=[_gpu_candidate("Test GPU", "amd")])
    service = SystemMetricsService(_FakeCpuRamProvider(), [winning])

    snapshot = _run(service.get_snapshot())

    assert snapshot.providers["cpu_ram"] == "fake_psutil"
    assert snapshot.providers["gpu"] == "windows_gpu"
