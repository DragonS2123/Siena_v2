"""open_url(url) — получить и прочитать содержимое страницы по ссылке.

Runtime не решает, какую ссылку открывать и зачем — это решила модель. Ошибки
сети/страницы возвращаются модели как восстановимая ошибка инструмента.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from core.errors import SienaToolError
from core.message import ToolResult
from tools.base import Tool

_ALLOWED_SCHEMES = ("http://", "https://")


class OpenUrlTool(Tool):
    name = "open_url"
    description = "Открыть веб-страницу по URL и вернуть её текстовое содержимое."
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Полный URL страницы (http/https)"}},
        "required": ["url"],
    }

    def __init__(self, timeout: int, max_chars: int):
        self._timeout = timeout
        self._max_chars = max_chars

    def run(self, url: str) -> ToolResult:
        if not url.startswith(_ALLOWED_SCHEMES):
            raise SienaToolError(f"Некорректный URL (ожидается http/https): {url!r}")

        try:
            response = requests.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": "Siena/2.0 (+local-agent-runtime)"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SienaToolError(f"Не удалось открыть URL {url!r}: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = " ".join(soup.get_text(separator=" ").split())
        truncated = text[: self._max_chars]

        return ToolResult(
            ok=True,
            content={"url": url, "text": truncated, "truncated": len(text) > self._max_chars},
        )
