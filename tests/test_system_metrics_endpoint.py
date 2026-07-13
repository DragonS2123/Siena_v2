"""GET /api/system/metrics — endpoint response shape and diagnostics
privacy (only provider name/vendor/availability/elapsed-time/safe-error-code
ever get logged, never raw exception text, hardware serial numbers, or
credentials).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from system_metrics.models import CpuMetrics, GpuMetrics, RamMetrics, SystemMetricsSnapshot  # noqa: E402


class _RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None:
        self.events.append((event_type, fields))

    def error(self, event_type: str, console_message: str, **fields: Any) -> None:
        self.events.append((event_type, fields))


class _FakeSystemMetricsService:
    def __init__(self, snapshot: SystemMetricsSnapshot):
        self._snapshot = snapshot

    async def get_snapshot(self) -> SystemMetricsSnapshot:
        return self._snapshot


_FAKE_SNAPSHOT = SystemMetricsSnapshot(
    sampled_at_utc="2026-07-13T00:00:00Z",
    computer_name="TESTHOST",
    stale=False,
    cpu=CpuMetrics(available=True, usage_percent=12.3, physical_cores=8, logical_cores=16),
    ram=RamMetrics(available=True, used_bytes=1024, total_bytes=2048, usage_percent=50.0),
    gpu=GpuMetrics(
        available=True, adapter_id="dxgi-1639f", vendor="amd", name="AMD Radeon RX 7900 XTX",
        usage_percent=5.0, vram_used_bytes=1024, vram_total_bytes=25769803776, vram_usage_percent=0.1,
    ),
    providers={"cpu_ram": "psutil", "gpu": "windows_gpu"},
    safe_errors=["nvidia: nvidia-smi not found on PATH"],
)


@pytest.fixture()
def recording_logger(monkeypatch):
    logger = _RecordingLogger()
    monkeypatch.setattr(server, "base_logger", logger)
    monkeypatch.setattr(server, "system_metrics_service", _FakeSystemMetricsService(_FAKE_SNAPSHOT))
    return logger


def test_endpoint_returns_snapshot_shape(recording_logger):
    response = TestClient(server.app).get("/api/system/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["computer_name"] == "TESTHOST"
    assert body["cpu"]["usage_percent"] == 12.3
    assert body["ram"]["total_bytes"] == 2048
    assert body["gpu"]["vendor"] == "amd"
    assert body["gpu"]["vram_total_bytes"] == 25769803776
    assert body["providers"] == {"cpu_ram": "psutil", "gpu": "windows_gpu"}


def test_endpoint_logs_snapshot_requested_event(recording_logger):
    TestClient(server.app).get("/api/system/metrics")

    event_types = [e for e, _ in recording_logger.events]
    assert "system_metrics_snapshot_requested" in event_types


def test_diagnostics_never_carry_hardware_names_or_serials(recording_logger):
    TestClient(server.app).get("/api/system/metrics")

    for event_type, fields in recording_logger.events:
        serialized = repr(fields)
        assert "AMD Radeon RX 7900 XTX" not in serialized
        assert "dxgi-1639f" not in serialized


def test_diagnostics_never_carry_raw_exception_text_from_a_failing_provider(monkeypatch):
    from system_metrics.providers.cpu_ram import CpuRamProvider
    from system_metrics.service import SystemMetricsService

    secret_path = r"C:\Users\frunk\AppData\Local\some-secret-internal-detail.txt"

    class _RaisingGpuProvider:
        name = "broken_provider"

        def probe(self):
            raise RuntimeError(f"failed while reading {secret_path}")

    logger = _RecordingLogger()
    monkeypatch.setattr(server, "base_logger", logger)
    monkeypatch.setattr(
        server, "system_metrics_service",
        SystemMetricsService(CpuRamProvider(), [_RaisingGpuProvider()], logger=logger),
    )

    response = TestClient(server.app).get("/api/system/metrics")

    assert response.status_code == 200
    for _event_type, fields in logger.events:
        serialized = repr(fields)
        assert secret_path not in serialized
        assert "failed while reading" not in serialized
