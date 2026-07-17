from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.priorities import EventPriority
from app.models.game_event import EventType, GameEvent
from app.models.reaction import SienaReaction
from app.models.scene import ReactionOpportunity, SceneContext, SceneUpdate


SituationState = Literal["unavailable", "loading", "idle", "exploration", "driving", "combat", "danger", "recovery"]
Urgency = Literal["none", "context", "recovery", "warning", "critical"]


class ContextEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    event_type: str
    created_at: datetime
    priority: EventPriority
    sequence: int | None = None
    reacted: bool = False


class ContextTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_type: Literal[
        "contextual_state_changed", "reaction_candidate_created", "reaction_candidate_grouped",
        "reaction_suppressed", "reaction_promoted", "reaction_replaced", "reaction_emitted",
    ]
    session_id: str | None = None
    event_id: str | None = None
    reason: str
    created_at: datetime


class ContextualStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    session_id: str | None = None
    situation_state: SituationState = "unavailable"
    reaction_urgency: Urgency = "none"
    primary_pending_event: str | None = None
    grouped_related_events: list[str] = Field(default_factory=list, max_length=8)
    queue_size: int = Field(default=0, ge=0)
    last_emitted_reaction_category: str | None = None
    last_suppression_reason: str | None = None
    event_window_size: int = Field(default=0, ge=0)


@dataclass(slots=True)
class ContextualDecision:
    opportunity: ReactionOpportunity | None
    traces: list[ContextTrace]
    reason: str | None = None


class ContextualCompanion:
    """Bounded, session-only arbitration over synthesized events and SceneContext."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        event_window_seconds: float = 60,
        event_window_max_items: int = 20,
        grouping_window_seconds: float = 3,
        low_priority_queue_limit: int = 3,
        recovery_voice_enabled: bool = False,
        build_aware_reactions_enabled: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.enabled = enabled
        self.event_window = timedelta(seconds=event_window_seconds)
        self.grouping_window = timedelta(seconds=grouping_window_seconds)
        self.low_priority_queue_limit = low_priority_queue_limit
        self.recovery_voice_enabled = recovery_voice_enabled
        self.build_aware_reactions_enabled = build_aware_reactions_enabled
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._events: deque[ContextEvent] = deque(maxlen=event_window_max_items)
        self._pending: deque[ReactionOpportunity] = deque(maxlen=low_priority_queue_limit)
        self._seen_event_ids: set[str] = set()
        self._session_id: str | None = None
        self._situation: SituationState = "unavailable"
        self._urgency: Urgency = "none"
        self._grouped: list[str] = []
        self._last_category: str | None = None
        self._last_suppression: str | None = None
        self._recently_recovered = False
        self._last_recovery_at: datetime | None = None

    def reset(self, session_id: str | None = None) -> None:
        self._events.clear()
        self._pending.clear()
        self._seen_event_ids.clear()
        self._session_id = session_id
        self._situation = "unavailable" if session_id is None else "loading"
        self._urgency = "none"
        self._grouped = []
        self._last_category = None
        self._last_suppression = None
        self._recently_recovered = False
        self._last_recovery_at = None

    def observe_scene(self, scene: SceneContext | None) -> list[ContextTrace]:
        previous = self._situation
        if scene is None or scene.player_available is False or scene.session_available is False:
            if self._session_id is not None:
                self.reset(None)
            current: SituationState = "unavailable"
        else:
            if scene.session_id != self._session_id:
                self.reset(scene.session_id)
            if scene.is_pre_game is not False:
                current = "loading"
            elif scene.health_state == "critical" or (scene.health_percent is not None and scene.health_percent <= 10):
                current = "danger"
            elif scene.phase == "recovery" or str(scene.health_trend) == "recovering" or (
                self._last_recovery_at is not None and self.clock() - self._last_recovery_at <= timedelta(seconds=15)
            ):
                current = "recovery"
            elif scene.combat_state is True:
                current = "combat"
            elif scene.vehicle_state is True:
                current = "driving"
            elif scene.phase == "idle":
                current = "idle"
            else:
                current = "exploration"
        self._situation = current
        if current != previous:
            return [self._trace("contextual_state_changed", f"{previous}_to_{current}")]
        return []

    def evaluate(self, update: SceneUpdate, opportunity: ReactionOpportunity | None, legacy_reason: str | None = None) -> ContextualDecision:
        event = update.normalized_event.event
        traces = self._remember_event(event)
        if not self.enabled:
            return ContextualDecision(opportunity, traces)
        scene = update.scene
        if scene is None or scene.player_available is False or scene.session_available is False:
            return self._suppress(event, traces, "player_unavailable")
        if scene.is_pre_game is True:
            return self._suppress(event, traces, "pregame")
        if opportunity is None:
            return self._suppress(event, traces, legacy_reason or "no_reaction_candidate")

        semantic = update.normalized_event.semantic_type
        traces.append(self._trace("reaction_candidate_created", semantic, event))
        if semantic in {EventType.WEAPON_DRAWN.value, EventType.WEAPON_HOLSTERED.value, EventType.WEAPON_CHANGED.value}:
            return self._suppress(event, traces, "absorbed_as_context")
        if semantic in {EventType.STATUS_EFFECTS_INCREASED.value, EventType.STATUS_EFFECTS_DECREASED.value}:
            return self._suppress(event, traces, "status_effect_low_confidence")
        if semantic == EventType.PLAYER_RAM_LOW.value and scene.combat_state is not True:
            return self._suppress(event, traces, "ram_warning_outside_combat")
        if semantic == EventType.PLAYER_RAM_EXHAUSTED.value and scene.combat_state is not True:
            return self._suppress(event, traces, "ram_warning_outside_combat")

        if semantic in {EventType.HEALTH_LOW.value, EventType.PLAYER_RAM_LOW.value}:
            if len(self._pending) >= self.low_priority_queue_limit:
                self._pending.popleft()
            self._pending.append(self._decorate(opportunity, scene, "warning", "voice_and_text"))
            self._urgency = "warning"
            self._last_suppression = "grouping_wait"
            traces.append(self._trace("reaction_candidate_grouped", "grouping_wait", event))
            return ContextualDecision(None, traces, "grouping_wait")

        related = self._related_types(event)
        if semantic in {EventType.HEALTH_CRITICAL.value, EventType.PLAYER_RAM_EXHAUSTED.value}:
            family = "health" if semantic == EventType.HEALTH_CRITICAL.value else "ram"
            retained = deque((item for item in self._pending if family not in str(item.trigger_event_type)), maxlen=self.low_priority_queue_limit)
            if len(retained) != len(self._pending):
                traces.append(self._trace("reaction_promoted", f"grouped_into_{semantic}", event))
                traces.append(self._trace("reaction_replaced", "lower_priority", event))
            self._pending = retained
        urgency: Urgency = "critical" if semantic == EventType.HEALTH_CRITICAL.value else self._urgency_for(opportunity)
        delivery = self._delivery_for(opportunity)
        decorated = self._decorate(opportunity, scene, urgency, delivery, related)
        self._urgency = urgency
        self._grouped = related
        self._last_suppression = None
        return ContextualDecision(decorated, traces)

    def flush_due(self, now: datetime | None = None) -> list[ReactionOpportunity]:
        current = now or self.clock()
        ready: list[ReactionOpportunity] = []
        while self._pending and current - self._pending[0].trigger_event.created_at >= self.grouping_window:
            candidate = self._pending.popleft()
            if current > candidate.expires_at:
                self._last_suppression = "stale_candidate"
            else:
                ready.append(candidate)
        return ready

    def mark_emitted(self, opportunity: ReactionOpportunity) -> ContextTrace:
        category = opportunity.reaction_category or str(opportunity.trigger_event_type)
        self._last_category = category
        self._urgency = "none"
        for item in self._events:
            if item.event_id == opportunity.trigger_event_id:
                item.reacted = True
        return self._trace("reaction_emitted", category, opportunity.trigger_event)

    def mark_reaction(self, reaction: SienaReaction) -> ContextTrace:
        category = str(reaction.metadata.get("reaction_category") or self._category_from_event_types(str(reaction.event_type)))
        self._last_category = category
        self._urgency = "none"
        for item in self._events:
            if item.event_id == reaction.event_id:
                item.reacted = True
        return ContextTrace(trace_type="reaction_emitted", session_id=reaction.session_id, event_id=reaction.event_id, reason=category, created_at=reaction.created_at)

    def status(self) -> ContextualStatus:
        self._expire(self.clock())
        return ContextualStatus(
            enabled=self.enabled, session_id=self._session_id, situation_state=self._situation,
            reaction_urgency=self._urgency,
            primary_pending_event=str(self._pending[0].trigger_event_type) if self._pending else None,
            grouped_related_events=self._grouped[:8], queue_size=len(self._pending),
            last_emitted_reaction_category=self._last_category,
            last_suppression_reason=self._last_suppression, event_window_size=len(self._events),
        )

    def _remember_event(self, event: GameEvent) -> list[ContextTrace]:
        if event.session_id != self._session_id:
            self.reset(event.session_id)
        self._expire(event.created_at)
        if event.event_id in self._seen_event_ids:
            return []
        self._seen_event_ids.add(event.event_id)
        self._events.append(ContextEvent(event_id=event.event_id, event_type=str(event.event_type), created_at=event.created_at, priority=event.priority, sequence=event.sequence))
        if event.event_type in {EventType.PLAYER_HEALED, EventType.PLAYER_RAM_RECOVERED, EventType.PLAYER_RECOVERED}:
            self._recently_recovered = True
            self._last_recovery_at = event.created_at
        if event.event_type == EventType.SESSION_ENDED:
            self.reset(None)
        return []

    def _expire(self, now: datetime) -> None:
        while self._events and now - self._events[0].created_at > self.event_window:
            expired = self._events.popleft()
            self._seen_event_ids.discard(expired.event_id)

    def _related_types(self, event: GameEvent) -> list[str]:
        return [item.event_type for item in self._events if item.event_id != event.event_id and timedelta(0) <= event.created_at - item.created_at <= self.grouping_window][-8:]

    def _decorate(self, opportunity: ReactionOpportunity, scene: SceneContext, urgency: Urgency, delivery: str, related: list[str] | None = None) -> ReactionOpportunity:
        category = self._category(opportunity)
        tactical_context = self._tactical_context(scene, urgency, related or [])
        tactical_context["repeated_category"] = self._last_category == category
        return opportunity.model_copy(update={
            "related_event_types": (related or [])[:8], "tactical_context": tactical_context,
            "delivery_hint": delivery, "reaction_category": category,
        })

    def _tactical_context(self, scene: SceneContext, urgency: Urgency, related: list[str]) -> dict:
        build_style = scene.build_style
        build_confidence = scene.build_confidence
        limitations = list(scene.build_limitations[:8])
        if not self.build_aware_reactions_enabled:
            build_style = build_confidence = None
        elif build_confidence is None or build_confidence < 0.6:
            build_style = None
            limitations = (limitations + ["build_profile_low_confidence"])[:8]
        return {
            "situation_state": self._situation, "in_combat": scene.combat_state, "driving": scene.vehicle_state,
            "health_state": scene.health_state, "health_percent": scene.health_percent,
            "ram_state": scene.ram_state, "ram_percent": scene.ram_percent,
            "active_weapon_record_id": scene.active_weapon_record_id,
            "build_style": build_style, "build_confidence": build_confidence,
            "cyberdeck_record_id": scene.cyberdeck_record_id,
            "installed_quickhack_record_ids": scene.installed_quickhack_record_ids[:8],
            "recent_event_types": [item.event_type for item in self._events][-8:],
            "related_event_types": related[:8], "recently_recovered": self._recently_recovered,
            "reaction_urgency": urgency, "known_limitations": limitations,
        }

    def _delivery_for(self, opportunity: ReactionOpportunity) -> str:
        if opportunity.focus and opportunity.focus.value in {"recovery_comment", "resource_recovery"}:
            return "voice_and_text" if self.recovery_voice_enabled else "text_only"
        if opportunity.priority in {EventPriority.P0_CRITICAL, EventPriority.P1_HIGH}:
            return "voice_and_text"
        return "text_only"

    @staticmethod
    def _urgency_for(opportunity: ReactionOpportunity) -> Urgency:
        if opportunity.priority == EventPriority.P0_CRITICAL:
            return "critical"
        if opportunity.priority == EventPriority.P1_HIGH:
            return "warning"
        if opportunity.focus and opportunity.focus.value in {"recovery_comment", "resource_recovery"}:
            return "recovery"
        return "context"

    @staticmethod
    def _category(opportunity: ReactionOpportunity) -> str:
        return ContextualCompanion._category_from_event_types(str(opportunity.trigger_event_type))

    @staticmethod
    def _category_from_event_types(event_type: str) -> str:
        if "health" in event_type or event_type in {"player_damaged", "player_healed"}:
            return "health"
        if "ram" in event_type:
            return "ram"
        if "vehicle" in event_type:
            return "vehicle"
        if "weapon" in event_type:
            return "weapon"
        if "combat" in event_type:
            return "combat"
        return event_type

    def _suppress(self, event: GameEvent, traces: list[ContextTrace], reason: str) -> ContextualDecision:
        self._last_suppression = reason
        traces.append(self._trace("reaction_suppressed", reason, event))
        return ContextualDecision(None, traces, reason)

    def _trace(self, trace_type: str, reason: str, event: GameEvent | None = None) -> ContextTrace:
        return ContextTrace(trace_type=trace_type, session_id=event.session_id if event else self._session_id, event_id=event.event_id if event else None, reason=reason, created_at=event.created_at if event else self.clock())
