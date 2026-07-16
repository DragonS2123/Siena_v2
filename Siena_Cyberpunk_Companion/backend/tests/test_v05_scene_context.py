from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.domain.priorities import EventPriority
from app.main import create_app
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.scene import HealthTrend, ReactionFocus, SceneContext, ScenePhase
from app.services.companion_behavior_policy import CompanionBehaviorPolicy
from app.services.scene_context import SceneContextBuilder
from app.services.scene_event_normalizer import SceneEventNormalizer

NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
CAPS = BridgeCapabilities(
    player_health=True, player_position=True, combat_state=True,
    vehicle_state=True, pause_state=True, district=True,
)


def event(
    event_type: EventType | str,
    *,
    seconds: float = 0,
    session: str = "v05-session",
    sequence: int | None = None,
    priority: EventPriority = EventPriority.P2_MEDIUM,
    payload: dict | None = None,
) -> GameEvent:
    return GameEvent(
        event_id=f"{event_type}-{seconds}-{session}",
        session_id=session,
        event_type=event_type,
        priority=priority,
        created_at=NOW + timedelta(seconds=seconds),
        source="simulator",
        sequence=sequence,
        summary=str(event_type),
        deduplication_key=f"{event_type}:{seconds}:{session}",
        payload=payload or {},
    )


@pytest.mark.parametrize("value", [
    "session_start", "exploration", "combat", "danger", "recovery",
    "vehicle", "idle", "transition", "session_end", "unknown",
])
def test_scene_phase_contract(value):
    assert ScenePhase(value).value == value


@pytest.mark.parametrize("value", [
    "stable", "falling", "rapidly_falling", "recovering", "critical", "unknown",
])
def test_health_trend_contract(value):
    assert HealthTrend(value).value == value


@pytest.mark.parametrize("value", [
    "session_greeting", "danger_warning", "combat_comment", "recovery_comment",
    "exploration_comment", "vehicle_comment", "idle_comment", "scene_resolution",
    "ram_warning", "resource_recovery", "equipment_change",
])
def test_reaction_focus_contract(value):
    assert ReactionFocus(value).value == value


@pytest.mark.parametrize(("alias", "semantic"), [
    (EventType.PLAYER_HEALTH_BELOW_50, EventType.HEALTH_LOW),
    (EventType.PLAYER_HEALTH_LOW, EventType.HEALTH_LOW),
    (EventType.PLAYER_HEALTH_CRITICAL, EventType.HEALTH_CRITICAL),
    (EventType.PLAYER_RECOVERED, EventType.PLAYER_HEALED),
    (EventType.PLAYER_HEALTH_RECOVERED, EventType.PLAYER_HEALED),
    (EventType.PLAYER_ENTERED_VEHICLE, EventType.VEHICLE_ENTERED),
    (EventType.PLAYER_EXITED_VEHICLE, EventType.VEHICLE_EXITED),
])
def test_legacy_aliases_normalize_without_changing_wire_event(alias, semantic):
    original = event(alias)
    normalized = SceneEventNormalizer().normalize(original)
    assert normalized.semantic_type == semantic.value
    assert normalized.legacy_alias is True
    assert normalized.event.event_type == alias


@pytest.mark.parametrize("event_type", [
    EventType.GAME_STARTED, EventType.GAME_STOPPED, EventType.GAME_LOADED,
    EventType.GAME_PAUSED, EventType.GAME_RESUMED, EventType.ENEMY_COUNT_CHANGED,
    EventType.COMPANION_INTENT_CHANGED,
])
def test_diagnostic_events_are_silence_candidates(event_type):
    assert SceneEventNormalizer().normalize(event(event_type)).diagnostic_only is True


@pytest.mark.parametrize(("event_type", "expected"), [
    (EventType.SESSION_STARTED, ScenePhase.SESSION_START),
    (EventType.COMBAT_STARTED, ScenePhase.COMBAT),
    (EventType.HEALTH_CRITICAL, ScenePhase.DANGER),
    (EventType.VEHICLE_ENTERED, ScenePhase.VEHICLE),
    (EventType.PLAYER_IDLE, ScenePhase.IDLE),
])
def test_builder_selects_meaningful_phase(event_type, expected):
    builder = SceneContextBuilder()
    update = builder.apply(event(event_type), CAPS)
    assert update.scene is not None
    assert update.scene.phase == expected
    assert update.significant is True


@pytest.mark.parametrize(("event_type", "payload", "focus"), [
    (EventType.SESSION_STARTED, {}, ReactionFocus.SESSION_GREETING),
    (EventType.COMBAT_STARTED, {}, ReactionFocus.COMBAT_COMMENT),
    (EventType.HEALTH_CRITICAL, {"health_percent": 8}, ReactionFocus.DANGER_WARNING),
    (EventType.VEHICLE_ENTERED, {}, ReactionFocus.VEHICLE_COMMENT),
    (EventType.PLAYER_IDLE, {"duration_seconds": 60}, ReactionFocus.IDLE_COMMENT),
])
def test_policy_creates_bounded_focus_opportunities(event_type, payload, focus):
    builder = SceneContextBuilder()
    update = builder.apply(event(event_type, payload=payload), CAPS)
    opportunity = CompanionBehaviorPolicy(min_reaction_interval_seconds=0).evaluate(update)
    assert opportunity is not None
    assert opportunity.focus == focus
    assert opportunity.scene_id == update.scene.scene_id
    assert opportunity.scene_revision == update.scene.revision


def test_same_frame_alias_pair_is_one_semantic_scene_event():
    builder = SceneContextBuilder()
    first = builder.apply(event(EventType.PLAYER_HEALTH_CRITICAL, sequence=7, payload={"health_percent": 8}), CAPS)
    second = builder.apply(event(EventType.HEALTH_CRITICAL, sequence=7, payload={"health_percent": 8}), CAPS)
    assert first.scene.event_count == 1
    assert second.normalized_event.duplicate_semantic_event is True
    assert second.scene.event_count == 1


def test_scene_history_is_bounded_and_session_scoped():
    builder = SceneContextBuilder(scene_history_limit=2)
    builder.apply(event(EventType.SESSION_STARTED, session="one"), CAPS)
    builder.apply(event(EventType.SESSION_STARTED, seconds=1, session="two"), CAPS)
    builder.apply(event(EventType.SESSION_STARTED, seconds=2, session="three"), CAPS)
    assert len(builder.history()) == 2
    assert [item.session_id for item in builder.history(session_id="two")] == ["two"]


def test_idle_gap_and_max_duration_create_new_scenes():
    builder = SceneContextBuilder(idle_gap_seconds=5, max_duration_seconds=10)
    first = builder.apply(event(EventType.SESSION_STARTED), CAPS).scene
    second = builder.apply(event(EventType.DISTRICT_CHANGED, seconds=6), CAPS).scene
    third = builder.apply(event(EventType.DISTRICT_CHANGED, seconds=17), CAPS).scene
    assert len({first.scene_id, second.scene_id, third.scene_id}) == 3


def test_recent_event_window_and_id_list_are_bounded():
    builder = SceneContextBuilder(event_history_limit=5, recent_event_window_seconds=2, idle_gap_seconds=100)
    for index in range(8):
        current = builder.apply(event(EventType.DISTRICT_CHANGED, seconds=index, sequence=index), CAPS).scene
    assert len(current.event_ids) == 5
    assert len(current.recent_events) == 3


def test_critical_bypasses_scene_budget_but_weak_event_does_not():
    builder = SceneContextBuilder()
    policy = CompanionBehaviorPolicy(max_reactions=1, min_reaction_interval_seconds=0, critical_bypass=True)
    assert policy.evaluate(builder.apply(event(EventType.SESSION_STARTED), CAPS)) is not None
    weak = event(EventType.PLAYER_DAMAGED, seconds=1, payload={"total_damage": 15, "max_health": 100})
    assert policy.evaluate(builder.apply(weak, CAPS)) is None
    critical = policy.evaluate(builder.apply(event(EventType.HEALTH_CRITICAL, seconds=2, priority=EventPriority.P0_CRITICAL, payload={"health_percent": 5}), CAPS))
    assert critical is not None


def test_scene_models_reject_unknown_fields_and_naive_time():
    valid = SceneContext(
        session_id="strict", source="test", phase=ScenePhase.UNKNOWN,
        started_at=NOW, updated_at=NOW, last_meaningful_event_at=NOW, summary="strict",
    )
    with pytest.raises(ValidationError):
        SceneContext(**{**valid.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        SceneContext(**{**valid.model_dump(), "started_at": NOW.replace(tzinfo=None)})


def test_scene_routes_current_history_and_filters():
    app = create_app(Settings(scene_enabled=True, general_reaction_cooldown_seconds=0, same_event_cooldown_seconds=0))
    services = app.state.services
    services.scene_builder.apply(event(EventType.SESSION_STARTED), CAPS)
    services.scene_builder.apply(event(EventType.SESSION_ENDED, seconds=1), CAPS)
    with TestClient(app) as client:
        assert client.get("/api/v1/scenes/current").json() == {"active": False, "scene": None}
        history = client.get("/api/v1/scenes", params={"session_id": "v05-session", "phase": "session_end"}).json()
        assert len(history) == 1
        assert history[0]["closed_at"] is not None


def test_scene_feature_flag_preserves_legacy_dispatch_path(state_factory):
    settings = Settings(scene_enabled=False, general_reaction_cooldown_seconds=0, same_event_cooldown_seconds=0)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        assert response.status_code == 202
        assert client.get("/api/v1/scenes/current").json()["active"] is False
        assert client.get("/api/v1/reactions").json()
