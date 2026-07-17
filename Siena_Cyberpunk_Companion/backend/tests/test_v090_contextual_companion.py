from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.priorities import EventPriority
from app.models.game_event import EventType, GameEvent
from app.models.scene import HealthTrend, ReactionFocus, ReactionOpportunity, SceneContext, ScenePhase, SceneUpdate
from app.services.contextual_companion import ContextualCompanion
from app.services.reaction_planner import TemplateReactionProvider
from app.services.scene_event_normalizer import SceneEventNormalizer


NOW = datetime(2026, 7, 17, 15, tzinfo=timezone.utc)


def scene(**patches) -> SceneContext:
    values = dict(
        session_id="context-session", source="cet", phase=ScenePhase.EXPLORATION,
        started_at=NOW, updated_at=NOW, last_meaningful_event_at=NOW,
        player_available=True, session_available=True, is_pre_game=False,
        health_state="normal", health_percent=100.0, health_trend=HealthTrend.STABLE,
        ram_state="normal", ram_percent=100.0, combat_state=False, vehicle_state=False,
        summary="Exploration",
    )
    values.update(patches)
    return SceneContext(**values)


def event(kind: EventType, seconds: float = 0, priority: EventPriority = EventPriority.P2_MEDIUM, sequence: int | None = None) -> GameEvent:
    return GameEvent(session_id="context-session", event_type=kind, priority=priority, created_at=NOW + timedelta(seconds=seconds), sequence=sequence, summary=str(kind), deduplication_key=f"{kind}-{seconds}")


def update(current: SceneContext, current_event: GameEvent) -> SceneUpdate:
    return SceneUpdate(scene=current, normalized_event=SceneEventNormalizer().normalize(current_event))


def opportunity(current: SceneContext, current_event: GameEvent, focus: ReactionFocus = ReactionFocus.COMBAT_COMMENT) -> ReactionOpportunity:
    return ReactionOpportunity(session_id=current.session_id, scene_id=current.scene_id, scene_revision=current.revision, trigger_event_id=current_event.event_id, trigger_event_type=str(current_event.event_type), focus=focus, priority=current_event.priority, reason="test", created_at=current_event.created_at, expires_at=current_event.created_at + timedelta(seconds=45), scene_snapshot=current, trigger_event=current_event)


def evaluate(service: ContextualCompanion, current: SceneContext, current_event: GameEvent, focus: ReactionFocus = ReactionFocus.COMBAT_COMMENT):
    return service.evaluate(update(current, current_event), opportunity(current, current_event, focus))


def test_unavailable_loading_exploration_driving_combat_and_danger_states():
    service = ContextualCompanion(clock=lambda: NOW)
    service.observe_scene(None)
    assert service.status().situation_state == "unavailable"
    service.observe_scene(scene(is_pre_game=True))
    assert service.status().situation_state == "loading"
    service.observe_scene(scene())
    assert service.status().situation_state == "exploration"
    service.observe_scene(scene(vehicle_state=True))
    assert service.status().situation_state == "driving"
    service.observe_scene(scene(combat_state=True))
    assert service.status().situation_state == "combat"
    service.observe_scene(scene(combat_state=True, health_state="critical", health_percent=8))
    assert service.status().situation_state == "danger"


def test_idle_and_recent_recovery_are_session_bounded():
    now = [NOW]
    service = ContextualCompanion(clock=lambda: now[0])
    service.observe_scene(scene(phase=ScenePhase.IDLE))
    assert service.status().situation_state == "idle"
    healed = event(EventType.PLAYER_HEALED)
    evaluate(service, scene(phase=ScenePhase.RECOVERY, health_trend=HealthTrend.RECOVERING), healed, ReactionFocus.RECOVERY_COMMENT)
    service.observe_scene(scene())
    assert service.status().situation_state == "recovery"
    now[0] += timedelta(seconds=16)
    service.observe_scene(scene())
    assert service.status().situation_state == "exploration"


def test_health_low_then_critical_groups_and_critical_is_immediate():
    service = ContextualCompanion(clock=lambda: NOW)
    current = scene(combat_state=True, health_state="low", health_percent=20)
    low = event(EventType.HEALTH_LOW, priority=EventPriority.P1_HIGH)
    assert evaluate(service, current, low, ReactionFocus.DANGER_WARNING).opportunity is None
    assert service.status().queue_size == 1
    critical = event(EventType.HEALTH_CRITICAL, 1, EventPriority.P0_CRITICAL)
    decision = evaluate(service, scene(combat_state=True, health_state="critical", health_percent=8), critical, ReactionFocus.DANGER_WARNING)
    assert decision.opportunity is not None and decision.opportunity.delivery_hint == "voice_and_text"
    assert EventType.HEALTH_LOW.value in decision.opportunity.related_event_types
    assert service.status().queue_size == 0


def test_ram_low_then_exhausted_groups_only_in_combat():
    service = ContextualCompanion(clock=lambda: NOW)
    low = event(EventType.PLAYER_RAM_LOW, priority=EventPriority.P2_MEDIUM)
    outside = evaluate(service, scene(ram_state="low", ram_percent=20), low, ReactionFocus.RAM_WARNING)
    assert outside.reason == "ram_warning_outside_combat"
    service.reset("context-session")
    assert evaluate(service, scene(combat_state=True, ram_state="low", ram_percent=20), low, ReactionFocus.RAM_WARNING).opportunity is None
    exhausted_event = event(EventType.PLAYER_RAM_EXHAUSTED, 1, EventPriority.P1_HIGH)
    exhausted = evaluate(service, scene(combat_state=True, ram_state="exhausted", ram_percent=0), exhausted_event, ReactionFocus.RAM_WARNING)
    assert exhausted.opportunity is not None
    assert EventType.PLAYER_RAM_LOW.value in exhausted.opportunity.related_event_types


def test_combined_critical_health_and_exhausted_ram_context_is_compact():
    service = ContextualCompanion(clock=lambda: NOW)
    ram = event(EventType.PLAYER_RAM_EXHAUSTED, priority=EventPriority.P1_HIGH)
    evaluate(service, scene(combat_state=True, ram_state="exhausted", ram_percent=0), ram, ReactionFocus.RAM_WARNING)
    critical = event(EventType.HEALTH_CRITICAL, 1, EventPriority.P0_CRITICAL)
    result = evaluate(service, scene(combat_state=True, health_state="critical", health_percent=5, ram_state="exhausted", ram_percent=0), critical, ReactionFocus.DANGER_WARNING).opportunity
    assert result.tactical_context["health_state"] == "critical"
    assert result.tactical_context["ram_state"] == "exhausted"
    assert len(result.tactical_context["recent_event_types"]) <= 8


def test_weapon_change_is_absorbed_and_critical_replaces_pending_weapon_class_priority():
    service = ContextualCompanion(clock=lambda: NOW)
    weapon = event(EventType.WEAPON_CHANGED, priority=EventPriority.P3_LOW)
    decision = evaluate(service, scene(combat_state=True), weapon, ReactionFocus.EQUIPMENT_CHANGE)
    assert decision.opportunity is None and decision.reason == "absorbed_as_context"
    critical = event(EventType.HEALTH_CRITICAL, 1, EventPriority.P0_CRITICAL)
    assert evaluate(service, scene(combat_state=True, health_state="critical", health_percent=7), critical, ReactionFocus.DANGER_WARNING).opportunity is not None


def test_build_context_is_supporting_only_and_silences_low_confidence():
    service = ContextualCompanion(clock=lambda: NOW)
    ram = event(EventType.PLAYER_RAM_EXHAUSTED, priority=EventPriority.P1_HIGH)
    offensive = scene(combat_state=True, ram_state="exhausted", ram_percent=0, build_style="offensive_netrunner", build_confidence=.85)
    context = evaluate(service, offensive, ram, ReactionFocus.RAM_WARNING).opportunity.tactical_context
    assert context["build_style"] == "offensive_netrunner"
    service.reset("context-session")
    no_deck = scene(combat_state=True, ram_state="exhausted", ram_percent=0, build_style="no_cyberdeck", build_confidence=.8)
    assert evaluate(service, no_deck, ram, ReactionFocus.RAM_WARNING).opportunity.tactical_context["build_style"] == "no_cyberdeck"
    service.reset("context-session")
    weak = scene(combat_state=True, ram_state="exhausted", ram_percent=0, build_style="offensive_netrunner", build_confidence=.3)
    weak_context = evaluate(service, weak, ram, ReactionFocus.RAM_WARNING).opportunity.tactical_context
    assert weak_context["build_style"] is None and "build_profile_low_confidence" in weak_context["known_limitations"]


def test_event_window_count_expiry_sequence_dedupe_and_session_reset():
    now = [NOW]
    service = ContextualCompanion(event_window_seconds=5, event_window_max_items=3, clock=lambda: now[0])
    current = scene()
    for index in range(5):
        item = event(EventType.PLAYER_IDLE, index, sequence=index)
        service.evaluate(update(current, item), None, "test")
    assert service.status().event_window_size == 3
    duplicate = event(EventType.PLAYER_IDLE, 4, sequence=4).model_copy(update={"event_id": service._events[-1].event_id})
    service.evaluate(update(current, duplicate), None, "test")
    assert service.status().event_window_size == 3
    now[0] = NOW + timedelta(seconds=20)
    assert service.status().event_window_size == 0
    service.observe_scene(scene(session_id="new-session"))
    assert service.status().session_id == "new-session" and service.status().event_window_size == 0


def test_low_priority_queue_is_bounded_and_flushes_after_grouping_window():
    service = ContextualCompanion(low_priority_queue_limit=2, grouping_window_seconds=2, clock=lambda: NOW)
    current = scene(combat_state=True, health_state="low", health_percent=20)
    for index in range(3):
        evaluate(service, current, event(EventType.HEALTH_LOW, index / 10, EventPriority.P1_HIGH), ReactionFocus.DANGER_WARNING)
    assert service.status().queue_size == 2
    assert len(service.flush_due(NOW + timedelta(seconds=3))) == 2


def test_voice_text_delivery_and_repeated_category_memory():
    service = ContextualCompanion(clock=lambda: NOW)
    critical_event = event(EventType.HEALTH_CRITICAL, priority=EventPriority.P0_CRITICAL)
    first = evaluate(service, scene(health_state="critical", health_percent=5), critical_event, ReactionFocus.DANGER_WARNING).opportunity
    assert first.delivery_hint == "voice_and_text"
    service.mark_emitted(first)
    second_event = event(EventType.HEALTH_CRITICAL, 2, EventPriority.P0_CRITICAL)
    second = evaluate(service, scene(health_state="critical", health_percent=5), second_event, ReactionFocus.DANGER_WARNING).opportunity
    assert second.tactical_context["repeated_category"] is True
    assert TemplateReactionProvider().text_for_opportunity(second).startswith("Снова")
    recovery_event = event(EventType.PLAYER_HEALED, 4)
    recovery = evaluate(service, scene(phase=ScenePhase.RECOVERY), recovery_event, ReactionFocus.RECOVERY_COMMENT).opportunity
    assert recovery.delivery_hint == "text_only"


def test_disabled_contextual_layer_returns_v085_opportunity_unchanged():
    service = ContextualCompanion(enabled=False, clock=lambda: NOW)
    current = scene(combat_state=True)
    weapon = event(EventType.WEAPON_CHANGED, priority=EventPriority.P3_LOW)
    original = opportunity(current, weapon, ReactionFocus.EQUIPMENT_CHANGE)
    assert service.evaluate(update(current, weapon), original).opportunity is original


def test_profile_change_without_event_only_updates_scene_and_creates_no_candidate():
    service = ContextualCompanion(clock=lambda: NOW)
    assert service.observe_scene(scene(build_style="offensive_netrunner", build_confidence=.9))
    assert service.status().queue_size == 0 and service.status().primary_pending_event is None


def test_contextual_configuration_is_validated_and_functional():
    settings = Settings(context_event_window_seconds=30, context_event_window_max_items=10, grouping_window_seconds=2, low_priority_queue_limit=2, recovery_voice_enabled=True, build_aware_reactions_enabled=False, contextual_companion_enabled=False)
    assert settings.context_event_window_max_items == 10 and not settings.contextual_companion_enabled
    with pytest.raises(ValidationError):
        Settings(grouping_window_seconds=6)
    with pytest.raises(ValidationError):
        Settings(low_priority_queue_limit=0)
