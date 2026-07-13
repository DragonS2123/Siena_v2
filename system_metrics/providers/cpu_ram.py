"""CPU/RAM provider — reuses core/system_metrics.py::cpu_ram_metrics() (the
existing, already-relied-upon psutil path used by the Runtime view and
Computer Awareness) for usage percent/RAM bytes, and adds the two fields
that function doesn't expose: physical/logical core counts and (where the
platform actually provides it) CPU temperature.

psutil.sensors_temperatures() is Linux-only by psutil's own documentation —
it always returns an empty dict on Windows, so CPU temperature is honestly
None with a reason on every Windows machine (there is no public, safe,
admin-rights-free API for CPU temperature on Windows outside vendor SDKs —
same class of limitation as AMD GPU temperature, see windows_gpu.py).
"""

from __future__ import annotations

import sys

import psutil

from core.system_metrics import cpu_ram_metrics
from system_metrics.models import CpuMetrics, RamMetrics


def _cpu_temperature_c() -> tuple[float | None, str | None]:
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None, "psutil.sensors_temperatures is not available on this platform"
    try:
        readings = sensors()
    except Exception as exc:  # defensive only — some platforms raise instead of returning {}
        return None, f"sensors_temperatures failed: {exc.__class__.__name__}"
    if not readings:
        return None, (
            "CPU temperature sensors are not exposed via psutil on this platform (Windows has no "
            "public, admin-free CPU temperature API outside vendor SDKs)"
            if sys.platform == "win32"
            else "no temperature sensors reported"
        )
    for entries in readings.values():
        for entry in entries:
            current = getattr(entry, "current", None)
            if isinstance(current, (int, float)):
                return float(current), None
    return None, "temperature sensors present but reported no readable value"


class CpuRamProvider:
    name = "psutil"

    def probe_cpu(self) -> CpuMetrics:
        try:
            physical_cores = psutil.cpu_count(logical=False)
            logical_cores = psutil.cpu_count(logical=True)
        except Exception:
            physical_cores = None
            logical_cores = None

        metrics = cpu_ram_metrics()
        temperature_c, temperature_reason = _cpu_temperature_c()

        usage_percent = metrics.get("cpu_percent")
        if usage_percent is None:
            return CpuMetrics(
                available=False,
                physical_cores=physical_cores,
                logical_cores=logical_cores,
                availability_reason=metrics.get("cpu_ram_error") or "CPU usage unavailable",
            )
        return CpuMetrics(
            available=True,
            usage_percent=max(0.0, min(100.0, float(usage_percent))),
            physical_cores=physical_cores,
            logical_cores=logical_cores,
            temperature_c=temperature_c,
            # CpuMetrics has one shared availability_reason field (matches the
            # task's spec) — CPU usage itself is available here, so this only
            # ever explains why temperature_c specifically came back null.
            availability_reason=None if temperature_c is not None else temperature_reason,
        )

    def probe_ram(self) -> RamMetrics:
        metrics = cpu_ram_metrics()
        total_gb = metrics.get("ram_total_gb")
        used_gb = metrics.get("ram_used_gb")
        percent = metrics.get("ram_percent")
        if total_gb is None or used_gb is None or percent is None:
            return RamMetrics(
                available=False,
                availability_reason=metrics.get("cpu_ram_error") or "RAM usage unavailable",
            )
        _GB = 1024**3
        return RamMetrics(
            available=True,
            used_bytes=round(used_gb * _GB),
            total_bytes=round(total_gb * _GB),
            usage_percent=max(0.0, min(100.0, float(percent))),
        )
