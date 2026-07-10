"""web_search(query) — поиск в интернете через DuckDuckGo (без API-ключа).

Runtime не решает, нужно ли искать — этот tool исполняется только когда модель
сама вызвала web_search. Любая неудача поиска (нет сети, нет результатов, сбой
провайдера) — восстановимая ошибка уровня инструмента (SienaToolError):
модель узнаёт о ней и сама решает, что делать дальше.
"""

from __future__ import annotations

from ddgs import DDGS

from core.errors import SienaToolError
from core.message import ToolResult
from tools.base import Tool


class WebSearchTool(Tool):
    name = "web_search"
    description = "Искать актуальную информацию в интернете по текстовому запросу."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Поисковый запрос"}},
        "required": ["query"],
    }

    def __init__(self, max_results: int, timeout: int):
        self._max_results = max_results
        self._timeout = timeout

    def run(self, query: str) -> ToolResult:
        try:
            results = DDGS(timeout=self._timeout).text(query, max_results=self._max_results)
        except Exception as exc:
            raise SienaToolError(f"web_search не смог получить результаты: {exc}") from exc

        if not results:
            raise SienaToolError(f"web_search не нашёл результатов по запросу: {query!r}")

        return ToolResult(
            ok=True,
            content=[
                {"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                for r in results
            ],
        )
