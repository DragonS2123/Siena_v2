"""Типы сообщений и результата инструмента. Формат совместим с wire-форматом Ollama chat API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


def _is_search_result_shaped(content: Any) -> bool:
    """True for a list of dicts carrying title/url-style fields (web_search's
    shape) — content that benefits from a labelled, per-source breakdown
    instead of a flat JSON blob, so the model can accurately ground specific
    claims in a title/domain/snippet/date instead of guessing at structure."""
    return (
        isinstance(content, list)
        and len(content) > 0
        and all(isinstance(item, dict) and ("title" in item or "url" in item) for item in content)
    )


def _format_search_results(content: list[dict], name: str | None, args: dict | None, timestamp: str) -> str:
    query = (args or {}).get("query", "n/a")
    lines = [
        f"Tool: {name or 'unknown'}",
        f"Query: {query}",
        f"Retrieved at: {timestamp}",
        f"Results ({len(content)}):",
    ]
    for i, item in enumerate(content, start=1):
        title = item.get("title") or "(no title)"
        url = item.get("url") or ""
        domain = urlparse(url).netloc if url else "unknown source"
        date = item.get("date") or item.get("published") or item.get("published_date")
        snippet = item.get("snippet") or ""
        lines.append(f"{i}. {title} — {domain}")
        if date:
            lines.append(f"   Date: {date}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")
        if url:
            lines.append(f"   URL: {url}")
    return "\n".join(lines)


@dataclass
class ToolResult:
    ok: bool
    content: Any = None
    error: str | None = None

    def to_message_content(self, name: str | None = None, args: dict | None = None) -> str:
        """Сериализация результата для отправки модели как содержимое tool-сообщения.

        name/args/timestamp — не решение о том, что "важно" (это по-прежнему
        решает модель), а техническая маркировка источника, чтобы модель могла
        точно сослаться на то, ЧТО именно вернул инструмент и КОГДА, вместо
        того чтобы додумывать детали за пределами результата (см. SYSTEM_PROMPT,
        раздел про research discipline)."""
        timestamp = datetime.now().astimezone().isoformat()
        if not self.ok:
            payload = {"ok": False, "tool": name, "query": (args or {}).get("query"), "timestamp": timestamp, "error": self.error}
            return json.dumps(payload, ensure_ascii=False)

        if _is_search_result_shaped(self.content):
            return _format_search_results(self.content, name, args, timestamp)

        payload = {"ok": True, "tool": name, "timestamp": timestamp, "result": self.content}
        return json.dumps(payload, ensure_ascii=False)


def system_message(content: str) -> dict:
    return {"role": "system", "content": content}


def user_message(content: str) -> dict:
    return {"role": "user", "content": content}


def tool_message(name: str, result: ToolResult, args: dict | None = None) -> dict:
    # Поле называется "tool_name", а не "name" — это единственное имя, которое
    # Ollama Message schema реально знает для tool-сообщений (проверено против
    # ollama._types.Message: "name" молча отбрасывается при валидации).
    return {"role": "tool", "tool_name": name, "content": result.to_message_content(name=name, args=args)}
