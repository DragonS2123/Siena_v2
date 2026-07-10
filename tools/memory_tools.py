"""Tool-обёртки над memory/short_memory_store.py и memory/long_memory_store.py.

Единственная логика здесь — вызов store и упаковка в ToolResult. Runtime не решает,
что сохранить, какую категорию/важность указать или достаточно ли явно пользователь
попросил долговременное сохранение — всё это уже решила модель (ARCHITECTURE.md, 5.3-5.4).

Логгер инжектируется, чтобы каждый вызов памяти оставлял отдельный, легко фильтруемый
след в JSONL (short_memory_saved/long_memory_saved/memory_tool_result) — это диагностика
для человека, а не решение, принимаемое Runtime.
"""

from __future__ import annotations

from core.errors import SienaToolError
from core.message import ToolResult
from logging_.logger import SienaLogger
from memory.long_memory_store import LongMemoryStore
from memory.short_memory_store import ShortMemoryStore
from tools.base import Tool


class ShortMemorySaveTool(Tool):
    name = "short_memory_save"
    description = (
        "Сохранить временный рабочий факт/заметку/промежуточное решение в кратковременную "
        "память текущей сессии."
    )
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Текст, который нужно запомнить"}},
        "required": ["text"],
    }

    def __init__(self, store: ShortMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(self, text: str) -> ToolResult:
        entry = self._store.save(text)
        self._logger.event(
            "short_memory_saved",
            id=entry["id"],
            text=entry["text"],
            console_message=f"[MEMORY][SHORT][SAVE] {text}",
        )
        return ToolResult(ok=True, content=entry)


class ShortMemorySearchTool(Tool):
    name = "short_memory_search"
    description = "Найти ранее сохранённые временные факты текущей сессии по запросу."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Поисковый запрос"}},
        "required": ["query"],
    }

    def __init__(self, store: ShortMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(self, query: str) -> ToolResult:
        matches = self._store.search(query)
        self._logger.event(
            "memory_tool_result",
            tool=self.name,
            query=query,
            matches=len(matches),
            console_message=f"[MEMORY][SHORT][SEARCH] {query!r} -> {len(matches)} совпадений",
        )
        if not matches:
            raise SienaToolError(f"В кратковременной памяти ничего не найдено по запросу: {query!r}")
        return ToolResult(ok=True, content=matches)


class ShortMemoryClearTool(Tool):
    name = "short_memory_clear"
    description = "Полностью очистить кратковременную память текущей сессии."
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, store: ShortMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(self) -> ToolResult:
        cleared = self._store.clear()
        self._logger.event(
            "memory_tool_result",
            tool=self.name,
            cleared=cleared,
            console_message=f"[MEMORY][SHORT][CLEAR] удалено записей: {cleared}",
        )
        return ToolResult(ok=True, content={"cleared": cleared})


class LongMemorySaveTool(Tool):
    name = "long_memory_save"
    description = (
        "Сохранить факт в долговременную память НАВСЕГДА (переживает перезапуск программы). "
        "Вызывать только когда пользователь явно попросил что-то запомнить/сохранить/оставить "
        "в памяти надолго."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Текст факта для сохранения"},
            "category": {
                "type": "string",
                "description": "Категория факта (например: architecture, preference, decision). Определяется моделью.",
            },
            "importance": {
                "type": "string",
                "description": "Важность факта (например: low, medium, high). Определяется моделью.",
            },
        },
        "required": ["text"],
    }

    def __init__(self, store: LongMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(self, text: str, category: str | None = None, importance: str | None = None) -> ToolResult:
        self._logger.event(
            "long_memory_save_started",
            text=text,
            category=category,
            importance=importance,
            console_message=f"[MEMORY][LONG][SAVE] начато: {text}",
        )
        try:
            entry = self._store.save(text, category=category, importance=importance, source="siena_v2")
        except Exception as exc:
            self._logger.error(
                "long_memory_save_failed",
                console_message=f"[MEMORY][LONG][SAVE] ошибка: {exc}",
                text=text,
                category=category,
                importance=importance,
                error=str(exc),
            )
            raise
        self._logger.event(
            "long_memory_saved",
            id=entry["id"],
            text=entry["text"],
            category=entry["category"],
            importance=entry["importance"],
            console_message=(
                f"[MEMORY][LONG][SAVE] {text} (category={category}, importance={importance})"
            ),
        )
        return ToolResult(ok=True, content=entry)


class LongMemorySearchTool(Tool):
    name = "long_memory_search"
    description = "Найти факты в долговременной памяти по тексту или категории."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Поисковый запрос (ищется по тексту и категории)"}
        },
        "required": ["query"],
    }

    def __init__(self, store: LongMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(self, query: str) -> ToolResult:
        matches = self._store.search(query)
        self._logger.event(
            "memory_tool_result",
            tool=self.name,
            query=query,
            matches=len(matches),
            console_message=f"[MEMORY][LONG][SEARCH] {query!r} -> {len(matches)} совпадений",
        )
        if not matches:
            raise SienaToolError(f"В долговременной памяти ничего не найдено по запросу: {query!r}")
        return ToolResult(ok=True, content=matches)


class LongMemoryListTool(Tool):
    name = "long_memory_list"
    description = "Показать последние сохранённые факты долговременной памяти."
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Сколько последних записей вернуть (по умолчанию 20)"}
        },
        "required": [],
    }

    def __init__(self, store: LongMemoryStore, logger: SienaLogger, default_limit: int = 20):
        self._store = store
        self._logger = logger
        self._default_limit = default_limit

    def run(self, limit: int | None = None) -> ToolResult:
        entries = self._store.list_recent(limit or self._default_limit)
        self._logger.event(
            "memory_tool_result",
            tool=self.name,
            limit=limit or self._default_limit,
            count=len(entries),
            console_message=f"[MEMORY][LONG][LIST] limit={limit or self._default_limit} -> {len(entries)} записей",
        )
        return ToolResult(ok=True, content=entries)
