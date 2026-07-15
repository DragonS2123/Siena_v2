import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.domain.priorities import EventPriority
from app.models.game_event import EventType
from app.models.scene import (
    HealthTrend,
    ReactionFocus,
    ReactionOpportunity,
    ScenePhase,
    SceneUpdate,
)

logger = logging.getLogger("siena_observer.scene_policy")


class CompanionBehaviorPolicy:
    def __init__(
        self,
        *,
        max_reactions: int = 3,
        min_reaction_interval_seconds: float = 15,
        critical_bypass: bool = True,
        resolution_enabled: bool = True,
        opportunity_ttl_seconds: float = 45,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.max_reactions = max_reactions
        self.min_interval = timedelta(seconds=min_reaction_interval_seconds)
        self.critical_bypass = critical_bypass
        self.resolution_enabled = resolution_enabled
        self.opportunity_ttl = timedelta(seconds=opportunity_ttl_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._reserved: dict[str, int] = {}
        self._last_at: dict[str, datetime] = {}
        self._resolution_scenes: set[str] = set()
        self._last_critical_at: dict[str, datetime] = {}
        self.last_suppression_reason: str | None = None

    def evaluate(self, update: SceneUpdate) -> ReactionOpportunity | None:
        self.last_suppression_reason = None
        scene = update.scene
        normalized = update.normalized_event
        event = normalized.event
        if not scene:
            return self._suppress(event.event_id, "no_meaningful_change")
        if normalized.duplicate_semantic_event:
            return self._suppress(event.event_id, "duplicate_semantic_event")
        if normalized.diagnostic_only:
            return self._suppress(event.event_id, "no_meaningful_change")

        semantic = normalized.semantic_type
        focus: ReactionFocus | None = None
        reason = "scene_update"
        priority = event.priority

        if semantic == EventType.SESSION_STARTED.value:
            focus = ReactionFocus.SESSION_GREETING
            reason = "session_started"
        elif semantic == EventType.HEALTH_CRITICAL.value:
            focus = ReactionFocus.DANGER_WARNING
            priority = EventPriority.P0_CRITICAL
            reason = "critical_health"
        elif semantic == EventType.HEALTH_LOW.value and (scene.health_percent is None or scene.health_percent <= 25):
            focus = ReactionFocus.DANGER_WARNING
            priority = EventPriority.P1_HIGH
            reason = "low_health"
        elif semantic == EventType.PLAYER_DAMAGED.value:
            damage = float(event.payload.get("total_damage", event.payload.get("damage_amount", 0)))
            maximum = float(event.payload.get("max_health", scene.health_max or 100))
            if damage < max(5.0, maximum * 0.1) and scene.health_trend != HealthTrend.RAPIDLY_FALLING:
                return self._suppress(event.event_id, "no_meaningful_change")
            focus = ReactionFocus.COMBAT_COMMENT
            reason = "rapid_damage" if scene.health_trend == HealthTrend.RAPIDLY_FALLING else "meaningful_damage"
        elif semantic == EventType.COMBAT_STARTED.value:
            if scene.combat_state is not True:
                return self._suppress(event.event_id, "capability_unavailable")
            focus = ReactionFocus.COMBAT_COMMENT
            reason = "combat_started"
        elif semantic == EventType.PLAYER_HEALED.value:
            critical_at = self._last_critical_at.get(scene.scene_id)
            if critical_at and event.created_at - critical_at < self.min_interval:
                return self._suppress(event.event_id, "weak_after_critical")
            if scene.phase != ScenePhase.RECOVERY:
                return self._suppress(event.event_id, "no_meaningful_change")
            focus = ReactionFocus.RECOVERY_COMMENT
            reason = "recovery"
        elif semantic == EventType.COMBAT_ENDED.value:
            if scene.combat_state is not False:
                return self._suppress(event.event_id, "capability_unavailable")
            if not self.resolution_enabled or scene.scene_id in self._resolution_scenes:
                return self._suppress(event.event_id, "recent_reaction")
            focus = ReactionFocus.SCENE_RESOLUTION
            reason = "combat_resolved"
        elif semantic == EventType.VEHICLE_ENTERED.value:
            if scene.vehicle_state is not True:
                return self._suppress(event.event_id, "capability_unavailable")
            focus = ReactionFocus.VEHICLE_COMMENT
            reason = "vehicle_entered"
        elif semantic == EventType.PLAYER_IDLE.value:
            focus = ReactionFocus.IDLE_COMMENT
            reason = "player_idle"
        else:
            return self._suppress(event.event_id, "no_meaningful_change")

        critical = priority == EventPriority.P0_CRITICAL
        reserved = self._reserved.get(scene.scene_id, 0)
        if reserved >= self.max_reactions and not (critical and self.critical_bypass):
            logger.info("scene_reaction_budget_exhausted scene_id=%s event_id=%s", scene.scene_id, event.event_id)
            return self._suppress(event.event_id, "scene_budget_exhausted")
        last_at = self._last_at.get(scene.scene_id)
        if last_at and event.created_at - last_at < self.min_interval and not (critical and self.critical_bypass):
            return self._suppress(event.event_id, "recent_reaction")

        self._reserved[scene.scene_id] = reserved + 1
        self._last_at[scene.scene_id] = event.created_at
        if critical:
            self._last_critical_at[scene.scene_id] = event.created_at
        if focus == ReactionFocus.SCENE_RESOLUTION:
            self._resolution_scenes.add(scene.scene_id)
        opportunity = ReactionOpportunity(
            session_id=scene.session_id,
            scene_id=scene.scene_id,
            scene_revision=scene.revision,
            trigger_event_id=event.event_id,
            trigger_event_type=semantic,
            focus=focus,
            priority=priority,
            reason=reason,
            created_at=event.created_at,
            expires_at=event.created_at + self.opportunity_ttl,
            scene_snapshot=scene.model_copy(deep=True),
            trigger_event=event,
            suppresses_event_ids=[item.event_id for item in scene.recent_events[:-1] if item.semantic_type == semantic],
        )
        logger.info(
            "reaction_opportunity_created opportunity_id=%s scene_id=%s focus=%s priority=%s",
            opportunity.opportunity_id, scene.scene_id, focus, priority,
        )
        return opportunity

    def _suppress(self, event_id: str, reason: str) -> None:
        self.last_suppression_reason = reason
        logger.info("reaction_opportunity_suppressed event_id=%s reason=%s", event_id, reason)
        return None
