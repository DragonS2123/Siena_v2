"""Computer-context injection decision + compact hidden context builder
(0.2.3 Phase 1).

Same regex-intent discipline as core/image_intent.py and
game/nucleares_context.py: a technical pattern match on the user's own
words, not a semantic judgment. The context block is injected ONLY when the
message explicitly asks about the computer / Siena's own runtime — a plain
"привет", a translation request, or a code question never gets it (see
tests/test_computer_awareness.py for the exact positive/negative fixtures).

Privacy in the model-visible block:
- active_window_title is included only when allow_active_window_title=true
  (default false — titles can carry personal info);
- the process list is included only when allow_process_list=true, and even
  then it's the curated Siena-runtime allowlist from computer_service.py,
  never a full system process dump;
- disks appear only when allow_disk_status=true, and only mountpoint +
  free-space numbers.
"""

from __future__ import annotations

import re

from computer.computer_service import ComputerSettings
from computer.computer_state import ComputerState

_GB = 1024**3

# Deliberately narrow (RU/EN). Each pattern targets an explicit question
# about the machine or Siena's runtime — see module docstring.
_COMPUTER_CONTEXT_PATTERNS = [
    # "что (сейчас) с компьютером/компом/ПК/системой/ресурсами/backend'ом"
    r"(что|как)\b.{0,40}\bс\s+(компьютер|компом|комп\b|пк\b|систем|ресурс|железом|backend|бэкенд|бекенд)",
    r"(состояние|статус)\s+(компьютер|систем|пк|ресурс|backend|бэкенд)",
    # perf complaints
    r"почему\s+(вс[её]|комп|компьютер|пк|система|siena|сиена)?\s*(так\s+)?(тормоз|лага|висн|фриз|медленн)",
    r"(что|кто)\s+(жр[её]т|съедает|занимает|грузит)\s+(памят|оперативк|ram|cpu|процессор|ресурс|видеопамят|vram)",
    r"(что|что-то)\s+(сейчас\s+)?перегру",
    # memory/VRAM usage questions
    r"сколько\s+(занято|свободно|осталось)?\s*(ram|vram|памят|оперативк|видеопамят)",
    r"(ram|vram|память|оперативк|видеопамят)\w*\s+(занят|свободн|исполь|заполнен)",
    # Siena's own runtime not working
    r"почему\s+(ты|siena|сиена)\s+не\s+(отвеча|говор|слыш|слуша|работа)",
    r"почему\s+не\s+работа\w*\s+(голос|звук|озвучк|микрофон|tts|stt|распознаван)",
    r"почему\s+(голос|звук|озвучк|микрофон|tts|stt)\s+не\s+работа",
    r"(tts|stt|голос|микрофон|озвучк)\w*\s+(не\s+работа|отвал|слома|выключ)",
    # runtime services
    r"(почему|что)\b.{0,30}\b(ollama|оллама)\b.{0,30}(не\s+(отвеча|работа)|offline|упал)",
    r"(работает|запущен|жив)\s*(ли)?\s+(backend|бэкенд|бекенд|ollama|оллама|tts|stt)",
    r"(есть|какие)\s*(ли)?\s+.{0,20}предупрежден",
    # EN
    r"what('s| is)?\b.{0,30}\b(with|about)?\s*(the\s+)?(computer|system|pc|machine|resources)\b",
    r"why\b.{0,30}\b(slow|lagg|freez|frozen)",
    r"how much\s+(ram|vram|memory)",
    r"(is|why)\b.{0,20}\b(ollama|backend|tts|stt)\b.{0,30}(offline|down|not (respond|work)|running)",
    r"why\b.{0,20}\b(voice|mic|microphone|audio|speech)\b.{0,20}(not work|broken|off)",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _COMPUTER_CONTEXT_PATTERNS]


def wants_computer_context(text: str) -> bool:
    lowered = text.lower()
    return any(p.search(lowered) for p in _COMPILED)


def _gb(value_bytes: int | None) -> str:
    if value_bytes is None:
        return "?"
    return f"{value_bytes / _GB:.1f}"


def build_computer_context(state: ComputerState, settings: ComputerSettings) -> str:
    """Compact, model-visible snapshot. Kept to a dozen short lines on
    purpose — this is orientation for an answer, not a telemetry dump."""
    lines: list[str] = ["[COMPUTER_CONTEXT]", "Computer awareness: enabled (read-only)"]

    lines.append(f"CPU: {state.cpu_percent}%" if state.cpu_percent is not None else "CPU: unavailable")
    if state.ram_percent is not None:
        lines.append(f"RAM: {_gb(state.ram_used_bytes)} / {_gb(state.ram_total_bytes)} GB, {state.ram_percent}%")
    else:
        lines.append("RAM: unavailable")
    if state.vram_percent is not None:
        lines.append(f"VRAM: {_gb(state.vram_used_bytes)} / {_gb(state.vram_total_bytes)} GB, {state.vram_percent}%")
    else:
        lines.append("VRAM: unavailable" + (f" ({state.vram_unavailable_reason})" if state.vram_unavailable_reason else ""))

    if settings.allow_disk_status and state.disks:
        low = [d for d in state.disks if d["free_gb"] < settings.warning_disk_free_gb]
        shown = low if low else state.disks[:3]
        lines.append("Disks: " + "; ".join(f"{d['mountpoint']} {d['free_gb']} GB free" for d in shown))

    lines.append(f"Backend: {state.backend_status}")
    lines.append(f"Ollama: {'online' if state.ollama_status.get('connected') else 'offline'}")
    lines.append(f"TTS: {state.tts_status.get('status', 'unknown')}")
    stt_available = state.stt_status.get("available")
    lines.append(f"STT: {'available' if stt_available else 'unavailable' if stt_available is False else 'unknown'}")

    if settings.allow_process_list and state.important_processes:
        parts = [f"{p['name']} ({p['ram_mb']} MB)" for p in state.important_processes if p.get("ram_mb")]
        if parts:
            lines.append("Siena runtime processes: " + ", ".join(parts[:6]))

    if settings.allow_active_window_title and state.active_window_title:
        lines.append(f"Active window: {state.active_window_title}")

    if state.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {w.message}" for w in state.warnings)
    else:
        lines.append("Warnings: none")

    lines.append(
        "Answer from this data only; do not invent numbers. You cannot run commands, "
        "close processes, or change anything on this computer — you can only read and explain."
    )
    lines.append("[/COMPUTER_CONTEXT]")
    return "\n".join(lines)
