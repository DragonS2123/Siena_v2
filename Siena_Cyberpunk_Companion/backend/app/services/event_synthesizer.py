from datetime import datetime, timezone

from app.domain.priorities import EventPriority
from app.domain.thresholds import (
    COMPANION_CRITICAL_HEALTH_PERCENT,
    PLAYER_CRITICAL_HEALTH_PERCENT,
    PLAYER_LOW_HEALTH_PERCENT,
)
from app.models.game_event import GameEvent
from app.models.game_state import GameState
from app.services.state_diff import StateChange


class EventSynthesizer:
    def __init__(self, too_far_meters: float = 20.0, stuck_seconds: float = 5.0, movement_epsilon: float = 0.25) -> None:
        self.too_far_meters = too_far_meters
        self.stuck_seconds = stuck_seconds
        self.movement_epsilon = movement_epsilon
        self._stuck_since: dict[str, datetime] = {}
        self._stuck_emitted: set[str] = set()

    @staticmethod
    def _percent(value: float, maximum: float) -> float:
        return value / maximum * 100.0

    @staticmethod
    def _event(state: GameState, event_type: str, priority: EventPriority, payload: dict | None = None, key: str | None = None) -> GameEvent:
        return GameEvent(
            session_id=state.session_id,
            event_type=event_type,
            priority=priority,
            deduplication_key=key or f"{state.session_id}:{event_type}",
            payload=payload or {},
        )

    def synthesize(
        self,
        previous: GameState | None,
        current: GameState,
        changes: list[StateChange],
        now: datetime | None = None,
    ) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        events: list[GameEvent] = []
        new_session = previous is None or previous.session_id != current.session_id

        def changed(path: str) -> bool:
            return new_session or any(change.path == path for change in changes)

        if new_session:
            if current.game.running:
                events.append(self._event(current, "game_started", EventPriority.P2_MEDIUM))
            if current.game.loaded:
                events.append(self._event(current, "game_loaded", EventPriority.P2_MEDIUM))
            if current.player.in_combat:
                events.append(self._event(current, "combat_started", EventPriority.P1_HIGH))
            if current.companion.present:
                events.append(self._event(current, "companion_appeared", EventPriority.P2_MEDIUM))
        else:
            pairs = [
                ("game.running", previous.game.running, current.game.running, "game_started", "game_stopped", EventPriority.P2_MEDIUM),
                ("game.paused", previous.game.paused, current.game.paused, "game_paused", "game_resumed", EventPriority.P2_MEDIUM),
                ("player.in_combat", previous.player.in_combat, current.player.in_combat, "combat_started", "combat_ended", EventPriority.P1_HIGH),
                ("player.in_vehicle", previous.player.in_vehicle, current.player.in_vehicle, "player_entered_vehicle", "player_exited_vehicle", EventPriority.P2_MEDIUM),
                ("companion.present", previous.companion.present, current.companion.present, "companion_appeared", "companion_disappeared", EventPriority.P1_HIGH),
            ]
            for path, before, after, on_true, on_false, priority in pairs:
                if changed(path) and before != after:
                    events.append(self._event(current, on_true if after else on_false, priority))
            if not previous.game.loaded and current.game.loaded:
                events.append(self._event(current, "game_loaded", EventPriority.P2_MEDIUM))

            if current.player.health < previous.player.health:
                damage = previous.player.health - current.player.health
                events.append(self._event(current, "player_damaged", EventPriority.P1_HIGH, {"total_damage": damage, "hits": 1}))

            previous_pct = self._percent(previous.player.health, previous.player.max_health)
            current_pct = self._percent(current.player.health, current.player.max_health)
            if previous_pct > PLAYER_LOW_HEALTH_PERCENT >= current_pct:
                events.append(self._event(current, "player_health_below_50", EventPriority.P1_HIGH, {"health_percent": current_pct}))
            if previous_pct > PLAYER_CRITICAL_HEALTH_PERCENT >= current_pct:
                events.append(self._event(current, "player_health_critical", EventPriority.P0_CRITICAL, {"health_percent": current_pct}))
            if previous_pct <= PLAYER_CRITICAL_HEALTH_PERCENT < current_pct:
                events.append(self._event(current, "player_recovered", EventPriority.P1_HIGH, {"health_percent": current_pct}))

            if previous.environment.visible_hostiles == 0 < current.environment.visible_hostiles:
                threat = current.environment.highest_threat_id or "unknown"
                events.append(self._event(current, "enemy_detected", EventPriority.P1_HIGH, {"count": current.environment.visible_hostiles, "threat_id": threat}, f"{current.session_id}:enemy:{threat}"))
            if previous.environment.visible_hostiles != current.environment.visible_hostiles:
                events.append(self._event(current, "enemy_count_changed", EventPriority.P2_MEDIUM, {"before": previous.environment.visible_hostiles, "after": current.environment.visible_hostiles}))
            if previous.environment.district != current.environment.district:
                events.append(self._event(current, "district_changed", EventPriority.P3_LOW, {"before": previous.environment.district, "after": current.environment.district}, f"{current.session_id}:district:{current.environment.district}"))
            if previous.companion.current_intent != current.companion.current_intent:
                events.append(self._event(current, "companion_intent_changed", EventPriority.P2_MEDIUM, {"before": previous.companion.current_intent, "after": current.companion.current_intent}))

            previous_companion_pct = self._percent(previous.companion.health, previous.companion.max_health)
            current_companion_pct = self._percent(current.companion.health, current.companion.max_health)
            if previous_companion_pct > COMPANION_CRITICAL_HEALTH_PERCENT >= current_companion_pct:
                events.append(self._event(current, "companion_health_critical", EventPriority.P0_CRITICAL, {"health_percent": current_companion_pct}))

            if previous.companion.distance_to_player <= self.too_far_meters < current.companion.distance_to_player:
                events.append(self._event(current, "companion_too_far", EventPriority.P1_HIGH, {"distance": current.companion.distance_to_player}))
            if previous.companion.distance_to_player > self.too_far_meters >= current.companion.distance_to_player:
                events.append(self._event(current, "companion_regrouped", EventPriority.P2_MEDIUM, {"distance": current.companion.distance_to_player}))

        self._update_stuck(previous, current, clock, events)
        return events

    def _update_stuck(self, previous: GameState | None, current: GameState, now: datetime, events: list[GameEvent]) -> None:
        session = current.session_id
        eligible = current.companion.present and current.companion.moving and current.companion.current_intent in {"follow", "regroup"}
        progressed = previous is None or previous.session_id != session or abs(previous.companion.distance_to_player - current.companion.distance_to_player) > self.movement_epsilon
        if not eligible or progressed:
            was_stuck = session in self._stuck_emitted
            self._stuck_since.pop(session, None)
            self._stuck_emitted.discard(session)
            if was_stuck and eligible:
                events.append(self._event(current, "companion_regrouped", EventPriority.P2_MEDIUM, {"reason": "movement_resumed"}))
            return
        since = self._stuck_since.setdefault(session, now)
        if session not in self._stuck_emitted and (now - since).total_seconds() >= self.stuck_seconds:
            self._stuck_emitted.add(session)
            events.append(self._event(current, "companion_stuck", EventPriority.P1_HIGH, {"duration_seconds": self.stuck_seconds}))

    def is_stuck(self, session_id: str) -> bool:
        return session_id in self._stuck_emitted
