"""Typed, vendor-neutral system metrics models returned by
SystemMetricsService (system_metrics/service.py). Every optional field is
None with a safe availability_reason when the real value can't be
obtained — never a fabricated 0%/0 GB/0°C substitute for missing data (see
each provider module's docstring for exactly why a given field can be
unavailable on a given machine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CpuMetrics:
    available: bool
    usage_percent: float | None = None
    physical_cores: int | None = None
    logical_cores: int | None = None
    temperature_c: float | None = None
    availability_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "usage_percent": self.usage_percent,
            "physical_cores": self.physical_cores,
            "logical_cores": self.logical_cores,
            "temperature_c": self.temperature_c,
            "availability_reason": self.availability_reason,
        }


@dataclass(frozen=True)
class RamMetrics:
    available: bool
    used_bytes: int | None = None
    total_bytes: int | None = None
    usage_percent: float | None = None
    availability_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "used_bytes": self.used_bytes,
            "total_bytes": self.total_bytes,
            "usage_percent": self.usage_percent,
            "availability_reason": self.availability_reason,
        }


@dataclass(frozen=True)
class GpuMetrics:
    available: bool
    adapter_id: str | None = None
    vendor: str | None = None  # "nvidia" | "amd" | "intel" | "unknown"
    name: str | None = None
    usage_percent: float | None = None
    temperature_c: float | None = None
    vram_used_bytes: int | None = None
    vram_total_bytes: int | None = None
    vram_usage_percent: float | None = None
    availability_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "adapter_id": self.adapter_id,
            "vendor": self.vendor,
            "name": self.name,
            "usage_percent": self.usage_percent,
            "temperature_c": self.temperature_c,
            "vram_used_bytes": self.vram_used_bytes,
            "vram_total_bytes": self.vram_total_bytes,
            "vram_usage_percent": self.vram_usage_percent,
            "availability_reason": self.availability_reason,
        }


NO_GPU_DETECTED = GpuMetrics(available=False, availability_reason="No GPU adapter could be detected on this system")


@dataclass(frozen=True)
class SystemMetricsSnapshot:
    sampled_at_utc: str
    computer_name: str
    stale: bool
    cpu: CpuMetrics
    ram: RamMetrics
    gpu: GpuMetrics
    # e.g. {"gpu": "nvidia", "cpu_ram": "psutil"} — which provider actually
    # produced each section, for diagnostics/debugging, never for behavior.
    providers: dict[str, str] = field(default_factory=dict)
    # Safe, content-free error/skip reasons collected along the way (provider
    # names + safe error codes only — never raw exception text/stack traces).
    safe_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sampled_at_utc": self.sampled_at_utc,
            "computer_name": self.computer_name,
            "stale": self.stale,
            "cpu": self.cpu.to_dict(),
            "ram": self.ram.to_dict(),
            "gpu": self.gpu.to_dict(),
            "providers": dict(self.providers),
            "safe_errors": list(self.safe_errors),
        }
