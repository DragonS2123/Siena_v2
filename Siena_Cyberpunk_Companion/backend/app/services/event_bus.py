import asyncio
from collections import deque
from typing import Any

from app.models.game_event import GameEvent
from app.models.reaction import SienaReaction


class EventBus:
    def __init__(self, history_size: int = 500, client_queue_size: int = 256, reaction_history_size: int = 200) -> None:
        self._history: deque[GameEvent] = deque(maxlen=history_size)
        self._reactions: deque[SienaReaction] = deque(maxlen=reaction_history_size)
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
        if message_type == "siena_reaction" and isinstance(data, SienaReaction):
            self._reactions.append(data)
        serialized = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        payload = {"type": message_type, "data": serialized}
        if message_type in {"game_event", "siena_reaction", "reaction_provider_status"}:
            payload["payload"] = serialized
        async with self._lock:
            clients = tuple(self._clients)
        for queue in clients:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def events(self, limit: int = 200, event_type: str | None = None, severity: str | None = None, session_id: str | None = None) -> list[GameEvent]:
        items = reversed(self._history)
        filtered = (item for item in items if (not event_type or item.event_type == event_type) and (not severity or item.severity == severity) and (not session_id or item.session_id == session_id))
        return list(filtered)[:limit]

    def reactions(self, limit: int = 200, event_type: str | None = None) -> list[SienaReaction]:
        items = reversed(self._reactions)
        filtered = (item for item in items if not event_type or item.event_type == event_type)
        return list(filtered)[:limit]
