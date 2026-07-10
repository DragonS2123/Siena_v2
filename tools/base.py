"""Абстрактный контракт инструмента. Любой tool — это только исполнитель:
он не решает, нужно ли его вызывать, это уже решила модель."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.message import ToolResult


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema для tool-calling

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        ...

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def required_args(self) -> list[str]:
        return self.parameters.get("required", [])
