"""Deterministic GPU adapter selection — used by each multi-adapter provider
(WindowsGpuProvider today; NvidiaGpuProvider if nvidia-smi ever lists more
than one card) to pick ONE winning adapter out of several candidates on the
same machine. Never selects a software/render-only adapter (Microsoft Basic
Render Driver and equivalents).

Priority order (highest first):
  1. an active discrete GPU (has a display attached / is driving output)
  2. the discrete GPU with the largest dedicated VRAM (covers "discrete but
     not currently driving a display", e.g. a headless compute card)
  3. an active integrated GPU
  4. the first well-formed (non-software) adapter found, as a last resort
"""

from __future__ import annotations

from dataclasses import dataclass

from system_metrics.models import GpuMetrics


@dataclass(frozen=True)
class GpuCandidate:
    metrics: GpuMetrics
    is_discrete: bool | None  # True=discrete, False=integrated, None=unknown
    is_software: bool  # Microsoft Basic Render Driver / any software rasterizer
    is_active: bool  # heuristic: currently driving a display / in active use


def pick_active_gpu(candidates: list[GpuCandidate]) -> GpuCandidate | None:
    real = [c for c in candidates if not c.is_software]
    if not real:
        return None

    discrete = [c for c in real if c.is_discrete is True]
    active_discrete = [c for c in discrete if c.is_active]
    if active_discrete:
        return max(active_discrete, key=lambda c: c.metrics.vram_total_bytes or 0)
    if discrete:
        return max(discrete, key=lambda c: c.metrics.vram_total_bytes or 0)

    integrated = [c for c in real if c.is_discrete is False]
    active_integrated = [c for c in integrated if c.is_active]
    if active_integrated:
        return active_integrated[0]
    if integrated:
        return integrated[0]

    return real[0]
