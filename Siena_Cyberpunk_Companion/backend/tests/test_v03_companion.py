from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.priorities import EventPriority
from app.main import create_app
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventType, GameEvent
from app.services.event_filter import EventFilter
from app.services.event_synthesizer import EventSynthesizer
from app.services.reaction_planner import DisabledReactionProvider, ReactionPlanner, TemplateReactionProvider
from app.services.state_diff import diff_states


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def detect(engine, previous, current, now=NOW, capabilities=None):
    return engine.synthesize(previous, current, diff_states(previous, current), now, capabilities)


def types(events):
    return [event.event_type for event in events]


def normalized_event(event_type=EventType.HEALTH_LOW, priority=EventPriority.P1_HIGH):
    return GameEvent(
        session_id="session",
        event_type=event_type,
        priority=priority,
        source="simulator",
        sequence=2,
        summary="test event",
        deduplication_key=f"session:{event_type}",
    )


def test_damage_contains_normalized_health_values(state_factory):
    events = detect(EventSynthesizer(), state_factory(1, player__health=90), state_factory(2, player__health=72))
    damage = next(item for item in events if item.event_type == EventType.PLAYER_DAMAGED)
    assert damage.payload == {
        "previous_health": 90,
        "current_health": 72,
        "damage_amount": 18,
        "total_damage": 18,
        "hits": 1,
        "health_percent": 72,
    }
    assert damage.source == "simulator" and damage.sequence == 2 and damage.severity.value == "high"


def test_identical_state_does_not_create_v03_event(state_factory):
    previous = state_factory(1)
    current = state_factory(2)
    assert not {EventType.PLAYER_DAMAGED, EventType.PLAYER_HEALED, EventType.HEALTH_LOW, EventType.HEALTH_CRITICAL} & set(types(detect(EventSynthesizer(), previous, current)))


def test_health_low_is_latched_until_recovery(state_factory):
    engine = EventSynthesizer()
    first = state_factory(1, player__health=50)
    low = state_factory(2, player__health=24)
    lower = state_factory(3, player__health=20)
    assert EventType.HEALTH_LOW in types(detect(engine, first, low))
    assert EventType.HEALTH_LOW not in types(detect(engine, low, lower, NOW + timedelta(seconds=1)))


def test_health_low_rearms_after_recovery(state_factory):
    engine = EventSynthesizer()
    high = state_factory(1, player__health=50)
    low = state_factory(2, player__health=24)
    recovered = state_factory(3, player__health=40)
    low_again = state_factory(4, player__health=22)
    detect(engine, high, low)
    detect(engine, low, recovered, NOW + timedelta(seconds=1))
    assert EventType.HEALTH_LOW in types(detect(engine, recovered, low_again, NOW + timedelta(seconds=2)))


def test_health_critical_has_critical_priority(state_factory):
    events = detect(EventSynthesizer(), state_factory(1, player__health=30), state_factory(2, player__health=9))
    critical = next(item for item in events if item.event_type == EventType.HEALTH_CRITICAL)
    low = next(item for item in events if item.event_type == EventType.HEALTH_LOW)
    assert critical.priority == EventPriority.P0_CRITICAL
    assert low.priority == EventPriority.P1_HIGH


def test_player_healed_requires_significant_change(state_factory):
    engine = EventSynthesizer(heal_threshold_percent=5)
    assert EventType.PLAYER_HEALED not in types(detect(engine, state_factory(1, player__health=50), state_factory(2, player__health=54)))
    assert EventType.PLAYER_HEALED in types(detect(engine, state_factory(2, player__health=54), state_factory(3, player__health=60), NOW + timedelta(seconds=1)))


def test_idle_emits_once_and_movement_emits_resume(state_factory):
    engine = EventSynthesizer(idle_timeout_seconds=60, player_position_epsilon=0.5)
    one = state_factory(1)
    two = state_factory(2)
    three = state_factory(3)
    moved = state_factory(4, player__position__x=1)
    detect(engine, None, one, NOW)
    assert EventType.PLAYER_IDLE in types(detect(engine, one, two, NOW + timedelta(seconds=60)))
    assert EventType.PLAYER_IDLE not in types(detect(engine, two, three, NOW + timedelta(seconds=61)))
    assert EventType.PLAYER_MOVED_AFTER_IDLE in types(detect(engine, three, moved, NOW + timedelta(seconds=62)))


def test_unavailable_capabilities_do_not_create_false_transitions(state_factory):
    unavailable = BridgeCapabilities(player_health=True, player_position=True)
    previous = state_factory(1, player__in_combat=True, player__in_vehicle=True)
    current = state_factory(2, player__in_combat=False, player__in_vehicle=False)
    found = set(types(detect(EventSynthesizer(), previous, current, capabilities=unavailable)))
    assert not found & {EventType.COMBAT_ENDED, EventType.VEHICLE_EXITED, EventType.PLAYER_EXITED_VEHICLE}


def test_new_session_starts_once_and_resets_tracker(state_factory):
    engine = EventSynthesizer()
    old = state_factory(10, session_id="old")
    new = state_factory(1, session_id="new")
    assert types(detect(engine, old, new)).count(EventType.SESSION_STARTED) == 1
    assert EventType.SESSION_STARTED not in types(detect(engine, new, state_factory(2, session_id="new"), NOW + timedelta(seconds=1)))


def test_session_ended_uses_injected_clock(state_factory):
    engine = EventSynthesizer(session_timeout_seconds=10)
    state = state_factory(1)
    detect(engine, None, state, NOW)
    assert engine.expire_sessions(NOW + timedelta(seconds=9.9)) == []
    ended = engine.expire_sessions(NOW + timedelta(seconds=10))
    assert types(ended) == [EventType.SESSION_ENDED]
    assert engine.expire_sessions(NOW + timedelta(seconds=20)) == []


def test_duplicate_event_cooldown_is_configurable():
    filter_ = EventFilter(same_event_cooldown_seconds=60)
    first = normalized_event(EventType.PLAYER_IDLE, EventPriority.P3_LOW)
    duplicate = normalized_event(EventType.PLAYER_IDLE, EventPriority.P3_LOW)
    assert filter_.process([first], NOW) == [first]
    assert filter_.process([duplicate], NOW + timedelta(seconds=30)) == []
    assert filter_.process([duplicate], NOW + timedelta(seconds=61)) == [duplicate]


@pytest.mark.asyncio
async def test_critical_reaction_is_planned_before_low_reaction():
    planner = ReactionPlanner(TemplateReactionProvider(), general_cooldown_seconds=20)
    low = normalized_event(EventType.HEALTH_LOW, EventPriority.P1_HIGH)
    critical = normalized_event(EventType.HEALTH_CRITICAL, EventPriority.P0_CRITICAL)
    reactions = await planner.consider_many([low, critical], NOW)
    assert [item.event_type for item in reactions] == [EventType.HEALTH_CRITICAL]


@pytest.mark.asyncio
async def test_disabled_provider_never_creates_reactions():
    planner = ReactionPlanner(DisabledReactionProvider(), enabled=True)
    assert await planner.consider(normalized_event(), NOW) is None
    assert not (await planner.status(NOW)).enabled


@pytest.mark.asyncio
async def test_template_provider_returns_expected_text():
    reaction = await ReactionPlanner(TemplateReactionProvider()).consider(normalized_event(), NOW)
    assert reaction and reaction.text == "Здоровья осталось мало. Будь осторожнее."
    assert reaction.provider == "template"


def test_v03_rest_endpoints_are_typed_and_filterable(state_factory):
    with TestClient(create_app(Settings(general_reaction_cooldown_seconds=0))) as client:
        payload = state_factory(1, player__health=24).model_dump(mode="json")
        assert client.post("/api/v1/telemetry/state", json=payload).status_code == 202
        events = client.get("/api/v1/events", params={"event_type": "health_low", "severity": "high", "session_id": "test-session"})
        assert events.status_code == 200 and events.json()[0]["summary"]
        assert client.get("/api/v1/events/latest").status_code == 200
        assert client.get("/api/v1/reactions").status_code == 200
        assert client.get("/api/v1/reactions/latest").status_code == 200
        planner = client.get("/api/v1/planner/status")
        assert planner.status_code == 200 and planner.json()["provider"] == "template"


def test_websocket_publishes_v03_event_and_reaction(state_factory):
    with TestClient(create_app(Settings(general_reaction_cooldown_seconds=0))) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "status"
            client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
            messages = [socket.receive_json() for _ in range(4)]
            game_event = next(item for item in messages if item["type"] == "game_event")
            reaction = next(item for item in messages if item["type"] == "siena_reaction")
            assert game_event["payload"]["event_type"] == "session_started"
            assert reaction["payload"]["event_type"] == "session_started"


def test_disabled_reactions_api_stays_empty(state_factory):
    with TestClient(create_app(Settings(reactions_enabled=False, reaction_provider="disabled"))) as client:
        client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        assert client.get("/api/v1/reactions").json() == []
        status = client.get("/api/v1/planner/status").json()
        assert status["enabled"] is False and status["provider"] == "disabled"
