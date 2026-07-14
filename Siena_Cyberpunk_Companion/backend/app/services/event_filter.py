from datetime import datetime, timedelta, timezone

from app.domain.priorities import EventPriority
from app.models.game_event import GameEvent


class EventFilter:
    def __init__(self, damage_window_seconds: float = 0.5, enemy_debounce_seconds: float = 5.0) -> None:
        self.damage_window = timedelta(seconds=damage_window_seconds)
        self.enemy_debounce = timedelta(seconds=enemy_debounce_seconds)
        self._damage: dict[str, tuple[datetime, GameEvent]] = {}
        self._last_published: dict[str, datetime] = {}

    def process(self, events: list[GameEvent], now: datetime | None = None) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        accepted = self.flush_due(clock)
        for event in events:
            if event.event_type == "player_damaged":
                self._accumulate_damage(event, clock)
                continue
            if event.event_type == "enemy_detected":
                last = self._last_published.get(event.deduplication_key)
                if last and clock - last < self.enemy_debounce:
                    continue
            if event.priority == EventPriority.P3_LOW:
                last = self._last_published.get(event.deduplication_key)
                if last and clock - last < self.enemy_debounce:
                    continue
            self._last_published[event.deduplication_key] = clock
            accepted.append(event)
        self._prune(clock)
        return accepted

    def _accumulate_damage(self, event: GameEvent, now: datetime) -> None:
        pending = self._damage.get(event.session_id)
        if pending and now - pending[0] < self.damage_window:
            aggregate = pending[1]
            aggregate.payload["total_damage"] = aggregate.payload.get("total_damage", 0) + event.payload.get("total_damage", 0)
            aggregate.payload["hits"] = aggregate.payload.get("hits", 0) + event.payload.get("hits", 1)
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
        return ready

    def _prune(self, now: datetime) -> None:
        cutoff = now - max(self.enemy_debounce, timedelta(seconds=30)) * 2
        self._last_published = {key: value for key, value in self._last_published.items() if value >= cutoff}
