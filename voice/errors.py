from __future__ import annotations

from typing import Any, Protocol


class TTSUnavailableError(RuntimeError):
    pass


class LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...
