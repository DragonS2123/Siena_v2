"""Shape of the read-only computer state — plain data, no collection logic
(that lives in computer_service.py). Every optional field is None (or an
empty list) when its metric is unavailable or its privacy gate is off —
never a fabricated value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ComputerWarning:
    code: str  # high_cpu | high_ram | high_vram | low_disk_space | ollama_offline |
    #            tts_offline | stt_unavailable | backend_error | network_unavailable |
    #            metrics_unavailable
    message: str  # short, human-readable RU line (frontend localizes via `code`)
    value: Any = None  # the number that tripped the threshold, when there is one

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "value": self.value}


@dataclass(frozen=True)
class ComputerState:
    os_name: str
    os_version: str
    hostname: str
    uptime_seconds: int | None
    cpu_percent: float | None
    ram_total_bytes: int | None
    ram_used_bytes: int | None
    ram_percent: float | None
    gpu_name: str | None
    vram_total_bytes: int | None
    vram_used_bytes: int | None
    vram_percent: float | None
    vram_unavailable_reason: str | None
    # None = collection gated off by its allow_* setting; [] = allowed but
    # nothing found / not applicable.
    disks: list[dict[str, Any]] | None
    network_available: bool | None
    backend_status: str  # "online" by construction — this process built the response
    ollama_status: dict[str, Any]
    tts_status: dict[str, Any]
    stt_status: dict[str, Any]
    active_window_title: str | None  # None unless allow_active_window_title=true AND readable
    important_processes: list[dict[str, Any]] | None
    warnings: list[ComputerWarning] = field(default_factory=list)
    collected_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "os_name": self.os_name,
            "os_version": self.os_version,
            "hostname": self.hostname,
            "uptime_seconds": self.uptime_seconds,
            "cpu_percent": self.cpu_percent,
            "ram_total_bytes": self.ram_total_bytes,
            "ram_used_bytes": self.ram_used_bytes,
            "ram_percent": self.ram_percent,
            "gpu_name": self.gpu_name,
            "vram_total_bytes": self.vram_total_bytes,
            "vram_used_bytes": self.vram_used_bytes,
            "vram_percent": self.vram_percent,
            "vram_unavailable_reason": self.vram_unavailable_reason,
            "disks": self.disks,
            "network_available": self.network_available,
            "backend_status": self.backend_status,
            "ollama_status": self.ollama_status,
            "tts_status": self.tts_status,
            "stt_status": self.stt_status,
            "active_window_title": self.active_window_title,
            "important_processes": self.important_processes,
            "warnings": [w.to_dict() for w in self.warnings],
            "collected_at": self.collected_at,
        }
