import logging
import math
from datetime import datetime, timezone

from app.domain.priorities import EventPriority
from app.domain.thresholds import COMPANION_CRITICAL_HEALTH_PERCENT, PLAYER_CRITICAL_HEALTH_PERCENT, PLAYER_LOW_HEALTH_PERCENT
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventType, GameEvent
from app.models.game_state import GameState
from app.services.state_diff import StateChange
from app.services.deep_game_state_awareness import DeepGameStateAwareness

logger = logging.getLogger("siena_observer.events")


SUMMARY: dict[EventType, str] = {
    EventType.SESSION_STARTED: "Game session telemetry became available",
    EventType.SESSION_ENDED: "Game session telemetry timed out",
    EventType.PLAYER_DAMAGED: "Player health decreased",
    EventType.HEALTH_LOW: "Player health dropped below 25%",
    EventType.HEALTH_CRITICAL: "Player health dropped below 10%",
    EventType.PLAYER_HEALED: "Player health recovered significantly",
    EventType.COMBAT_STARTED: "Combat started",
    EventType.COMBAT_ENDED: "Combat ended",
    EventType.VEHICLE_ENTERED: "Player entered a vehicle",
    EventType.VEHICLE_EXITED: "Player exited a vehicle",
    EventType.PLAYER_IDLE: "Player has been idle",
    EventType.PLAYER_MOVED_AFTER_IDLE: "Player moved after being idle",
}


class EventSynthesizer:
    def __init__(
        self,
        too_far_meters: float = 20.0,
        stuck_seconds: float = 5.0,
        movement_epsilon: float = 0.25,
        health_low_percent: float = 25.0,
        health_recovery_percent: float = 35.0,
        health_critical_percent: float = 10.0,
        heal_threshold_percent: float = 5.0,
        idle_timeout_seconds: float = 60.0,
        player_position_epsilon: float = 0.5,
        session_timeout_seconds: float = 10.0,
    ) -> None:
        self.too_far_meters = too_far_meters
        self.stuck_seconds = stuck_seconds
        self.movement_epsilon = movement_epsilon
        self.health_low_percent = health_low_percent
        self.health_recovery_percent = health_recovery_percent
        self.health_critical_percent = health_critical_percent
        self.heal_threshold_percent = heal_threshold_percent
        self.idle_timeout_seconds = idle_timeout_seconds
        self.player_position_epsilon = player_position_epsilon
        self.session_timeout_seconds = session_timeout_seconds
        self._stuck_since: dict[str, datetime] = {}
        self._stuck_emitted: set[str] = set()
        self._health_low: set[str] = set()
        self._health_critical: set[str] = set()
        self._idle_since: dict[str, datetime] = {}
        self._idle_emitted: set[str] = set()
        self._last_seen: dict[tuple[str, str], tuple[datetime, GameState]] = {}
        self._ended: set[tuple[str, str]] = set()
        self._active_session_by_source: dict[str, str] = {}
        self.deep_awareness = DeepGameStateAwareness()

    @staticmethod
    def _percent(value: float, maximum: float) -> float:
        return value / maximum * 100.0

    @staticmethod
    def _all_capabilities() -> BridgeCapabilities:
        return BridgeCapabilities(player_health=True, player_position=True, combat_state=True, vehicle_state=True, pause_state=True, district=True)

    @staticmethod
    def _distance(previous: GameState, current: GameState) -> float:
        before, after = previous.player.position, current.player.position
        return math.sqrt((after.x - before.x) ** 2 + (after.y - before.y) ** 2 + (after.z - before.z) ** 2)

    @staticmethod
    def _event(
        state: GameState,
        event_type: EventType | str,
        priority: EventPriority,
        payload: dict | None = None,
        key: str | None = None,
        summary: str | None = None,
        now: datetime | None = None,
    ) -> GameEvent:
        normalized_type = EventType(event_type)
        return GameEvent(
            session_id=state.session_id,
            event_type=normalized_type,
            priority=priority,
            created_at=now or datetime.now(timezone.utc),
            source=state.source,
            sequence=state.sequence,
            summary=summary or SUMMARY.get(normalized_type, normalized_type.value.replace("_", " ").capitalize()),
            deduplication_key=key or f"{state.session_id}:{normalized_type.value}",
            payload=payload or {},
        )

    def synthesize(
        self,
        previous: GameState | None,
        current: GameState,
        changes: list[StateChange],
        now: datetime | None = None,
        capabilities: BridgeCapabilities | None = None,
        observed_at: datetime | None = None,
    ) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        liveness_clock = observed_at or clock
        caps = capabilities or self._all_capabilities()
        events: list[GameEvent] = []
        new_session = previous is None or previous.session_id != current.session_id
        deep_contract_present = current.deep_game_state is not None

        old_session = self._active_session_by_source.get(current.source)
        if old_session and old_session != current.session_id:
            self._last_seen.pop((current.source, old_session), None)
            self._ended.discard((current.source, old_session))
        self._active_session_by_source[current.source] = current.session_id
        self._last_seen[(current.source, current.session_id)] = (liveness_clock, current)
        self._ended.discard((current.source, current.session_id))

        def changed(path: str) -> bool:
            return new_session or any(change.path == path for change in changes)

        if new_session:
            session_payload = {}
            if current.deep_game_state and current.deep_game_state.player:
                deep_player = current.deep_game_state.player
                session_payload = {
                    "player_available": deep_player.entity_available,
                    "session_available": deep_player.session_available,
                    "is_pre_game": deep_player.is_pre_game,
                }
            events.append(self._event(current, EventType.SESSION_STARTED, EventPriority.P2_MEDIUM, session_payload, now=clock))
            if current.game.running:
                events.append(self._event(current, EventType.GAME_STARTED, EventPriority.P2_MEDIUM, now=clock))
            if current.game.loaded:
                events.append(self._event(current, EventType.GAME_LOADED, EventPriority.P2_MEDIUM, now=clock))
            if caps.combat_state and current.player.in_combat:
                events.append(self._event(current, EventType.COMBAT_STARTED, EventPriority.P1_HIGH, now=clock))
            if current.companion.present:
                events.append(self._event(current, EventType.COMPANION_APPEARED, EventPriority.P2_MEDIUM, now=clock))
        else:
            pairs = [
                ("game.running", previous.game.running, current.game.running, EventType.GAME_STARTED, EventType.GAME_STOPPED, EventPriority.P2_MEDIUM, True),
                ("game.paused", previous.game.paused, current.game.paused, EventType.GAME_PAUSED, EventType.GAME_RESUMED, EventPriority.P2_MEDIUM, caps.pause_state),
                ("player.in_combat", previous.player.in_combat, current.player.in_combat, EventType.COMBAT_STARTED, EventType.COMBAT_ENDED, EventPriority.P1_HIGH, caps.combat_state),
                ("companion.present", previous.companion.present, current.companion.present, EventType.COMPANION_APPEARED, EventType.COMPANION_DISAPPEARED, EventPriority.P1_HIGH, True),
            ]
            for path, before, after, on_true, on_false, priority, available in pairs:
                if available and changed(path) and before != after:
                    events.append(self._event(current, on_true if after else on_false, priority, now=clock))
            if caps.vehicle_state and changed("player.in_vehicle") and previous.player.in_vehicle != current.player.in_vehicle:
                new_type = EventType.VEHICLE_ENTERED if current.player.in_vehicle else EventType.VEHICLE_EXITED
                legacy_type = EventType.PLAYER_ENTERED_VEHICLE if current.player.in_vehicle else EventType.PLAYER_EXITED_VEHICLE
                events.append(self._event(current, new_type, EventPriority.P2_MEDIUM, now=clock))
                events.append(self._event(current, legacy_type, EventPriority.P2_MEDIUM, now=clock))
            if not previous.game.loaded and current.game.loaded:
                events.append(self._event(current, EventType.GAME_LOADED, EventPriority.P2_MEDIUM, now=clock))

            if caps.player_health and not deep_contract_present and current.player.health < previous.player.health:
                damage = previous.player.health - current.player.health
                current_pct = self._percent(current.player.health, current.player.max_health)
                events.append(self._event(current, EventType.PLAYER_DAMAGED, EventPriority.P1_HIGH, {
                    "previous_health": previous.player.health,
                    "current_health": current.player.health,
                    "damage_amount": damage,
                    "total_damage": damage,
                    "hits": 1,
                    "health_percent": current_pct,
                }, now=clock))

            if caps.player_health and not deep_contract_present and current.player.health > previous.player.health:
                recovered_pct = (current.player.health - previous.player.health) / current.player.max_health * 100.0
                if recovered_pct >= self.heal_threshold_percent:
                    events.append(self._event(current, EventType.PLAYER_HEALED, EventPriority.P2_MEDIUM, {
                        "previous_health": previous.player.health,
                        "current_health": current.player.health,
                        "healed_amount": current.player.health - previous.player.health,
                        "health_percent": self._percent(current.player.health, current.player.max_health),
                    }, now=clock))

            previous_pct = self._percent(previous.player.health, previous.player.max_health)
            current_pct = self._percent(current.player.health, current.player.max_health)
            if caps.player_health and not deep_contract_present:
                if previous_pct > PLAYER_LOW_HEALTH_PERCENT >= current_pct:
                    events.append(self._event(current, EventType.PLAYER_HEALTH_BELOW_50, EventPriority.P1_HIGH, {"health_percent": current_pct}, now=clock))
                if previous_pct > PLAYER_CRITICAL_HEALTH_PERCENT >= current_pct:
                    events.append(self._event(current, EventType.PLAYER_HEALTH_CRITICAL, EventPriority.P0_CRITICAL, {"health_percent": current_pct}, now=clock))
                if previous_pct <= PLAYER_CRITICAL_HEALTH_PERCENT < current_pct:
                    events.append(self._event(current, EventType.PLAYER_RECOVERED, EventPriority.P1_HIGH, {"health_percent": current_pct}, now=clock))

            if previous.environment.visible_hostiles == 0 < current.environment.visible_hostiles:
                threat = current.environment.highest_threat_id or "unknown"
                events.append(self._event(current, EventType.ENEMY_DETECTED, EventPriority.P1_HIGH, {"count": current.environment.visible_hostiles, "threat_id": threat}, f"{current.session_id}:enemy:{threat}", now=clock))
            if previous.environment.visible_hostiles != current.environment.visible_hostiles:
                events.append(self._event(current, EventType.ENEMY_COUNT_CHANGED, EventPriority.P2_MEDIUM, {"before": previous.environment.visible_hostiles, "after": current.environment.visible_hostiles}, now=clock))
            if caps.district and previous.environment.district != current.environment.district:
                events.append(self._event(current, EventType.DISTRICT_CHANGED, EventPriority.P3_LOW, {"before": previous.environment.district, "after": current.environment.district}, f"{current.session_id}:district:{current.environment.district}", now=clock))
            if previous.companion.current_intent != current.companion.current_intent:
                events.append(self._event(current, EventType.COMPANION_INTENT_CHANGED, EventPriority.P2_MEDIUM, {"before": previous.companion.current_intent, "after": current.companion.current_intent}, now=clock))

            previous_companion_pct = self._percent(previous.companion.health, previous.companion.max_health)
            current_companion_pct = self._percent(current.companion.health, current.companion.max_health)
            if previous_companion_pct > COMPANION_CRITICAL_HEALTH_PERCENT >= current_companion_pct:
                events.append(self._event(current, EventType.COMPANION_HEALTH_CRITICAL, EventPriority.P0_CRITICAL, {"health_percent": current_companion_pct}, now=clock))
            if previous.companion.distance_to_player <= self.too_far_meters < current.companion.distance_to_player:
                events.append(self._event(current, EventType.COMPANION_TOO_FAR, EventPriority.P1_HIGH, {"distance": current.companion.distance_to_player}, now=clock))
            if previous.companion.distance_to_player > self.too_far_meters >= current.companion.distance_to_player:
                events.append(self._event(current, EventType.COMPANION_REGROUPED, EventPriority.P2_MEDIUM, {"distance": current.companion.distance_to_player}, now=clock))

        if caps.player_health and not deep_contract_present:
            current_pct = self._percent(current.player.health, current.player.max_health)
            if current_pct > self.health_recovery_percent:
                self._health_low.discard(current.session_id)
                self._health_critical.discard(current.session_id)
            if current_pct <= self.health_low_percent and current.session_id not in self._health_low:
                self._health_low.add(current.session_id)
                events.append(self._event(current, EventType.HEALTH_LOW, EventPriority.P1_HIGH, {
                    "current_health": current.player.health, "max_health": current.player.max_health, "health_percent": current_pct,
                }, summary=f"Player health dropped below {self.health_low_percent:g}%", now=clock))
            if current_pct <= self.health_critical_percent and current.session_id not in self._health_critical:
                self._health_critical.add(current.session_id)
                events.append(self._event(current, EventType.HEALTH_CRITICAL, EventPriority.P0_CRITICAL, {
                    "current_health": current.player.health, "max_health": current.player.max_health, "health_percent": current_pct,
                }, summary=f"Player health dropped below {self.health_critical_percent:g}%", now=clock))

        if caps.player_position:
            self._update_idle(previous, current, clock, events)
        self._update_stuck(previous, current, clock, events)
        events.extend(self.deep_awareness.synthesize(previous, current, clock))
        return events

    def _update_idle(self, previous: GameState | None, current: GameState, now: datetime, events: list[GameEvent]) -> None:
        session = current.session_id
        moved = previous is None or previous.session_id != session or self._distance(previous, current) >= self.player_position_epsilon
        if moved:
            if session in self._idle_emitted:
                events.append(self._event(current, EventType.PLAYER_MOVED_AFTER_IDLE, EventPriority.P3_LOW, now=now))
            self._idle_emitted.discard(session)
            self._idle_since[session] = now
            return
        since = self._idle_since.setdefault(session, now)
        if session not in self._idle_emitted and (now - since).total_seconds() >= self.idle_timeout_seconds:
            self._idle_emitted.add(session)
            events.append(self._event(current, EventType.PLAYER_IDLE, EventPriority.P3_LOW, {"duration_seconds": self.idle_timeout_seconds}, now=now))

    def _update_stuck(self, previous: GameState | None, current: GameState, now: datetime, events: list[GameEvent]) -> None:
        session = current.session_id
        eligible = current.companion.present and current.companion.moving and current.companion.current_intent in {"follow", "regroup"}
        progressed = previous is None or previous.session_id != session or abs(previous.companion.distance_to_player - current.companion.distance_to_player) > self.movement_epsilon
        if not eligible or progressed:
            was_stuck = session in self._stuck_emitted
            self._stuck_since.pop(session, None)
            self._stuck_emitted.discard(session)
            if was_stuck and eligible:
                events.append(self._event(current, EventType.COMPANION_REGROUPED, EventPriority.P2_MEDIUM, {"reason": "movement_resumed"}, now=now))
            return
        since = self._stuck_since.setdefault(session, now)
        if session not in self._stuck_emitted and (now - since).total_seconds() >= self.stuck_seconds:
            self._stuck_emitted.add(session)
            events.append(self._event(current, EventType.COMPANION_STUCK, EventPriority.P1_HIGH, {"duration_seconds": self.stuck_seconds}, now=now))

    def expire_sessions(self, now: datetime | None = None) -> list[GameEvent]:
        clock = now or datetime.now(timezone.utc)
        events: list[GameEvent] = []
        for key, (last_seen, state) in list(self._last_seen.items()):
            if key in self._ended or (clock - last_seen).total_seconds() < self.session_timeout_seconds:
                continue
            self._ended.add(key)
            event = self._event(state, EventType.SESSION_ENDED, EventPriority.P2_MEDIUM, {"timeout_seconds": self.session_timeout_seconds}, now=clock)
            events.append(event)
            logger.info("session_ended session_id=%s source=%s", state.session_id, state.source)
        return events

    def is_stuck(self, session_id: str) -> bool:
        return session_id in self._stuck_emitted
