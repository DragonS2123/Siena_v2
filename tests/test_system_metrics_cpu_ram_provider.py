"""CpuRamProvider (system_metrics/providers/cpu_ram.py) — reuses
core.system_metrics.cpu_ram_metrics() for the numbers, adds physical/logical
core counts and (honestly, mostly-null-on-Windows) CPU temperature.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.providers import cpu_ram as cpu_ram_module  # noqa: E402
from system_metrics.providers.cpu_ram import CpuRamProvider  # noqa: E402


def test_cpu_snapshot_available(monkeypatch):
    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": 42.7, "ram_total_gb": 32.0, "ram_used_gb": 16.0, "ram_percent": 50.0},
    )
    monkeypatch.setattr(cpu_ram_module.psutil, "cpu_count", lambda logical=True: 16 if logical else 8)
    monkeypatch.setattr(cpu_ram_module.psutil, "sensors_temperatures", lambda: {}, raising=False)

    provider = CpuRamProvider()
    cpu = provider.probe_cpu()

    assert cpu.available is True
    assert cpu.usage_percent == 42.7
    assert cpu.physical_cores == 8
    assert cpu.logical_cores == 16


def test_ram_snapshot_available_64bit_bytes():
    provider = CpuRamProvider()

    class _FakeMetrics(dict):
        pass

    import system_metrics.providers.cpu_ram as mod

    def fake_metrics():
        return {"cpu_percent": 1.0, "ram_total_gb": 64.0, "ram_used_gb": 32.0, "ram_percent": 50.0}

    mod.cpu_ram_metrics = fake_metrics
    ram = provider.probe_ram()

    assert ram.available is True
    assert ram.total_bytes == 64 * 1024**3
    assert ram.used_bytes == 32 * 1024**3
    assert ram.usage_percent == 50.0
    assert ram.total_bytes > 2**32  # would silently wrap if stored/truncated as 32-bit


def test_cpu_unavailable_reports_reason_not_fake_zero(monkeypatch):
    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": None, "ram_total_gb": None, "ram_used_gb": None, "ram_percent": None, "cpu_ram_error": "boom"},
    )
    monkeypatch.setattr(cpu_ram_module.psutil, "cpu_count", lambda logical=True: None)

    provider = CpuRamProvider()
    cpu = provider.probe_cpu()

    assert cpu.available is False
    assert cpu.usage_percent is None
    assert cpu.availability_reason == "boom"


def test_ram_unavailable_reports_reason_not_fake_zero(monkeypatch):
    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": None, "ram_total_gb": None, "ram_used_gb": None, "ram_percent": None, "cpu_ram_error": "boom"},
    )
    provider = CpuRamProvider()
    ram = provider.probe_ram()

    assert ram.available is False
    assert ram.used_bytes is None
    assert ram.total_bytes is None
    assert ram.availability_reason == "boom"


def test_cpu_temperature_null_on_windows_with_honest_reason(monkeypatch):
    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": 5.0, "ram_total_gb": 16.0, "ram_used_gb": 8.0, "ram_percent": 50.0},
    )
    monkeypatch.setattr(cpu_ram_module.psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(cpu_ram_module.psutil, "sensors_temperatures", lambda: {}, raising=False)
    monkeypatch.setattr(cpu_ram_module.sys, "platform", "win32")

    provider = CpuRamProvider()
    cpu = provider.probe_cpu()

    assert cpu.available is True  # CPU usage itself is fine
    assert cpu.temperature_c is None
    assert cpu.availability_reason  # explains why temperature specifically is null


def test_cpu_temperature_read_when_sensors_present(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": 5.0, "ram_total_gb": 16.0, "ram_used_gb": 8.0, "ram_percent": 50.0},
    )
    monkeypatch.setattr(cpu_ram_module.psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(
        cpu_ram_module.psutil, "sensors_temperatures",
        lambda: {"coretemp": [SimpleNamespace(current=55.5)]},
        raising=False,
    )

    provider = CpuRamProvider()
    cpu = provider.probe_cpu()

    assert cpu.temperature_c == 55.5
    assert cpu.availability_reason is None


def test_percent_values_are_clamped_to_0_100(monkeypatch):
    monkeypatch.setattr(
        cpu_ram_module, "cpu_ram_metrics",
        lambda: {"cpu_percent": 150.0, "ram_total_gb": 16.0, "ram_used_gb": 16.0, "ram_percent": 105.0},
    )
    monkeypatch.setattr(cpu_ram_module.psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(cpu_ram_module.psutil, "sensors_temperatures", lambda: {}, raising=False)

    provider = CpuRamProvider()
    cpu = provider.probe_cpu()
    ram = provider.probe_ram()

    assert cpu.usage_percent == 100.0
    assert ram.usage_percent == 100.0
