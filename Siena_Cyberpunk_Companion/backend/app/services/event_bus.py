import asyncio
from collections import deque
from typing import Any

from app.models.game_event import GameEvent


class EventBus:
    def __init__(self, history_size: int = 1000, client_queue_size: int = 256) -> None:
        self._history: deque[GameEvent] = deque(maxlen=history_size)
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self._client_queue_size = client_queue_size
        self._lock = asyncio.Lock()

    async def connect(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._client_queue_size)
        async with self._lock:
            self._clients.add(queue)
        return queue

    async def disconnect(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._clients.discard(queue)

    async def publish(self, message_type: str, data: Any) -> None:
        if message_type == "event" and isinstance(data, GameEvent):
            self._history.append(data)
        payload = {"type": message_type, "data": data.model_dump(mode="json") if hasattr(data, "model_dump") else data}
        async with self._lock:
            clients = tuple(self._clients)
        for queue in clients:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def events(self, limit: int = 200) -> list[GameEvent]:
        return list(self._history)[-limit:][::-1]
