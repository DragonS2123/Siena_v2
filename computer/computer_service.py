"""Read-only computer state collection (0.2.3 Phase 1).

Reuses the metrics machinery that already exists instead of duplicating it:
CPU/RAM/VRAM come from core/system_metrics.py (including its deliberate
"AMD VRAM is honestly unavailable, never guessed" stance), and the runtime
service statuses (Ollama / TTS / STT) are injected as callables from
api/server.py so this module reuses the server's existing status helpers
without importing api.server (which has heavy import-time side effects).

Everything here is per-request and synchronous: no background threads, no
polling, no caching daemons. The only state kept between calls is the last
warning-code set, used purely so the caller can log
computer_warning_detected once per CHANGE instead of once per 10s poll.

No command execution: the sole subprocess in the entire collection path is
core/system_metrics.vram_metrics()'s pre-existing read-only nvidia-smi
query. Nothing in this module (or package) spawns, kills, writes, clicks,
or types anything.
"""

from __future__ import annotations

import platform
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import psutil

from computer.computer_state import ComputerState, ComputerWarning
from core.system_metrics import cpu_ram_metrics, vram_metrics

_GB = 1024**3

# The processes Siena's own runtime is made of — a curated allowlist, not a
# full system process dump (that would be noise AND a privacy leak). Scanned
# by exact executable name only.
_IMPORTANT_PROCESS_NAMES = {
    "ollama.exe": "ollama",
    "ollama app.exe": "ollama_app",
    "tts-server.exe": "tts_server",
    "whisper-cli.exe": "whisper_cli",
}


@dataclass(frozen=True)
class ComputerSettings:
    """Live config.* values bundled per call (same pattern as
    presence/presence_service.py::PresenceSettings) — read fresh by
    api/server.py at request time, so POST /api/settings changes apply
    immediately, no restart."""

    enabled: bool = True
    allow_active_window_title: bool = False  # off by default: titles can carry personal info
    allow_process_list: bool = True
    allow_disk_status: bool = True
    allow_network_status: bool = True
    warning_cpu_percent: int = 90
    warning_ram_percent: int = 85
    warning_vram_percent: int = 90
    warning_disk_free_gb: int = 10


def _read_active_window_title() -> str | None:
    """Foreground window title via user32 (Windows only) — a read-only
    GetWindowTextW call, no input control, no screenshots. Returns None on
    any failure or on non-Windows platforms."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or None
    except Exception:
        return None


class ComputerService:
    def __init__(
        self,
        ollama_status_provider: Callable[[], dict[str, Any]],
        tts_status_provider: Callable[[], dict[str, Any]],
        stt_status_provider: Callable[[], dict[str, Any]],
    ):
        self._ollama_status_provider = ollama_status_provider
        self._tts_status_provider = tts_status_provider
        self._stt_status_provider = stt_status_provider
        # Only for warning-change detection (trace anti-spam) — not a cache.
        self._last_warning_codes: frozenset[str] | None = None

    # ---- individual collectors (each fails soft, never raises) -----------

    def _provider_status(self, provider: Callable[[], dict[str, Any]], label: str) -> dict[str, Any]:
        try:
            return provider()
        except Exception as exc:
            return {"status": "unknown", "error": f"{label} status collection failed: {exc}"}

    def _collect_disks(self) -> list[dict[str, Any]]:
        disks: list[dict[str, Any]] = []
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            return disks
        for part in partitions:
            # Optical/unready drives raise OSError from disk_usage — skip
            # them instead of failing the whole collection.
            if "cdrom" in (part.opts or "").lower():
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({
                "mountpoint": part.mountpoint,
                "total_gb": round(usage.total / _GB, 1),
                "free_gb": round(usage.free / _GB, 1),
                "percent_used": round(usage.percent, 1),
            })
        return disks

    def _collect_important_processes(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        try:
            own_pid = psutil.Process().pid
            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    role = _IMPORTANT_PROCESS_NAMES.get(name)
                    mem = proc.info.get("memory_info")
                    if role is None and proc.info.get("pid") != own_pid:
                        continue
                    found.append({
                        "name": proc.info.get("name"),
                        "role": role or "siena_backend",
                        "pid": proc.info.get("pid"),
                        "ram_mb": round(mem.rss / (1024**2)) if mem else None,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            return found
        return found

    def _network_available(self) -> bool | None:
        """Purely local check — any non-loopback interface up. Deliberately
        makes no external requests (no ping, no DNS): "NIC is up" is the
        honest claim this can make without touching the network itself."""
        try:
            stats = psutil.net_if_stats()
            return any(s.isup for name, s in stats.items() if "loopback" not in name.lower() and not name.lower().startswith("lo"))
        except Exception:
            return None

    # ---- warnings ----------------------------------------------------------

    def _build_warnings(
        self,
        settings: ComputerSettings,
        cpu_percent: float | None,
        ram_percent: float | None,
        vram_percent: float | None,
        disks: list[dict[str, Any]] | None,
        network_available: bool | None,
        ollama_status: dict[str, Any],
        tts_status: dict[str, Any],
        stt_status: dict[str, Any],
    ) -> list[ComputerWarning]:
        warnings: list[ComputerWarning] = []
        if cpu_percent is None or ram_percent is None:
            warnings.append(ComputerWarning("metrics_unavailable", "Метрики CPU/RAM недоступны"))
        if cpu_percent is not None and cpu_percent >= settings.warning_cpu_percent:
            warnings.append(ComputerWarning("high_cpu", "Высокая загрузка CPU", cpu_percent))
        if ram_percent is not None and ram_percent >= settings.warning_ram_percent:
            warnings.append(ComputerWarning("high_ram", "Высокая загрузка RAM", ram_percent))
        if vram_percent is not None and vram_percent >= settings.warning_vram_percent:
            warnings.append(ComputerWarning("high_vram", "Высокая загрузка VRAM", vram_percent))
        if disks:
            for disk in disks:
                if disk["free_gb"] < settings.warning_disk_free_gb:
                    warnings.append(
                        ComputerWarning("low_disk_space", f"Мало места на диске {disk['mountpoint']}", disk["free_gb"])
                    )
        if not ollama_status.get("connected", False):
            warnings.append(ComputerWarning("ollama_offline", "Ollama не отвечает"))
        if tts_status.get("status") == "offline":
            warnings.append(ComputerWarning("tts_offline", "TTS выключен"))
        if stt_status.get("available") is False:
            warnings.append(ComputerWarning("stt_unavailable", "STT недоступен"))
        if network_available is False:
            warnings.append(ComputerWarning("network_unavailable", "Сеть недоступна"))
        return warnings

    # ---- main entry point ----------------------------------------------------

    def collect(self, settings: ComputerSettings) -> ComputerState:
        """One full read-only snapshot. Individual metric failures degrade to
        None/unavailable fields (plus a warning where meaningful) — this
        method itself never raises for a metric problem."""
        cpu_ram = cpu_ram_metrics()
        vram = vram_metrics()

        try:
            uptime_seconds: int | None = round(time.time() - psutil.boot_time())
        except Exception:
            uptime_seconds = None

        ram_total_gb = cpu_ram.get("ram_total_gb")
        ram_used_gb = cpu_ram.get("ram_used_gb")

        disks = self._collect_disks() if settings.allow_disk_status else None
        network_available = self._network_available() if settings.allow_network_status else None
        important_processes = self._collect_important_processes() if settings.allow_process_list else None
        active_window_title = _read_active_window_title() if settings.allow_active_window_title else None

        ollama_status = self._provider_status(self._ollama_status_provider, "ollama")
        tts_status = self._provider_status(self._tts_status_provider, "tts")
        stt_status = self._provider_status(self._stt_status_provider, "stt")

        vram_supported = vram.get("vram_supported", False)
        vram_total_gb = vram.get("vram_total_gb")
        vram_used_gb = vram.get("vram_used_gb")

        warnings = self._build_warnings(
            settings,
            cpu_percent=cpu_ram.get("cpu_percent"),
            ram_percent=cpu_ram.get("ram_percent"),
            vram_percent=vram.get("vram_percent") if vram_supported else None,
            disks=disks,
            network_available=network_available,
            ollama_status=ollama_status,
            tts_status=tts_status,
            stt_status=stt_status,
        )

        return ComputerState(
            os_name=platform.system(),
            os_version=platform.version(),
            hostname=socket.gethostname(),
            uptime_seconds=uptime_seconds,
            cpu_percent=cpu_ram.get("cpu_percent"),
            ram_total_bytes=round(ram_total_gb * _GB) if ram_total_gb is not None else None,
            ram_used_bytes=round(ram_used_gb * _GB) if ram_used_gb is not None else None,
            ram_percent=cpu_ram.get("ram_percent"),
            gpu_name=None,  # honest: the nvidia-smi query used doesn't return the name; AMD has no safe query at all
            vram_total_bytes=round(vram_total_gb * _GB) if vram_supported and vram_total_gb is not None else None,
            vram_used_bytes=round(vram_used_gb * _GB) if vram_supported and vram_used_gb is not None else None,
            vram_percent=vram.get("vram_percent") if vram_supported else None,
            vram_unavailable_reason=None if vram_supported else vram.get("vram_reason"),
            disks=disks,
            network_available=network_available,
            backend_status="online",
            ollama_status=ollama_status,
            tts_status=tts_status,
            stt_status=stt_status,
            active_window_title=active_window_title,
            important_processes=important_processes,
            warnings=warnings,
            collected_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def warnings_changed(self, warnings: list[ComputerWarning]) -> list[str]:
        """Returns the warning codes that are NEW compared to the previous
        collection (and updates the reference set) — used by api/server.py
        to fire computer_warning_detected once per change instead of once
        per poll."""
        codes = frozenset(w.code for w in warnings)
        previous = self._last_warning_codes
        self._last_warning_codes = codes
        if previous is None:
            return sorted(codes)
        return sorted(codes - previous)

    @staticmethod
    def summarize(state: ComputerState) -> dict[str, Any]:
        """Deterministic one-line human summary — the first (highest-listed)
        warning wins; no warnings means "everything is fine". RU text is
        canonical (like presence's pools); the frontend localizes via
        `code`."""
        if state.warnings:
            first = state.warnings[0]
            return {"summary": first.message, "code": first.code, "warning_count": len(state.warnings)}
        return {"summary": "Компьютер работает нормально", "code": "ok", "warning_count": 0}
