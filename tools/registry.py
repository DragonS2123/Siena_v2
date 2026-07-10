"""Реестр инструментов. Runtime здесь исполняет ровно два технических правила:

1. Инструмент существует? Обязательные аргументы присутствуют? (раздел 7.2 ARCHITECTURE.md)
2. Исполнить и вернуть ToolResult модели.

Никакой смысловой оценки того, "нужно ли было" вызывать инструмент, здесь нет —
это было решено моделью до вызова dispatch().
"""

from __future__ import annotations

from core.errors import SienaInfraError, SienaToolError
from core.message import ToolResult
from tools.base import Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [tool.to_schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def dispatch(self, name: str, args: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"Unknown tool: {name}")

        missing = [a for a in tool.required_args() if a not in args or args[a] is None]
        if missing:
            return ToolResult(
                ok=False,
                error=f"Missing required argument(s) for {name}: {', '.join(missing)}",
            )

        try:
            return tool.run(**args)
        except SienaToolError as exc:
            return ToolResult(ok=False, error=str(exc))
        except SienaInfraError:
            # Инфраструктурный сбой Runtime (не сайта/инструмента) — спрашивать
            # модель не у кого, поднимается и эскалируется пользователю напрямую
            # (раздел 7.3 ARCHITECTURE.md).
            raise
        except Exception as exc:
            # Всё остальное — неверный тип аргумента, лишний/отсутствующий kwarg
            # от модели и т.п. Это проблема ФОРМАТА вызова (раздел 7.2), а не
            # инфраструктурный сбой: возвращаем её модели как recoverable
            # ToolResult вместо падения всего процесса.
            return ToolResult(ok=False, error=f"Ошибка вызова инструмента {name}: {exc}")
