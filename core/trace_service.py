"""In-memory bounded Tool Trace fan-out."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


class TraceService:
    def __init__(self, limit: int = 500):
        self._recent: deque[dict[str, Any]] = deque(maxlen=limit)
        self._subscribers: set[asyncio.Queue] = set()

    def publish(self, event: dict[str, Any]) -> None:
        self._recent.append(event)
        for queue in tuple(self._subscribers):
            queue.put_nowait(event)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._recent)[-max(1, min(limit, 500)):]

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
