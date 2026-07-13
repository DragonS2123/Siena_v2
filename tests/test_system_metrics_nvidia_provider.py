"""NvidiaGpuProvider (system_metrics/providers/nvidia.py) — CSV parsing,
multi-GPU, and every required failure mode (missing executable, permission
denied, timeout, malformed output, driver unavailable). All exercised
against a fake find_nvidia_smi()/subprocess.run() — never spawns a real
process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from system_metrics.providers import nvidia as nvidia_module  # noqa: E402
from system_metrics.providers.nvidia import NvidiaGpuProvider  # noqa: E402


def _fake_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


def test_no_nvidia_smi_on_path_returns_no_candidates(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: None)
    provider = NvidiaGpuProvider()

    result = provider.probe()

    assert result.candidates == []
    assert "not found" in result.reason


def test_single_gpu_parsed_correctly(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    csv = "0, NVIDIA GeForce RTX 4090, 12, 24576, 4096, 62\n"
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result(csv))

    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.metrics.vendor == "nvidia"
    assert candidate.metrics.name == "NVIDIA GeForce RTX 4090"
    assert candidate.metrics.usage_percent == 12.0
    assert candidate.metrics.temperature_c == 62.0
    assert candidate.metrics.vram_total_bytes == 24576 * 1024 * 1024
    assert candidate.metrics.vram_used_bytes == 4096 * 1024 * 1024
    assert candidate.is_discrete is True
    assert candidate.is_active is True


def test_multi_gpu_parsed_as_separate_candidates(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    csv = (
        "0, NVIDIA RTX 3060, 5, 12288, 1024, 55\n"
        "1, NVIDIA RTX 4090, 40, 24576, 8192, 68\n"
    )
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result(csv))

    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert len(result.candidates) == 2
    assert {c.metrics.name for c in result.candidates} == {"NVIDIA RTX 3060", "NVIDIA RTX 4090"}


def test_pick_selects_largest_vram_among_multiple_nvidia_gpus(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    csv = (
        "0, NVIDIA RTX 3060, 5, 12288, 1024, 55\n"
        "1, NVIDIA RTX 4090, 40, 24576, 8192, 68\n"
    )
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result(csv))

    provider = NvidiaGpuProvider()
    winner = provider.pick()

    assert winner.name == "NVIDIA RTX 4090"


def test_missing_executable_reports_safe_reason(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")

    def raise_missing(*a, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(nvidia_module.subprocess, "run", raise_missing)
    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "missing" in result.reason


def test_permission_denied_reports_safe_reason(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")

    def raise_denied(*a, **k):
        raise PermissionError("access denied")

    monkeypatch.setattr(nvidia_module.subprocess, "run", raise_denied)
    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "permission" in result.reason.lower()


def test_timeout_reports_safe_reason(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3)

    monkeypatch.setattr(nvidia_module.subprocess, "run", raise_timeout)
    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "timed out" in result.reason


def test_malformed_output_reports_safe_reason(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result("garbage, not, csv\n"))

    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "parsed" in result.reason


def test_driver_unavailable_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result("", returncode=1))

    provider = NvidiaGpuProvider()
    result = provider.probe()

    assert result.candidates == []
    assert "driver" in result.reason.lower() or "error" in result.reason.lower()


def test_na_utilization_and_temperature_become_none(monkeypatch):
    monkeypatch.setattr(nvidia_module, "find_nvidia_smi", lambda: "nvidia-smi")
    csv = "0, NVIDIA Tesla T4, [N/A], 16384, 0, [N/A]\n"
    monkeypatch.setattr(nvidia_module.subprocess, "run", lambda *a, **k: _fake_result(csv))

    provider = NvidiaGpuProvider()
    result = provider.probe()

    candidate = result.candidates[0]
    assert candidate.metrics.usage_percent is None
    assert candidate.metrics.temperature_c is None
