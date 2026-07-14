from typing import Protocol

from app.models.companion_command import CompanionCommand
from app.models.game_event import GameEvent
from app.models.game_state import GameState


class SienaCoreClient(Protocol):
    async def decide(self, event: GameEvent, state: GameState) -> CompanionCommand | None: ...


class DisabledSienaCoreClient:
    async def decide(self, event: GameEvent, state: GameState) -> CompanionCommand | None:
        return None
