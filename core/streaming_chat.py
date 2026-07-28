"""Helpers shared by Siena's end-to-end streaming chat path."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from core.errors import SienaTimeoutError

CONTINUATION_INSTRUCTION = """Предыдущий ответ был остановлен лимитом длины.
Продолжи строго с места остановки.
Не повторяй уже выданный текст.
Не начинай ответ заново.
Не добавляй новое вступление.
Если внутри незакрытого блока кода — продолжай непосредственно код.
Закрой все открытые конструкции и Markdown fence только в конце файла.
После внутреннего анализа обязательно выдай непустой message.content.
Первый символ content должен быть непосредственным продолжением последнего символа файла."""


def open_fence_language(content: str) -> str | None:
    """Return the language of the last unmatched Markdown fence."""
    opened: tuple[str, int, str] | None = None
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})([^\n]*)$", line)
        if not match:
            continue
        fence, info = match.groups()
        if opened is None:
            opened = (fence[0], len(fence), info.strip().split(maxsplit=1)[0].lower())
        elif fence[0] == opened[0] and len(fence) >= opened[1] and not info.strip():
            opened = None
    return opened[2] if opened else None


def has_incomplete_structure(content: str) -> bool:
    lowered = content.lower()
    if open_fence_language(content) is not None:
        return True
    if "<!doctype html" in lowered and "</html>" not in lowered:
        return True
    if "<script" in lowered and "</script>" not in lowered:
        return True
    stripped = content.rstrip()
    if stripped.startswith(("{", "[")):
        expected = "}" if stripped.startswith("{") else "]"
        if not stripped.endswith(expected):
            return True
    if stripped.startswith("<?xml") and not re.search(r"</[^>]+>\s*$", stripped):
        return True
    return False


def response_missing_requested_structure(request: str, content: str) -> bool:
    """Check explicit structural and physical-line requirements from a code request."""
    request_lower = request.lower()
    content_lower = content.lower()
    line_match = re.search(
        r"(?:не\s+менее|минимум|at\s+least)\s+(\d+)\s+(?:физических\s+)?(?:строк|lines)",
        request_lower,
    )
    if line_match and len(content.splitlines()) < int(line_match.group(1)):
        return True
    asks_for_html_document = "html" in request_lower and any(
        marker in request_lower
        for marker in ("html-файл", "html файл", "html document", "полный html", "автономный")
    )
    if asks_for_html_document and not all(
        marker in content_lower for marker in ("<html", "</html>", "<body", "</body>")
    ):
        return True
    asks_for_fence = "fenced" in request_lower or "markdown fence" in request_lower or "```" in request
    if asks_for_fence and open_fence_language(content) is not None:
        return True
    if asks_for_fence and not re.search(r"(?m)^ {0,3}`{3,}", content):
        return True
    return False

def has_incomplete_html_document(content: str) -> bool:
    """Detect a full HTML document whose structural closing tags are still missing."""
    lowered = content.lower()
    if "<!doctype html" not in lowered and not re.search(r"<html(?:\s|>)", lowered):
        return False
    for tag in ("style", "head", "body", "script", "html"):
        openings = len(re.findall(rf"<{tag}(?:\s|>)", lowered))
        closings = lowered.count(f"</{tag}>")
        if openings > closings:
            return True
    return False


class PrematureHtmlFenceFilter:
    """Suppress premature HTML fences and normalize a valid trailing fence to its own line."""

    def __init__(self, context: str = "") -> None:
        self._context = context
        self._pending_ticks = ""
        self.suppressed = 0
        self.normalized = 0

    def _resolve_ticks(self, *, at_end: bool) -> str:
        ticks = self._pending_ticks
        self._pending_ticks = ""
        if len(ticks) < 3:
            return ticks
        line_start = not self._context.rsplit("\n", 1)[-1].strip()
        incomplete_html = has_incomplete_html_document(self._context)
        if line_start and incomplete_html:
            self.suppressed += 1
            return ""
        if at_end and not incomplete_html and not line_start:
            self.normalized += 1
            return "\n" + ticks
        return ticks

    def feed(self, delta: str) -> str:
        emitted: list[str] = []
        for char in delta:
            if char == "`":
                self._pending_ticks += char
                continue
            if self._pending_ticks:
                resolved = self._resolve_ticks(at_end=False)
                emitted.append(resolved)
                self._context += resolved
            emitted.append(char)
            self._context += char
        return "".join(emitted)

    def flush(self) -> str:
        result = self._resolve_ticks(at_end=True)
        self._context += result
        return result

def _strip_continuation_preamble(incoming: str, language: str | None) -> str:
    """Remove only known continuation wrappers without touching code indentation."""
    cleaned = re.sub(
        r"^\s*(?:Продолжаю(?:\s+с\s+места\s+остановки)?|Continuation)\s*:?\s*(?:\r?\n)?",
        "",
        incoming,
        count=1,
        flags=re.IGNORECASE,
    )
    if language is not None:
        escaped = re.escape(language)
        # A continuation inside an open block must not open a nested duplicate fence.
        cleaned = re.sub(
            rf"^\s*```(?:{escaped})?[^\n]*\r?\n",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
        # Also collapse a premature close+reopen pair at the segment seam.
        cleaned = re.sub(
            rf"^\s*```\s*\r?\n\s*```(?:{escaped})?[^\n]*\r?\n",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
    return cleaned


def seam_merge(existing: str, incoming: str, window_chars: int = 4000) -> tuple[str, int]:
    """Return only the non-overlapping continuation and removed overlap size."""
    cleaned = _strip_continuation_preamble(incoming, open_fence_language(existing))
    old_window = existing[-max(0, window_chars):]
    new_window = cleaned[:max(0, window_chars)]
    maximum = min(len(old_window), len(new_window))
    overlap = 0
    for size in range(maximum, 0, -1):
        if old_window[-size:] == new_window[:size]:
            overlap = size
            break
    return cleaned[overlap:], overlap


async def with_stream_timeouts(
    source: AsyncIterator[dict[str, Any]],
    *,
    first_token_timeout: float,
    idle_timeout: float,
    hard_deadline: float,
) -> AsyncIterator[dict[str, Any]]:
    """Apply first-token, per-chunk idle, and hard-total deadlines."""
    iterator = source.__aiter__()
    loop = asyncio.get_running_loop()
    first = True
    while True:
        remaining = hard_deadline - loop.time()
        if remaining <= 0:
            raise SienaTimeoutError("Ollama hard total generation timeout")
        timeout = min(first_token_timeout if first else idle_timeout, remaining)
        try:
            chunk = await asyncio.wait_for(anext(iterator), timeout=timeout)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            kind = "first token" if first else "stream idle"
            raise SienaTimeoutError(f"Ollama {kind} timeout after {timeout:.1f}s") from exc
        first = False
        yield chunk


def continuation_messages(
    base_messages: list[dict[str, Any]],
    accumulated: str,
    *,
    num_ctx: int,
) -> list[dict[str, Any]]:
    """Build a context-safe continuation request, retaining full output when possible."""
    line_count = len(accumulated.splitlines())
    progress_instruction = (
        CONTINUATION_INSTRUCTION
        + f"\nВ уже накопленном файле {line_count} физических строк. "
        + "Не добавляй новые возможности сверх исходного запроса. "
        + "Если требуемый минимальный объём уже достигнут, немедленно закончи текущую конструкцию "
        + "и выдай только минимально необходимые закрывающие части style/body/script/html/fence."
    )
    full = [
        *base_messages,
        {"role": "assistant", "content": accumulated},
        {"role": "user", "content": progress_instruction},
    ]
    estimated = sum(len(str(item.get("content") or "")) for item in full) // 2
    if estimated + 512 < num_ctx:
        return full

    system = base_messages[0] if base_messages and base_messages[0].get("role") == "system" else None
    original_user = next((item for item in reversed(base_messages) if item.get("role") == "user"), None)
    fixed = [item for item in (system, original_user) if item is not None]
    fixed_chars = sum(len(str(item.get("content") or "")) for item in fixed)
    available_chars = max(1000, (num_ctx - 1024) * 2 - fixed_chars - len(progress_instruction))
    tail = accumulated[-available_chars:]
    language = open_fence_language(accumulated)
    context_note = (
        f"Открытый Markdown-язык: {language or 'нет'}.\n"
        "Ниже последний полный доступный фрагмент предыдущего ответа:\n"
    )
    return [
        *fixed,
        {"role": "assistant", "content": context_note + tail},
        {"role": "user", "content": progress_instruction},
    ]
