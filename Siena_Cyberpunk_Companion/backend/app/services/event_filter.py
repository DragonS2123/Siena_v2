import logging
from datetime import datetime, timedelta, timezone

from app.domain.priorities import EventPriority
from app.models.game_event import EventType, GameEvent

logger = logging.getLogger("siena_observer.events")


class EventFilter:
    def __init__(self, damage_window_seconds: float = 1.5, enemy_debounce_seconds: float = 5.0, same_event_cooldown_seconds: float = 60.0) -> None:
        self.damage_window = timedelta(seconds=damage_window_seconds)
        self.enemy_debounce = timedelta(seconds=enemy_debounce_seconds)
        self.same_event_cooldown = timedelta(seconds=same_event_cooldown_seconds)
        self._damage: dict[str, tuple[datetime, GameEvent]] = {}
        self._last_published: dict[str, datetime] = {}

    def process(self, events: list[GameEvent], now: datetime | None = None) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        accepted = self.flush_due(clock)
        for event in events:
            if event.event_type == EventType.PLAYER_DAMAGED:
                self._accumulate_damage(event, clock)
                continue
            cooldown = self.enemy_debounce if event.event_type == EventType.ENEMY_DETECTED else self.same_event_cooldown
            last = self._last_published.get(event.deduplication_key)
            if last and clock - last < cooldown:
                logger.info("event_suppressed event_type=%s reason=cooldown", event.event_type)
                continue
            self._last_published[event.deduplication_key] = clock
            accepted.append(event)
            logger.info("event_created event_id=%s event_type=%s session_id=%s", event.event_id, event.event_type, event.session_id)
            if event.event_type in {EventType.SESSION_STARTED, EventType.SESSION_ENDED}:
                logger.info("%s session_id=%s source=%s", event.event_type, event.session_id, event.source)
        self._prune(clock)
        return accepted

    def _accumulate_damage(self, event: GameEvent, now: datetime) -> None:
        pending = self._damage.get(event.session_id)
        if pending and now - pending[0] < self.damage_window:
            aggregate = pending[1]
            if "current_health" in event.payload:
                aggregate.payload["current_health"] = event.payload["current_health"]
            if "damage_amount" in aggregate.payload or "damage_amount" in event.payload:
                aggregate.payload["damage_amount"] = aggregate.payload.get("damage_amount", 0) + event.payload.get("damage_amount", 0)
            aggregate.payload["total_damage"] = aggregate.payload.get("total_damage", 0) + event.payload.get("total_damage", 0)
            aggregate.payload["hits"] = aggregate.payload.get("hits", 0) + event.payload.get("hits", 1)
            if "health_percent" in event.payload:
                aggregate.payload["health_percent"] = event.payload["health_percent"]
            aggregate.sequence = event.sequence
            aggregate.summary = f"Player took {aggregate.payload['total_damage']:.1f} damage across {aggregate.payload['hits']} hits"
            logger.info("event_aggregated event_type=player_damaged session_id=%s hits=%s", event.session_id, aggregate.payload["hits"])
        else:
            self._damage[event.session_id] = (now, event.model_copy(deep=True))

    def flush_due(self, now: datetime | None = None, force: bool = False) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        ready: list[GameEvent] = []
        for session, (started, event) in list(self._damage.items()):
            if force or clock - started >= self.damage_window:
                event.created_at = clock
                ready.append(event)
                self._last_published[event.deduplication_key] = clock
                del self._damage[session]
                logger.info("event_created event_id=%s event_type=player_damaged session_id=%s", event.event_id, session)
        return ready

    def _prune(self, now: datetime) -> None:
        cutoff = now - max(self.enemy_debounce, self.same_event_cooldown, timedelta(seconds=30)) * 2
        self._last_published = {key: value for key, value in self._last_published.items() if value >= cutoff}
