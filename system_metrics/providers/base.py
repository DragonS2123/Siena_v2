"""Common provider contract. Every GPU provider's probe() must never raise —
callers (SystemMetricsService) treat an exception as a bug in the provider,
not a normal "hardware not present" outcome, which is instead expressed as
an empty candidate list plus a safe `reason` string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from system_metrics.selection import GpuCandidate


@dataclass(frozen=True)
class GpuProbeResult:
    provider_name: str
    candidates: list[GpuCandidate]
    # Set when the provider found zero candidates — a safe, content-free
    # explanation (e.g. "nvidia-smi not found on PATH"), never a raw
    # exception message or stack trace.
    reason: str | None = None


class GpuProvider(Protocol):
    name: str

    def probe(self) -> GpuProbeResult:
        """Never raises. Returns whatever candidates this provider can see
        (possibly empty) plus a safe reason when empty."""
        ...
