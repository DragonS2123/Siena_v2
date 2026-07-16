import asyncio
from collections import deque
from collections.abc import Callable
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
        self._observers: list[Callable[[str, Any], list[tuple[str, Any]]]] = []

    def subscribe(self, observer: Callable[[str, Any], list[tuple[str, Any]]]) -> None:
        self._observers.append(observer)

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
        messages = [(message_type, data)]
        if message_type != "in_game_presence_status":
            for observer in tuple(self._observers):
                messages.extend(observer(message_type, data))
        payloads = []
        compatible = {
            "game_event", "siena_reaction", "reaction_provider_status",
            "reaction_generation_status", "scene_context_updated",
            "voice_generation_status", "voice_clip_ready", "in_game_presence_status",
        }
        for current_type, current_data in messages:
            serialized = current_data.model_dump(mode="json") if hasattr(current_data, "model_dump") else current_data
            payload = {"type": current_type, "data": serialized}
            if current_type in compatible:
                payload["payload"] = serialized
            payloads.append(payload)
        async with self._lock:
            clients = tuple(self._clients)
        for payload in payloads:
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
