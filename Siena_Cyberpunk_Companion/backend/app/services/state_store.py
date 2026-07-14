import asyncio

from app.models.game_state import GameState


class SequenceConflict(ValueError):
    pass


class StateStore:
    def __init__(self) -> None:
        self._latest_by_source: dict[str, GameState] = {}
        self._active_source = "simulator"
        self._lock = asyncio.Lock()

    async def accept(self, state: GameState) -> GameState | None:
        async with self._lock:
            previous = self._latest_by_source.get(state.source)
            if previous and state.session_id == previous.session_id and state.sequence <= previous.sequence:
                raise SequenceConflict(
                    f"sequence must increase for session {state.session_id!r}; "
                    f"received {state.sequence}, latest is {previous.sequence}"
                )
            self._latest_by_source[state.source] = state
            return previous

    async def activate(self, source: str) -> bool:
        async with self._lock:
            changed = source != self._active_source
            self._active_source = source
            return changed

    async def latest(self) -> GameState | None:
        async with self._lock:
            return self._latest_by_source.get(self._active_source)

    async def latest_for_source(self, source: str) -> GameState | None:
        async with self._lock:
            return self._latest_by_source.get(source)

    async def active_source(self) -> str:
        async with self._lock:
            return self._active_source
