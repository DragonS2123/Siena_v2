"""System resource metrics for the Runtime view (CPU/RAM always, VRAM
best-effort). Purely diagnostic — Runtime doesn't decide anything based on
these numbers, they only inform the human via GET /api/runtime/status.

CPU/RAM use psutil (cross-platform, in-process, stable — safe to call
directly on every poll). VRAM has no equally reliable cross-vendor
equivalent on Windows: NVIDIA exposes it cleanly via nvidia-smi; AMD does
not, through any simple/safe API. Win32_VideoController.AdapterRAM (WMI) is
a known-unreliable 32-bit field — verified directly on the AMD RX 7900 XTX
this backend runs on during development of this module: WMI reports
AdapterRAM = 4293918720 bytes (~4.0 GB) for a card that actually has 24 GB,
a well-documented truncation bug. A correct reading would require the vendor
SDK (AMD ADL) via raw ctypes/COM calls, which risks crashing the whole
backend process on any struct/ABI mismatch — not an acceptable trade-off for
a "nice to have" Runtime meter. So: try nvidia-smi (safe subprocess, short
timeout, isolated from this process), and if that doesn't work, report
vram_supported=False with an honest reason instead of showing a wrong or
half-known number.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import psutil

_GB = 1024**3

# Primes psutil's internal CPU-time reference point at import time so the
# first real call from cpu_ram_metrics() already returns a meaningful delta
# instead of psutil's documented meaningless "0.0 on first call" value.
psutil.cpu_percent(interval=None)

# Cached across calls so a system without nvidia-smi doesn't pay a failed
# subprocess spawn on every single /api/runtime/status poll (every 5s from
# RuntimeStatusProvider) forever. False = not probed yet; None = probed, not found.
_nvidia_smi_path: str | None | bool = False


def _find_nvidia_smi() -> str | None:
    global _nvidia_smi_path
    if _nvidia_smi_path is False:
        _nvidia_smi_path = shutil.which("nvidia-smi")
    return _nvidia_smi_path


def cpu_ram_metrics() -> dict[str, Any]:
    """Never raises. psutil is a stable, well-tested dependency, but any
    unexpected platform quirk still falls back to clearly-null fields rather
    than crashing the whole /api/runtime/status response."""
    try:
        mem = psutil.virtual_memory()
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "ram_total_gb": round(mem.total / _GB, 2),
            "ram_used_gb": round(mem.used / _GB, 2),
            "ram_available_gb": round(mem.available / _GB, 2),
            "ram_percent": round(mem.percent, 1),
        }
    except Exception as exc:  # defensive only — psutil is stable in practice
        return {
            "cpu_percent": None,
            "ram_total_gb": None,
            "ram_used_gb": None,
            "ram_available_gb": None,
            "ram_percent": None,
            "cpu_ram_error": str(exc),
        }


def _vram_unsupported(reason: str) -> dict[str, Any]:
    return {
        "vram_supported": False,
        "vram_reason": reason,
        "vram_total_gb": None,
        "vram_used_gb": None,
        "vram_percent": None,
    }


def vram_metrics() -> dict[str, Any]:
    """Best-effort VRAM via nvidia-smi only (see module docstring for why
    AMD is intentionally not attempted through WMI/ADL). Any failure —
    missing binary, no driver, timeout, malformed output — is reported as
    vram_supported=False with a human-readable reason, never fabricated
    numbers."""
    exe = _find_nvidia_smi()
    if not exe:
        return _vram_unsupported("nvidia-smi not found on PATH — no NVIDIA GPU detected")

    try:
        result = subprocess.run(
            [exe, "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _vram_unsupported(f"nvidia-smi call failed: {exc}")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        first_line = detail.splitlines()[0] if detail else "unknown error"
        return _vram_unsupported(
            f"nvidia-smi reported an error (GPU is likely non-NVIDIA or the driver is unreachable): {first_line}"
        )

    try:
        first_line = result.stdout.strip().splitlines()[0]
        total_mib_str, used_mib_str = (part.strip() for part in first_line.split(","))
        total_mib, used_mib = float(total_mib_str), float(used_mib_str)
    except (IndexError, ValueError) as exc:
        return _vram_unsupported(f"nvidia-smi output could not be parsed: {exc}")

    total_gb = total_mib / 1024
    used_gb = used_mib / 1024
    return {
        "vram_supported": True,
        "vram_reason": None,
        "vram_total_gb": round(total_gb, 2),
        "vram_used_gb": round(used_gb, 2),
        "vram_percent": round((used_gb / total_gb) * 100, 1) if total_gb else None,
    }
