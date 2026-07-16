import logging
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.game_state import GameState
from app.models.scene import (
    HealthTrend,
    NormalizedSceneEvent,
    SceneContext,
    SceneEventSummary,
    ScenePhase,
    SceneUpdate,
)
from app.services.scene_event_normalizer import SceneEventNormalizer
from app.services.deep_game_state_awareness import DeepGameStateAwareness

logger = logging.getLogger("siena_observer.scene")

SEVERITY_RANK = {
    EventSeverity.INFO: 0,
    EventSeverity.LOW: 1,
    EventSeverity.MEDIUM: 2,
    EventSeverity.HIGH: 3,
    EventSeverity.CRITICAL: 4,
}


class SceneContextBuilder:
    def __init__(
        self,
        *,
        enabled: bool = True,
        event_history_limit: int = 20,
        scene_history_limit: int = 50,
        idle_gap_seconds: float = 30,
        max_duration_seconds: float = 180,
        recent_event_window_seconds: float = 45,
        clock: Callable[[], datetime] | None = None,
        normalizer: SceneEventNormalizer | None = None,
    ) -> None:
        self.enabled = enabled
        self.event_history_limit = event_history_limit
        self.idle_gap_seconds = idle_gap_seconds
        self.max_duration_seconds = max_duration_seconds
        self.recent_event_window_seconds = recent_event_window_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.normalizer = normalizer or SceneEventNormalizer()
        self._current: SceneContext | None = None
        self._history: deque[SceneContext] = deque(maxlen=scene_history_limit)

    def current(self) -> SceneContext | None:
        return self._current.model_copy(deep=True) if self._current else None

    def history(
        self,
        limit: int = 50,
        session_id: str | None = None,
        phase: ScenePhase | None = None,
    ) -> list[SceneContext]:
        items = reversed(self._history)
        return [
            item.model_copy(deep=True)
            for item in items
            if (not session_id or item.session_id == session_id) and (not phase or item.phase == phase)
        ][:limit]

    def record_reaction(self, scene_id: str) -> None:
        if self._current and self._current.scene_id == scene_id:
            self._current.reaction_count += 1

    def observe_state(self, state: GameState, capabilities: BridgeCapabilities | None = None) -> bool:
        """Refresh nullable awareness fields without turning every telemetry packet into an event."""
        scene = self._current
        deep = state.deep_game_state
        if not self.enabled or scene is None or scene.session_id != state.session_id or deep is None:
            return False
        before = self._deep_fingerprint(scene)
        player = deep.player
        scene.player_available = player.entity_available if player else None
        scene.session_available = player.session_available if player else None
        scene.is_pre_game = player.is_pre_game if player else None

        health = DeepGameStateAwareness.health_resource(state)
        if health:
            scene.health_current, scene.health_maximum, scene.health_percent = health
            scene.health_max = scene.health_maximum
            scene.health_state = DeepGameStateAwareness.health_label(scene.health_percent)
        else:
            scene.health_current = scene.health_max = scene.health_maximum = scene.health_percent = None
            scene.health_state = None

        ram = DeepGameStateAwareness.ram_resource(state)
        if ram:
            scene.ram_current, scene.ram_maximum, scene.ram_percent = ram
            scene.ram_state = DeepGameStateAwareness.ram_label(scene.ram_percent)
        else:
            scene.ram_current = scene.ram_maximum = scene.ram_percent = None
            scene.ram_state = None

        weapon = deep.weapon
        scene.active_weapon_record_id = DeepGameStateAwareness.stable_weapon_id(weapon.record_id) if weapon else None
        scene.weapon_drawn = weapon.drawn if weapon else None
        effects = deep.status_effects
        scene.status_effect_count = effects.observed_count if effects else None
        stats = deep.stats
        scene.level = stats.level if stats else None
        scene.street_cred = stats.street_cred if stats else None
        scene.armor = stats.armor if stats else None
        scene.vehicle_state = state.player.in_vehicle if (capabilities or scene.capabilities).vehicle_state else scene.vehicle_state
        if capabilities is not None:
            scene.capabilities = capabilities
        scene.notable_facts = self._facts(scene)
        changed = before != self._deep_fingerprint(scene)
        if changed:
            scene.updated_at = state.captured_at
            scene.revision += 1
            logger.info("scene_deep_state_updated scene_id=%s revision=%s", scene.scene_id, scene.revision)
        return changed

    def apply(
        self,
        event: GameEvent,
        capabilities: BridgeCapabilities | None = None,
    ) -> SceneUpdate:
        normalized = self.normalizer.normalize(event)
        if not self.enabled:
            return SceneUpdate(scene=None, normalized_event=normalized)
        caps = capabilities or (
            self._current.capabilities.model_copy(deep=True)
            if self._current and self._current.session_id == event.session_id
            else BridgeCapabilities(
                player_health=True,
                player_position=True,
                combat_state=True,
                vehicle_state=True,
                pause_state=True,
                district=True,
            )
        )
        event_type = normalized.semantic_type
        now = event.created_at
        started = False
        closed = False

        if self._current and self._current.session_id != event.session_id:
            self._close(now, ScenePhase.SESSION_END, "Session changed")
            closed = True

        if normalized.diagnostic_only:
            return SceneUpdate(
                scene=self.current(),
                normalized_event=normalized,
                significant=False,
                closed=closed,
            )

        if event_type == EventType.SESSION_ENDED.value:
            if not self._current:
                self._start(event, caps, ScenePhase.SESSION_END)
            else:
                self._apply_event(self._current, normalized, caps)
                self._set_phase(self._current, ScenePhase.SESSION_END)
            scene = self._current.model_copy(deep=True) if self._current else None
            self._close(now, ScenePhase.SESSION_END, "Game session ended")
            logger.info("scene_closed session_id=%s reason=session_ended", event.session_id)
            return SceneUpdate(scene=scene, normalized_event=normalized, significant=True, closed=True)

        desired_phase = self._phase_for(normalized, caps)
        if self._needs_new_scene(event, desired_phase):
            if self._current:
                self._close(now, self._current.phase, "Scene boundary reached")
                closed = True
            self._start(event, caps, desired_phase)
            started = True

        assert self._current is not None
        duplicate = self._is_duplicate(self._current, normalized)
        normalized.duplicate_semantic_event = duplicate
        if duplicate:
            logger.info(
                "reaction_opportunity_suppressed event_id=%s reason=duplicate_semantic_event semantic_type=%s",
                event.event_id,
                event_type,
            )
            return SceneUpdate(scene=self.current(), normalized_event=normalized, started=started, closed=closed)

        before = self._fingerprint(self._current)
        self._apply_event(self._current, normalized, caps)
        if desired_phase != ScenePhase.UNKNOWN:
            self._set_phase(self._current, desired_phase)
        self._current.summary = self._summary(self._current)
        significant = started or before != self._fingerprint(self._current)
        if significant and not started:
            self._current.revision += 1
            logger.info("scene_updated scene_id=%s revision=%s", self._current.scene_id, self._current.revision)
        return SceneUpdate(
            scene=self.current(), normalized_event=normalized,
            significant=significant, started=started, closed=closed,
        )

    def is_request_relevant(self, scene_id: str, revision: int, focus: str | None) -> bool:
        current = self._current
        if not current or current.scene_id != scene_id:
            return False
        if current.revision <= revision:
            return True
        if focus == "danger_warning":
            return current.phase == ScenePhase.DANGER and current.health_trend in {HealthTrend.CRITICAL, HealthTrend.RAPIDLY_FALLING}
        if focus == "combat_comment":
            return current.phase == ScenePhase.COMBAT and current.combat_state is True
        if focus == "recovery_comment":
            return current.phase == ScenePhase.RECOVERY
        if focus == "scene_resolution":
            return current.phase == ScenePhase.TRANSITION
        return current.revision - revision <= 1

    def _needs_new_scene(self, event: GameEvent, phase: ScenePhase) -> bool:
        current = self._current
        if not current:
            return True
        if event.event_type == EventType.SESSION_STARTED:
            return True
        gap = (event.created_at - current.last_meaningful_event_at).total_seconds()
        duration = (event.created_at - current.started_at).total_seconds()
        if gap >= self.idle_gap_seconds or duration >= self.max_duration_seconds:
            return True
        if phase == ScenePhase.COMBAT and current.phase not in {ScenePhase.COMBAT, ScenePhase.DANGER, ScenePhase.RECOVERY}:
            return True
        if phase == ScenePhase.VEHICLE and current.phase != ScenePhase.VEHICLE:
            return True
        if phase == ScenePhase.IDLE and current.phase != ScenePhase.IDLE:
            return True
        if phase == ScenePhase.EXPLORATION and current.phase in {ScenePhase.IDLE, ScenePhase.VEHICLE}:
            return True
        return False

    def _start(self, event: GameEvent, caps: BridgeCapabilities, phase: ScenePhase) -> None:
        now = event.created_at
        self._current = SceneContext(
            session_id=event.session_id,
            source=event.source,
            phase=phase,
            started_at=now,
            updated_at=now,
            last_meaningful_event_at=now,
            capabilities=caps,
            combat_state=False if caps.combat_state else None,
            vehicle_state=False if caps.vehicle_state else None,
            summary="Game session started" if phase == ScenePhase.SESSION_START else "Scene started",
        )
        logger.info("scene_started scene_id=%s session_id=%s phase=%s", self._current.scene_id, event.session_id, phase)

    def _close(self, now: datetime, phase: ScenePhase, summary: str) -> None:
        if not self._current:
            return
        self._current.previous_phase = self._current.phase
        self._current.phase = phase
        self._current.updated_at = now
        self._current.closed_at = now
        self._current.summary = summary
        self._current.revision += 1
        self._history.append(self._current.model_copy(deep=True))
        logger.info("scene_closed scene_id=%s phase=%s", self._current.scene_id, phase)
        self._current = None

    def _is_duplicate(self, scene: SceneContext, normalized: NormalizedSceneEvent) -> bool:
        event = normalized.event
        return any(
            item.semantic_type == normalized.semantic_type
            and (
                (item.sequence is not None and event.sequence is not None and item.sequence == event.sequence)
                or abs((item.created_at - event.created_at).total_seconds()) < 0.001
            )
            for item in scene.recent_events
        )

    def _apply_event(self, scene: SceneContext, normalized: NormalizedSceneEvent, caps: BridgeCapabilities) -> None:
        event = normalized.event
        scene.updated_at = event.created_at
        scene.last_meaningful_event_at = event.created_at
        scene.capabilities = caps
        scene.event_count += 1
        scene.event_ids = [*scene.event_ids, event.event_id][-self.event_history_limit:]
        cutoff = event.created_at.timestamp() - self.recent_event_window_seconds
        recent = [item for item in scene.recent_events if item.created_at.timestamp() >= cutoff]
        scene.recent_events = [*recent, SceneEventSummary(
            event_id=event.event_id,
            event_type=str(event.event_type),
            semantic_type=normalized.semantic_type,
            created_at=event.created_at,
            severity=event.severity or EventSeverity.MEDIUM,
            summary=event.summary,
            sequence=event.sequence,
        )][-self.event_history_limit:]
        scene.primary_event_type = normalized.semantic_type
        severity = event.severity or EventSeverity.MEDIUM
        scene.severity = severity
        if SEVERITY_RANK[severity] > SEVERITY_RANK[scene.peak_severity]:
            scene.peak_severity = severity

        payload = event.payload
        if "player_available" in payload:
            scene.player_available = payload["player_available"]
        if "session_available" in payload:
            scene.session_available = payload["session_available"]
        if "is_pre_game" in payload:
            scene.is_pre_game = payload["is_pre_game"]
        previous_pct = scene.health_percent
        if "current_health" in payload:
            scene.health_current = float(payload["current_health"])
        if "max_health" in payload:
            scene.health_max = float(payload["max_health"])
            scene.health_maximum = scene.health_max
        if "health_percent" in payload:
            scene.health_percent = float(payload["health_percent"])
        if "health_state" in payload:
            scene.health_state = str(payload["health_state"])
        if "current_ram" in payload:
            scene.ram_current = float(payload["current_ram"])
        if "max_ram" in payload:
            scene.ram_maximum = float(payload["max_ram"])
        if "ram_percent" in payload:
            scene.ram_percent = float(payload["ram_percent"])
        if "ram_state" in payload:
            scene.ram_state = str(payload["ram_state"])
        if "active_weapon_record_id" in payload:
            scene.active_weapon_record_id = payload["active_weapon_record_id"]
        if "weapon_drawn" in payload:
            scene.weapon_drawn = bool(payload["weapon_drawn"])
        if "status_effect_count" in payload:
            scene.status_effect_count = int(payload["status_effect_count"])
        if normalized.semantic_type == EventType.PLAYER_DAMAGED.value:
            scene.total_damage += float(payload.get("total_damage", payload.get("damage_amount", 0)))
            scene.damage_hits += int(payload.get("hits", 1))
            drop = (previous_pct - scene.health_percent) if previous_pct is not None and scene.health_percent is not None else float(payload.get("damage_amount", 0))
            scene.health_trend = HealthTrend.RAPIDLY_FALLING if drop >= 20 or int(payload.get("hits", 1)) >= 3 else HealthTrend.FALLING
        elif normalized.semantic_type == EventType.HEALTH_CRITICAL.value:
            scene.health_trend = HealthTrend.CRITICAL
        elif normalized.semantic_type == EventType.PLAYER_HEALED.value:
            scene.total_healing += float(payload.get("healed_amount", 0))
            scene.health_trend = HealthTrend.RECOVERING
        elif scene.health_percent is not None and scene.health_percent <= 10:
            scene.health_trend = HealthTrend.CRITICAL
        elif previous_pct is not None and scene.health_percent is not None and scene.health_percent == previous_pct:
            scene.health_trend = HealthTrend.STABLE

        if normalized.semantic_type == EventType.COMBAT_STARTED.value and caps.combat_state:
            scene.combat_state = True
        elif normalized.semantic_type == EventType.COMBAT_ENDED.value and caps.combat_state:
            scene.combat_state = False
        if normalized.semantic_type == EventType.VEHICLE_ENTERED.value and caps.vehicle_state:
            scene.vehicle_state = True
        elif normalized.semantic_type == EventType.VEHICLE_EXITED.value and caps.vehicle_state:
            scene.vehicle_state = False
        if normalized.semantic_type == EventType.PLAYER_IDLE.value:
            scene.idle_seconds = float(payload.get("duration_seconds", 0))
        elif normalized.semantic_type == EventType.PLAYER_MOVED_AFTER_IDLE.value:
            scene.idle_seconds = 0
        scene.notable_facts = self._facts(scene)

    def _phase_for(self, normalized: NormalizedSceneEvent, caps: BridgeCapabilities) -> ScenePhase:
        event_type = normalized.semantic_type
        pct = normalized.event.payload.get("health_percent")
        if event_type == EventType.SESSION_STARTED.value:
            return ScenePhase.SESSION_START
        if event_type == EventType.COMBAT_STARTED.value:
            return ScenePhase.COMBAT if caps.combat_state else ScenePhase.UNKNOWN
        if event_type == EventType.COMBAT_ENDED.value:
            return ScenePhase.TRANSITION if caps.combat_state else ScenePhase.UNKNOWN
        if event_type == EventType.HEALTH_CRITICAL.value:
            return ScenePhase.DANGER
        if event_type == EventType.HEALTH_LOW.value and (pct is None or float(pct) <= 25):
            return ScenePhase.DANGER
        if event_type == EventType.PLAYER_HEALED.value and self._current and self._current.phase == ScenePhase.DANGER:
            return ScenePhase.RECOVERY
        if event_type == EventType.VEHICLE_ENTERED.value:
            return ScenePhase.VEHICLE if caps.vehicle_state else ScenePhase.UNKNOWN
        if event_type == EventType.PLAYER_IDLE.value:
            return ScenePhase.IDLE
        if event_type == EventType.PLAYER_MOVED_AFTER_IDLE.value:
            return ScenePhase.EXPLORATION
        return self._current.phase if self._current else ScenePhase.UNKNOWN

    @staticmethod
    def _set_phase(scene: SceneContext, phase: ScenePhase) -> None:
        if phase == scene.phase:
            return
        previous = scene.phase
        scene.previous_phase = previous
        scene.phase = phase
        logger.info("scene_phase_changed scene_id=%s previous=%s current=%s", scene.scene_id, previous, phase)

    @staticmethod
    def _fingerprint(scene: SceneContext) -> tuple:
        return (
            scene.phase, scene.severity, scene.health_trend,
            round(scene.health_percent, 1) if scene.health_percent is not None else None,
            scene.health_state,
            round(scene.ram_percent, 1) if scene.ram_percent is not None else None,
            scene.ram_state, scene.active_weapon_record_id, scene.weapon_drawn,
            scene.status_effect_count, scene.level, scene.street_cred, scene.armor,
            scene.combat_state, scene.vehicle_state,
            round(scene.total_damage, 1), round(scene.total_healing, 1), scene.damage_hits,
        )

    @staticmethod
    def _deep_fingerprint(scene: SceneContext) -> tuple:
        return (
            scene.health_current, scene.health_maximum, scene.health_percent, scene.health_state,
            scene.ram_current, scene.ram_maximum, scene.ram_percent, scene.ram_state,
            scene.active_weapon_record_id, scene.weapon_drawn, scene.status_effect_count,
            scene.level, scene.street_cred, scene.armor,
            scene.player_available, scene.session_available, scene.is_pre_game, scene.vehicle_state,
        )

    @staticmethod
    def _facts(scene: SceneContext) -> list[str]:
        facts = []
        if scene.combat_state is True:
            facts.append("combat is active")
        if scene.health_percent is not None:
            facts.append(f"health is {scene.health_percent:.0f}%")
        if scene.ram_percent is not None:
            facts.append(f"RAM is {scene.ram_percent:.0f}%")
        if scene.damage_hits:
            facts.append(f"{scene.damage_hits} damage hits")
        if scene.vehicle_state is True:
            facts.append("player is in a vehicle")
        if scene.weapon_drawn is True and scene.active_weapon_record_id:
            facts.append(f"weapon is drawn: {scene.active_weapon_record_id}")
        if scene.status_effect_count is not None:
            facts.append(f"status effect count is {scene.status_effect_count}")
        return facts[:6]

    @staticmethod
    def _summary(scene: SceneContext) -> str:
        if scene.phase == ScenePhase.DANGER:
            return "Combat escalated into critical health danger" if scene.combat_state else "Health became critical"
        if scene.phase == ScenePhase.RECOVERY:
            return "Health is recovering after danger"
        if scene.phase == ScenePhase.COMBAT:
            return "Combat is active"
        if scene.phase == ScenePhase.TRANSITION:
            return "The immediate scene is resolving"
        if scene.phase == ScenePhase.VEHICLE:
            return "Player entered a vehicle context"
        if scene.phase == ScenePhase.IDLE:
            return "Player has been idle"
        if scene.phase == ScenePhase.SESSION_START:
            return "Game observation started"
        return "Exploration continues"
