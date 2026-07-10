"""История диалога текущего запуска. Session не принимает решений — только хранит messages[]."""

from __future__ import annotations

from core.message import system_message, tool_message, user_message
from core.message import ToolResult


class Session:
    def __init__(self, system_prompt: str):
        self.messages: list[dict] = [system_message(system_prompt)]

    def add_user(self, content: str) -> None:
        self.messages.append(user_message(content))

    def add_assistant_raw(self, message: dict) -> None:
        """Добавляет сырое сообщение ассистента, как его вернул Ollama (включая tool_calls)."""
        self.messages.append(message)

    def add_tool_result(self, name: str, result: ToolResult, args: dict | None = None) -> None:
        self.messages.append(tool_message(name, result, args))

    def get_messages(self) -> list[dict]:
        return self.messages

    def get_context_messages(self, max_messages: int) -> list[dict]:
        """Технический срез для отправки модели: system prompt (всегда) + последние
        `max_messages` сообщений истории. Полная история в `self.messages` НЕ
        изменяется и не укорачивается — это только то, что физически уезжает в
        Ollama на этот вызов.

        Никакой смысловой фильтрации или суммаризации здесь нет — чистая обрезка
        по позиции (см. DIAGNOSIS_CONTEXT_OVERFLOW.md, раздел 10: Runtime не решает,
        что важно, он лишь ограничивает технически допустимый объём).
        """
        if not self.messages:
            return []
        system = self.messages[0]
        rest = self.messages[1:]
        tail = rest[-max_messages:] if max_messages > 0 else []
        return [system] + tail
