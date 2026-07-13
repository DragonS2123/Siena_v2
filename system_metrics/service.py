"""SystemMetricsService — the one entry point api/server.py talks to for
GET /api/system/metrics. Owns provider selection/fallback so callers never
see a provider object directly.

Provider fallback order for GPU (tried one at a time, never concurrently —
"не запускать несколько параллельных тяжёлых probe"): NVIDIA (nvidia-smi;
preferred when present because it also reports temperature) -> Windows
DXGI/PDH (any vendor, no temperature) -> Null (no GPU detected at all). A
provider that raises is treated as a provider bug, logged, and skipped —
it never takes the whole snapshot down.

All actual collection work (subprocess calls, ctypes/COM calls, PDH's
required settle-sleep) is synchronous/blocking, so it always runs inside
asyncio.to_thread() under an overall timeout — a hung probe degrades to a
stale snapshot instead of hanging the request or the event loop.

Known limitation: asyncio.to_thread() cannot force-kill the underlying OS
thread. A timeout/cancellation stops THIS coroutine from waiting any
longer (the caller gets its stale snapshot / CancelledError promptly), but
if a probe were to genuinely hang (e.g. a stuck native call), its thread
would keep running in the background thread pool until it eventually
returns — the same limitation any blocking call wrapped in
asyncio.to_thread has. There is no safe cross-platform way to forcibly
terminate a Python thread.
"""

from __future__ import annotations

import asyncio
import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from system_metrics.models import CpuMetrics, GpuMetrics, RamMetrics, SystemMetricsSnapshot
from system_metrics.providers.base import GpuProvider
from system_metrics.selection import pick_active_gpu


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class _CpuRamProviderLike(Protocol):
    def probe_cpu(self) -> CpuMetrics: ...
    def probe_ram(self) -> RamMetrics: ...


_UNAVAILABLE_CPU = CpuMetrics(available=False, availability_reason="metrics collection timed out")
_UNAVAILABLE_RAM = RamMetrics(available=False, availability_reason="metrics collection timed out")
_UNAVAILABLE_GPU = GpuMetrics(available=False, availability_reason="metrics collection timed out")

DEFAULT_TIMEOUT_SECONDS = 5.0


class SystemMetricsService:
    def __init__(
        self,
        cpu_ram_provider: _CpuRamProviderLike,
        gpu_providers: list[GpuProvider],
        logger: _LoggerLike | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._cpu_ram_provider = cpu_ram_provider
        self._gpu_providers = gpu_providers
        self._logger = logger
        self._timeout_seconds = timeout_seconds

    async def get_snapshot(self) -> SystemMetricsSnapshot:
        computer_name = _safe_hostname()
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._collect_sync, computer_name), timeout=self._timeout_seconds)
        except asyncio.TimeoutError:
            if self._logger:
                self._logger.error(
                    "system_metrics_provider_failed",
                    console_message="[SYSTEM_METRICS] collection timed out",
                    provider="timeout",
                    safe_error_code="collection_timeout",
                )
            return SystemMetricsSnapshot(
                sampled_at_utc=_utc_now_iso(),
                computer_name=computer_name,
                stale=True,
                cpu=_UNAVAILABLE_CPU,
                ram=_UNAVAILABLE_RAM,
                gpu=_UNAVAILABLE_GPU,
                providers={},
                safe_errors=["collection_timeout"],
            )
        # asyncio.CancelledError intentionally NOT caught here — cancellation
        # must propagate to the caller, never be swallowed as a normal result.

    def _collect_sync(self, computer_name: str) -> SystemMetricsSnapshot:
        start = time.monotonic()
        providers_used: dict[str, str] = {}
        safe_errors: list[str] = []

        try:
            cpu = self._cpu_ram_provider.probe_cpu()
            ram = self._cpu_ram_provider.probe_ram()
            providers_used["cpu_ram"] = getattr(self._cpu_ram_provider, "name", "psutil")
        except Exception:  # defensive only — CpuRamProvider itself never raises
            cpu, ram = _UNAVAILABLE_CPU, _UNAVAILABLE_RAM
            safe_errors.append("cpu_ram: probe_exception")
            if self._logger:
                self._logger.error(
                    "system_metrics_provider_failed",
                    console_message="[SYSTEM_METRICS] cpu_ram provider raised unexpectedly",
                    provider="cpu_ram",
                    safe_error_code="probe_exception",
                )

        gpu, gpu_provider_name, gpu_errors = self._select_gpu()
        safe_errors.extend(gpu_errors)
        providers_used["gpu"] = gpu_provider_name

        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        if self._logger:
            self._logger.event(
                "system_metrics_sampled",
                cpu_available=cpu.available,
                ram_available=ram.available,
                gpu_available=gpu.available,
                gpu_provider=gpu_provider_name,
                elapsed_ms=elapsed_ms,
                console_message=f"[SYSTEM_METRICS] sampled in {elapsed_ms}ms (gpu via {gpu_provider_name})",
            )

        return SystemMetricsSnapshot(
            sampled_at_utc=_utc_now_iso(),
            computer_name=computer_name,
            stale=False,
            cpu=cpu,
            ram=ram,
            gpu=gpu,
            providers=providers_used,
            safe_errors=safe_errors,
        )

    def _select_gpu(self) -> tuple[GpuMetrics, str, list[str]]:
        safe_errors: list[str] = []
        for provider in self._gpu_providers:
            try:
                result = provider.probe()
            except Exception:
                safe_errors.append(f"{provider.name}: probe_exception")
                if self._logger:
                    self._logger.error(
                        "system_metrics_provider_failed",
                        console_message=f"[SYSTEM_METRICS] {provider.name} provider raised unexpectedly",
                        provider=provider.name,
                        safe_error_code="probe_exception",
                    )
                continue

            if result.candidates:
                winner = pick_active_gpu(result.candidates)
                if winner is not None:
                    if self._logger:
                        self._logger.event(
                            "system_metrics_provider_selected",
                            provider=provider.name,
                            vendor=winner.metrics.vendor,
                            console_message=f"[SYSTEM_METRICS] GPU provider selected: {provider.name} ({winner.metrics.vendor})",
                        )
                    return winner.metrics, provider.name, safe_errors

            if result.reason:
                safe_errors.append(f"{provider.name}: {result.reason}")
                if self._logger:
                    self._logger.event(
                        "system_metrics_provider_failed",
                        provider=provider.name,
                        safe_error_code="no_candidates",
                        console_message=f"[SYSTEM_METRICS] {provider.name} found no GPU: {result.reason}",
                    )

        return (
            GpuMetrics(available=False, availability_reason="No GPU provider could detect a GPU on this system"),
            "none",
            safe_errors,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return platform.node() or "unknown"
