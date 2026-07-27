"""Логирование Siena v2: человекочитаемая консоль (INFO) + полная трассировка в JSONL.

Формат JSONL специально стабилен (см. ARCHITECTURE.md раздел 7) — это будущий
источник данных для этапов, явно исключённых из v1 (TrainingCase, Dataset Export),
но сама логика этих этапов здесь не реализуется.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any


_LEVEL_ORDER = {"debug": 10, "info": 20, "warn": 30, "error": 40}


_SENSITIVE_KEYS = {"content", "text", "task", "raw", "raw_message", "args", "proposed_memory"}


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _SENSITIVE_KEYS:
            safe[f"{key}_length"] = len(str(value)) if value is not None else 0
        else:
            safe[key] = value
    return safe


class SienaLogger:
    def __init__(
        self,
        log_dir: Path,
        level: str = "info",
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        log_dir.mkdir(parents=True, exist_ok=True)
        date_tag = datetime.now().strftime("%Y%m%d")
        self._jsonl_path = log_dir / f"siena_{date_tag}.jsonl"
        self._level_value = _LEVEL_ORDER.get(level, _LEVEL_ORDER["info"])
        self._event_sink = event_sink

        self._console = logging.getLogger("siena")
        self._console.setLevel(logging.DEBUG)
        if not self._console.handlers:
            handler = logging.StreamHandler(stream=sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._console.addHandler(handler)
            self._console.propagate = False

    def set_level(self, level: str) -> None:
        """Меняет порог консольного вывода на лету (например, из /api/settings).

        JSONL-лог этот порог не затрагивает — туда всегда пишется всё, полная
        трассировка (ARCHITECTURE.md, раздел 8) не должна зависеть от того,
        насколько "шумной" сейчас хочет видеть консоль пользователь. Порог
        касается только console_message в event()/error() — debug/info печатают
        всё, warn/error приглушают обычные event()-строки и оставляют только
        ошибки (более тонкой грануляции консоль сейчас не различает).
        """
        self._level_value = _LEVEL_ORDER.get(level, _LEVEL_ORDER["info"])

    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **_safe_fields(fields),
        }
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self._event_sink is not None:
            self._event_sink(record)

        if console_message is not None and self._level_value <= _LEVEL_ORDER["info"]:
            self._console.info(console_message)

    def error(self, event_type: str, console_message: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "level": "ERROR",
            **_safe_fields(fields),
        }
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self._event_sink is not None:
            self._event_sink(record)
        self._console.error(console_message)
