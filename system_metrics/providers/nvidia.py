"""NVIDIA GPU provider — nvidia-smi subprocess, same safe/never-raises
contract and cached PATH lookup as core/system_metrics.py::vram_metrics()
(reused via find_nvidia_smi() rather than re-probing PATH on every AMD/Intel
machine this backend also runs on). Extends that module's memory-only query
with name/utilization/temperature so this provider can fill every GpuMetrics
field nvidia-smi actually exposes, instead of just VRAM.

Multiple NVIDIA GPUs are supported: nvidia-smi lists one CSV line per
physical card; each becomes its own GpuCandidate (always discrete — NVIDIA
has never shipped an x86 integrated GPU — and always considered "active"
since nvidia-smi only enumerates installed, driver-attached cards), and
selection.pick_active_gpu() picks the winner using the same rules as every
other provider.
"""

from __future__ import annotations

import subprocess

from core.system_metrics import find_nvidia_smi
from system_metrics.models import GpuMetrics
from system_metrics.providers.base import GpuProbeResult
from system_metrics.selection import GpuCandidate, pick_active_gpu

_QUERY_FIELDS = "index,name,utilization.gpu,memory.total,memory.used,temperature.gpu"
_TIMEOUT_SECONDS = 3


def _to_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw or raw in ("[N/A]", "N/A"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_line(line: str, index_in_output: int) -> GpuCandidate | None:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 6:
        return None
    index_str, name, util_str, mem_total_str, mem_used_str, temp_str = parts

    util = _to_float(util_str)
    mem_total_mib = _to_float(mem_total_str)
    mem_used_mib = _to_float(mem_used_str)
    temp = _to_float(temp_str)

    vram_total_bytes = round(mem_total_mib * 1024 * 1024) if mem_total_mib is not None else None
    vram_used_bytes = round(mem_used_mib * 1024 * 1024) if mem_used_mib is not None else None
    vram_usage_percent = (
        round((vram_used_bytes / vram_total_bytes) * 100, 1)
        if vram_total_bytes and vram_used_bytes is not None
        else None
    )

    metrics = GpuMetrics(
        available=True,
        adapter_id=f"nvidia-{index_str or index_in_output}",
        vendor="nvidia",
        name=name or None,
        usage_percent=max(0.0, min(100.0, util)) if util is not None else None,
        temperature_c=temp,
        vram_used_bytes=vram_used_bytes,
        vram_total_bytes=vram_total_bytes,
        vram_usage_percent=vram_usage_percent,
    )
    return GpuCandidate(metrics=metrics, is_discrete=True, is_software=False, is_active=True)


class NvidiaGpuProvider:
    name = "nvidia"

    def probe(self) -> GpuProbeResult:
        exe = find_nvidia_smi()
        if not exe:
            return GpuProbeResult(self.name, [], reason="nvidia-smi not found on PATH — no NVIDIA GPU detected")

        try:
            result = subprocess.run(
                [exe, f"--query-gpu={_QUERY_FIELDS}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return GpuProbeResult(self.name, [], reason="nvidia-smi executable missing")
        except PermissionError:
            return GpuProbeResult(self.name, [], reason="nvidia-smi permission denied")
        except subprocess.TimeoutExpired:
            return GpuProbeResult(self.name, [], reason="nvidia-smi call timed out")
        except OSError as exc:
            return GpuProbeResult(self.name, [], reason=f"nvidia-smi call failed: {exc.__class__.__name__}")

        if result.returncode != 0:
            return GpuProbeResult(
                self.name, [], reason="nvidia-smi reported an error (driver unavailable or no NVIDIA GPU present)"
            )

        candidates: list[GpuCandidate] = []
        for i, line in enumerate(result.stdout.strip().splitlines()):
            if not line.strip():
                continue
            candidate = _parse_line(line, i)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            return GpuProbeResult(self.name, [], reason="nvidia-smi output could not be parsed")
        return GpuProbeResult(self.name, candidates)

    def pick(self) -> GpuMetrics | None:
        result = self.probe()
        winner = pick_active_gpu(result.candidates)
        return winner.metrics if winner else None
