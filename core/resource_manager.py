"""Resource/Model Lifecycle — Phase 1: honest visibility + safe manual
controls only (HANDOFF_v2.md). No automatic keep_alive/TTL policy lives
here — that is a deliberately separate, not-yet-done future pass (Phase 2).

Three genuinely different things get lumped together as "what's using
RAM/VRAM right now", and this module keeps them cleanly separate instead of
pretending they're one system:

1. Ollama-managed models (chat/tool models) — Ollama's own process owns
   loading/unloading; we only ever ask it nicely (`GET /api/ps`,
   `POST /api/generate` with `keep_alive`). We never manage an Ollama
   subprocess ourselves.
2. The external qwen3_tts_ggml_vulkan `tts-server.exe` — a real subprocess
   `QwenTTSGgmlVulkanProvider` starts itself when the human clicks
   Speak/Stream (or on backend boot if configured to stay warm). The known
   bug this module exists to fix: `provider._process` is `None` again after
   a backend restart/reload even though the *actual* external process may
   still be alive on port 8080 — `is_server_managed_by_us()` alone cannot
   see that. `find_tts_server_processes()` below finds it independently via
   the OS process list.
3. whisper.cpp's `whisper-cli.exe` — spawned per-transcription via a
   blocking `subprocess.run(...)` (see `voice/whisper_cpp_stt.py`) and
   normally exits the instant that call returns. It should essentially
   never show up as "running" outside of a transcription actually in
   progress; if it does, that's a sign something is stuck, not something
   this module tries to fix automatically.

Same discipline as core/system_metrics.py throughout: every public function
here is safe to call unconditionally and never raises — a missing psutil
process, an unreachable Ollama, or a permissions error all degrade to an
honest empty/False result plus an error string, never a crash or a
fabricated number.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import psutil
import requests


def ollama_process_status(host: str) -> dict[str, Any]:
    """`GET /api/ps` — Ollama's own view of which models it currently has
    loaded (distinct from `/api/tags`, which lists every *installed* model
    whether loaded or not). Never raises."""
    try:
        response = requests.get(f"{host}/api/ps", timeout=2)
        response.raise_for_status()
    except Exception as exc:
        return {"available": False, "models": [], "error": str(exc)}

    try:
        raw_models = response.json().get("models", [])
    except ValueError as exc:
        return {"available": False, "models": [], "error": f"malformed /api/ps response: {exc}"}

    models = []
    for m in raw_models:
        size = m.get("size") or 0
        size_vram = m.get("size_vram") or 0
        models.append({
            "name": m.get("name"),
            "model": m.get("model"),
            "size_bytes": size,
            "size_vram_bytes": size_vram,
            "processor": _processor_label(size, size_vram),
            "context_length": m.get("context_length"),
            "expires_at": m.get("expires_at"),
            "digest": m.get("digest"),
        })
    return {"available": True, "models": models, "error": None}


def _processor_label(size: int, size_vram: int) -> str:
    """Mirrors `ollama ps`'s own PROCESSOR column — a rough GPU/CPU split
    estimate from comparing total size against VRAM-resident size. Not a
    real VRAM meter (see core/system_metrics.py's own AMD-VRAM caveat) —
    this is just relaying what Ollama itself already reports about its own
    loaded models, never a guess derived from process RSS or similar."""
    if size <= 0:
        return "unknown"
    if size_vram <= 0:
        return "100% CPU"
    if size_vram >= size:
        return "100% GPU"
    pct_gpu = round(size_vram / size * 100)
    return f"{100 - pct_gpu}%/{pct_gpu}% CPU/GPU"


def _find_processes_by_exe_name(exe_name: str) -> list[dict[str, Any]]:
    """Cross-platform-safe process scan by executable filename (not full
    path) — psutil.process_iter can raise per-process (process exits mid-
    iteration, permission denied) which is expected and skipped rather than
    aborting the whole scan."""
    found: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            if name.lower() != exe_name.lower():
                continue
            found.append({"pid": info.get("pid"), "exe": info.get("exe")})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def _port_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tts_server_status(
    *,
    managed_by_backend: bool,
    expected_exe_path: Path,
    host: str,
    port: int,
) -> dict[str, Any]:
    """Combines what the backend's own provider handle knows
    (`managed_by_backend`, from `QwenTTSGgmlVulkanProvider.is_server_managed_by_us()`)
    with an independent OS-level process scan — the whole point being that
    these two can legitimately disagree (a backend restart loses the handle
    but the external process is still alive), and callers need to see both,
    not just whichever one happens to be convenient."""
    processes = _find_processes_by_exe_name("tts-server.exe")
    expected_str = str(expected_exe_path.resolve()) if expected_exe_path.exists() else str(expected_exe_path)
    expected_path_match = None
    matching_pid = None
    if processes:
        expected_path_match = False
        for p in processes:
            exe = p.get("exe")
            if exe and Path(exe).resolve() == expected_exe_path.resolve():
                expected_path_match = True
                matching_pid = p["pid"]
                break
        if matching_pid is None:
            matching_pid = processes[0]["pid"]

    return {
        "running": bool(processes) or _port_reachable(host, port),
        "managed_by_backend": managed_by_backend,
        "pid": matching_pid,
        "path": expected_str,
        "port_reachable": _port_reachable(host, port),
        "expected_path_match": expected_path_match,
        "process_count": len(processes),
    }


def find_tts_server_processes(expected_exe_path: Path) -> list[dict[str, Any]]:
    """Used by the force-stop path — returns every OS process named
    tts-server.exe with its pid/exe path, so the caller can kill only the
    ones whose exe path actually matches `expected_exe_path` (never an
    arbitrary same-named process elsewhere on the machine)."""
    return _find_processes_by_exe_name("tts-server.exe")


def resolve_unload_targets(
    target: str,
    model: str | None,
    *,
    known_models: set[str],
    tool_model_names: list[str],
    manual_heavy_model: str,
    active_chat_model: str,
) -> list[str]:
    """Pure resolution logic for POST /api/models/lifecycle/unload — kept
    here (not inline in api/server.py) specifically so it's unit-testable
    without importing the whole FastAPI app, which has heavy import-time
    side effects (constructs the Ollama client, loads persisted settings,
    builds the tool registry, ...) not designed for test isolation, unlike
    every other test in tests/.

    Raises ValueError for anything the caller should turn into an HTTP 400.
    `tool_models`/`all_non_chat` silently exclude `active_chat_model` no
    matter what — the only way to unload the currently active chat model is
    `target="specific"` naming it explicitly, mirroring
    POST /api/models/active's own "only ever an explicit human action"
    discipline elsewhere in this project."""
    if target not in ("tool_models", "all_non_chat", "specific"):
        raise ValueError(f"target must be one of: tool_models, all_non_chat, specific (got {target!r})")

    if target == "specific":
        if not model:
            raise ValueError("model is required when target=specific")
        model = model.strip()
        if model not in known_models:
            raise ValueError(f"model {model!r} is not a known model (see config.MODEL_REGISTRY)")
        return [model]

    candidates = list(tool_model_names) if target == "tool_models" else [*tool_model_names, manual_heavy_model]

    seen: set[str] = set()
    result: list[str] = []
    for m in candidates:
        if m == active_chat_model or m in seen:
            continue
        seen.add(m)
        result.append(m)
    return result


def whisper_cli_status(_expected_exe_path: Path) -> dict[str, Any]:
    """whisper-cli.exe is spawned per-transcription and normally exits the
    moment that call returns (see voice/whisper_cpp_stt.py) — this is
    visibility only (Phase 1 scope explicitly excludes any control action
    for it). Any result here should normally be empty; a non-empty one is a
    signal something is stuck, not something this function fixes."""
    processes = _find_processes_by_exe_name("whisper-cli.exe")
    return {
        "running": bool(processes),
        "pids": [p["pid"] for p in processes],
        "note": "whisper-cli.exe is normally ephemeral (spawned per transcription) — a lingering process here may indicate a stuck transcription.",
    }
