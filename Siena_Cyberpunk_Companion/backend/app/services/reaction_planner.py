import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.domain.priorities import PRIORITY_RANK, EventPriority
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import PlannerStatus, ReactionPriority, SienaReaction
from app.models.scene import ReactionFocus, ReactionOpportunity

logger = logging.getLogger("siena_observer.reactions")

TEMPLATES: dict[EventType, str] = {
    EventType.SESSION_STARTED: "Я подключилась и наблюдаю за происходящим.",
    EventType.PLAYER_DAMAGED: "Ты получил заметный урон.",
    EventType.HEALTH_LOW: "Здоровья осталось мало. Будь осторожнее.",
    EventType.HEALTH_CRITICAL: "Здоровье критическое. Найди укрытие.",
    EventType.PLAYER_HEALED: "Стало лучше, здоровье восстановилось.",
    EventType.COMBAT_STARTED: "Начался бой.",
    EventType.COMBAT_ENDED: "Похоже, бой закончился.",
    EventType.VEHICLE_ENTERED: "Ты сел в транспорт.",
    EventType.VEHICLE_EXITED: "Ты вышел из транспорта.",
    EventType.PLAYER_IDLE: "Ты уже некоторое время стоишь на месте.",
    EventType.PLAYER_MOVED_AFTER_IDLE: "Снова в движении.",
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

    def text_for_opportunity(self, opportunity: ReactionOpportunity) -> str:
        return self.text_for_focus(opportunity.focus, opportunity.tactical_context)

    def text_for_focus(self, focus: ReactionFocus, tactical_context: dict | None = None) -> str:
        templates = {
            ReactionFocus.SESSION_GREETING: "Я подключилась и наблюдаю за происходящим.",
            ReactionFocus.DANGER_WARNING: "Здоровье критическое. Найди укрытие.",
            ReactionFocus.COMBAT_COMMENT: "Начался бой. Будь внимательнее.",
            ReactionFocus.RECOVERY_COMMENT: "Стало лучше. Опасность немного отступила.",
            ReactionFocus.SCENE_RESOLUTION: "Похоже, всё закончилось.",
            ReactionFocus.VEHICLE_COMMENT: "Теперь ты в транспорте.",
            ReactionFocus.IDLE_COMMENT: "Ты уже некоторое время не двигаешься.",
            ReactionFocus.EXPLORATION_COMMENT: "Пока всё спокойно.",
            ReactionFocus.RAM_WARNING: "Оперативной памяти осталось мало.",
            ReactionFocus.RESOURCE_RECOVERY: "Оперативная память восстановилась.",
            ReactionFocus.EQUIPMENT_CHANGE: "Оружие сменилось.",
        }
        if focus == ReactionFocus.DANGER_WARNING and tactical_context and tactical_context.get("repeated_category") is True:
            return "Снова опасно низкое здоровье. Найди укрытие."
        return templates[focus]


class DeferredSienaCoreProvider:
    name = "siena_core"

    def text_for(self, event: GameEvent) -> None:
        return None


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
            text = self.provider.text_for(event) if self.enabled else None
            if not text:
                self._event_count += 1
                self._last_event_at = clock
                logger.info("reaction_suppressed event_type=%s reason=no_provider_template", event.event_type)
                return None
            if not self._reserve_locked(event, clock):
                return None
            reaction = SienaReaction(
                event_id=event.event_id,
                session_id=event.session_id,
                event_type=event.event_type,
                text=text,
                created_at=clock,
                priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM],
                provider=self.provider.name,
                requested_provider=self.provider.name,
            )
            self._reaction_count += 1
            logger.info("reaction_created reaction_id=%s event_type=%s provider=%s", reaction.reaction_id, event.event_type, self.provider.name)
            return reaction

    async def reserve(self, event: GameEvent, now: datetime | None = None) -> bool:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            if not self.enabled or event.event_type not in TEMPLATES:
                self._event_count += 1
                self._last_event_at = clock
                logger.info("reaction_suppressed event_type=%s reason=not_reaction_eligible", event.event_type)
                return False
            return self._reserve_locked(event, clock)

    async def reserve_opportunity(self, event: GameEvent, now: datetime | None = None) -> bool:
        """Apply only technical cooldowns; scene policy owns semantic eligibility."""
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            if not self.enabled:
                self._event_count += 1
                self._last_event_at = clock
                return False
            return self._reserve_locked(event, clock)

    def _reserve_locked(self, event: GameEvent, clock: datetime) -> bool:
        self._event_count += 1
        self._last_event_at = clock
        same_type = self._last_by_type.get(event.event_type)
        if same_type and clock - same_type < self.same_event_cooldown:
            logger.info("reaction_suppressed event_type=%s reason=same_event_cooldown", event.event_type)
            return False
        critical = event.priority == EventPriority.P0_CRITICAL
        if not critical and self._last_reaction_at and clock - self._last_reaction_at < self.general_cooldown:
            logger.info("reaction_suppressed event_type=%s reason=general_cooldown", event.event_type)
            return False
        self._last_reaction_at = clock
        self._last_by_type[event.event_type] = clock
        return True

    async def record_reaction(self) -> None:
        async with self._lock:
            self._reaction_count += 1

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
    if name == "template":
        return TemplateReactionProvider()
    if name == "siena_core":
        return DeferredSienaCoreProvider()
    return DisabledReactionProvider()
