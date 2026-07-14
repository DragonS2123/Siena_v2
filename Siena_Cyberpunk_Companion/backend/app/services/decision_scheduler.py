import asyncio
from collections import deque
from datetime import datetime, timezone

from app.domain.priorities import PRIORITY_RANK, EventPriority
from app.models.companion_command import CommandIntent, CompanionCommand
from app.models.game_event import GameEvent


DECISIONS = {
    "player_health_critical": (CommandIntent.RETREAT, EventPriority.P0_CRITICAL, 12.0),
    "combat_started": (CommandIntent.PROTECT, EventPriority.P1_HIGH, 20.0),
    "companion_too_far": (CommandIntent.REGROUP, EventPriority.P1_HIGH, 15.0),
    "combat_ended": (CommandIntent.FOLLOW, EventPriority.P2_MEDIUM, 30.0),
}


class DecisionScheduler:
    def __init__(self) -> None:
        self._current: CompanionCommand | None = None
        self._reason: str | None = None
        self._p2_context: deque[dict] = deque(maxlen=32)
        self._p3_context: deque[dict] = deque(maxlen=32)
        self._lock = asyncio.Lock()

    async def consider(self, event: GameEvent) -> CompanionCommand | None:
        decision = DECISIONS.get(event.event_type)
        async with self._lock:
            context = {"event_type": event.event_type, "payload": event.payload, "created_at": event.created_at.isoformat()}
            if event.priority == EventPriority.P2_MEDIUM:
                self._p2_context.append(context)
            elif event.priority == EventPriority.P3_LOW:
                self._p3_context.append(context)
            if not decision:
                return None
            intent, priority, ttl = decision
            current = self._valid_current()
            if current and PRIORITY_RANK[current.priority] < PRIORITY_RANK[priority]:
                return None
            if current and current.intent == intent and current.is_valid():
                return None
            self._current = CompanionCommand(intent=intent, priority=priority, valid_for_seconds=ttl, source="mock_scheduler", parameters={"reason": event.event_type})
            self._reason = event.event_type
            return self._current

    async def set_manual(self, command: CompanionCommand) -> CompanionCommand:
        async with self._lock:
            self._current = command
            self._reason = "manual_command"
            return command

    async def current(self, now: datetime | None = None) -> CompanionCommand | None:
        async with self._lock:
            return self._valid_current(now)

    async def status(self) -> dict:
        async with self._lock:
            command = self._valid_current()
            return {
                "mode": "deterministic_mock",
                "reason": self._reason if command else None,
                "command": command.model_dump(mode="json") if command else None,
                "p2_context_count": len(self._p2_context),
                "p3_context_count": len(self._p3_context),
            }

    def _valid_current(self, now: datetime | None = None) -> CompanionCommand | None:
        if self._current and not self._current.is_valid(now or datetime.now(timezone.utc)):
            self._current = None
            self._reason = None
        return self._current
