import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.domain.priorities import PRIORITY_RANK, EventPriority
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import PlannerStatus, ReactionPriority, SienaReaction

logger = logging.getLogger("siena_observer.reactions")

TEMPLATES: dict[EventType, str] = {
    EventType.SESSION_STARTED: "Я подключилась к игровой сессии и наблюдаю.",
    EventType.PLAYER_DAMAGED: "Ты получил серьёзный урон.",
    EventType.HEALTH_LOW: "Здоровье уже низкое. Лучше найти укрытие.",
    EventType.HEALTH_CRITICAL: "Здоровье критическое.",
    EventType.PLAYER_HEALED: "Здоровье восстановлено.",
    EventType.COMBAT_STARTED: "Начался бой.",
    EventType.COMBAT_ENDED: "Похоже, бой закончился.",
    EventType.VEHICLE_ENTERED: "Ты сел в транспорт.",
    EventType.PLAYER_IDLE: "Ты уже некоторое время стоишь на месте.",
}

REACTION_PRIORITY = {
    EventSeverity.INFO: ReactionPriority.LOW,
    EventSeverity.LOW: ReactionPriority.LOW,
    EventSeverity.MEDIUM: ReactionPriority.MEDIUM,
    EventSeverity.HIGH: ReactionPriority.HIGH,
    EventSeverity.CRITICAL: ReactionPriority.CRITICAL,
}


class ReactionProvider(Protocol):
    name: str

    def text_for(self, event: GameEvent) -> str | None: ...


class DisabledReactionProvider:
    name = "disabled"

    def text_for(self, event: GameEvent) -> None:
        return None


class TemplateReactionProvider:
    name = "template"

    def text_for(self, event: GameEvent) -> str | None:
        return TEMPLATES.get(event.event_type)


class ReactionPlanner:
    def __init__(
        self,
        provider: ReactionProvider,
        enabled: bool = True,
        general_cooldown_seconds: float = 20.0,
        same_event_cooldown_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.enabled = enabled and provider.name != "disabled"
        self.general_cooldown = timedelta(seconds=general_cooldown_seconds)
        self.same_event_cooldown = timedelta(seconds=same_event_cooldown_seconds)
        self._last_reaction_at: datetime | None = None
        self._last_by_type: dict[EventType, datetime] = {}
        self._event_count = 0
        self._reaction_count = 0
        self._last_event_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def consider(self, event: GameEvent, now: datetime | None = None) -> SienaReaction | None:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            self._event_count += 1
            self._last_event_at = clock
            text = self.provider.text_for(event) if self.enabled else None
            if not text:
                logger.info("reaction_suppressed event_type=%s reason=no_provider_template", event.event_type)
                return None
            same_type = self._last_by_type.get(event.event_type)
            if same_type and clock - same_type < self.same_event_cooldown:
                logger.info("reaction_suppressed event_type=%s reason=same_event_cooldown", event.event_type)
                return None
            critical = event.priority == EventPriority.P0_CRITICAL
            if not critical and self._last_reaction_at and clock - self._last_reaction_at < self.general_cooldown:
                logger.info("reaction_suppressed event_type=%s reason=general_cooldown", event.event_type)
                return None
            reaction = SienaReaction(
                event_id=event.event_id,
                event_type=event.event_type,
                text=text,
                created_at=clock,
                priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM],
                provider=self.provider.name,
            )
            self._last_reaction_at = clock
            self._last_by_type[event.event_type] = clock
            self._reaction_count += 1
            logger.info("reaction_created reaction_id=%s event_type=%s provider=%s", reaction.reaction_id, event.event_type, self.provider.name)
            return reaction

    async def consider_many(self, events: list[GameEvent], now: datetime | None = None) -> list[SienaReaction]:
        reactions: list[SienaReaction] = []
        for event in sorted(events, key=lambda item: PRIORITY_RANK[item.priority]):
            reaction = await self.consider(event, now)
            if reaction:
                reactions.append(reaction)
        return reactions

    async def status(self, now: datetime | None = None) -> PlannerStatus:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            remaining = 0.0
            if self._last_reaction_at:
                remaining = max(0.0, (self.general_cooldown - (clock - self._last_reaction_at)).total_seconds())
            return PlannerStatus(
                enabled=self.enabled,
                provider=self.provider.name,
                queued_events=0,
                event_count=self._event_count,
                reaction_count=self._reaction_count,
                last_event_at=self._last_event_at,
                last_reaction_at=self._last_reaction_at,
                cooldown_remaining_seconds=remaining,
            )


def create_reaction_provider(name: str) -> ReactionProvider:
    return TemplateReactionProvider() if name == "template" else DisabledReactionProvider()
