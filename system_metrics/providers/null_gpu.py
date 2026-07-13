"""Fallback provider used when no real GPU provider can produce any
candidate (non-Windows without nvidia-smi, or every provider failed) — the
service always ends with a GpuMetrics, never a missing section.
"""

from __future__ import annotations

from system_metrics.providers.base import GpuProbeResult


class NullGpuProvider:
    name = "none"

    def probe(self) -> GpuProbeResult:
        return GpuProbeResult(self.name, [], reason="no GPU provider was able to detect a GPU on this system")
