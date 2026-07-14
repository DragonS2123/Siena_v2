import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.domain.priorities import EventPriority
from app.main import create_app
from app.models.companion_command import CommandIntent, CompanionCommand
from app.models.game_event import GameEvent
from app.models.game_state import GameState
from app.services.decision_scheduler import DecisionScheduler
from app.services.event_bus import EventBus
from app.services.event_filter import EventFilter
from app.services.event_synthesizer import EventSynthesizer
from app.services.state_diff import diff_states
from app.services.state_store import SequenceConflict, StateStore


def event(event_type: str, key: str | None = None, damage: float = 0) -> GameEvent:
    return GameEvent(session_id="s", event_type=event_type, priority=EventPriority.P1_HIGH, deduplication_key=key or event_type, payload={"total_damage": damage, "hits": 1} if damage else {})


def synth(previous, current, now=None, service=None):
    engine = service or EventSynthesizer()
    return engine.synthesize(previous, current, diff_states(previous, current), now)


def names(events):
    return [item.event_type for item in events]


def test_game_state_validation_rejects_invalid_health(state_factory):
    with pytest.raises(ValidationError):
        state_factory(player__health=101)


def test_game_state_forbids_unknown_fields(state_factory):
    data = state_factory().model_dump()
    data["surprise"] = True
    with pytest.raises(ValidationError):
        GameState.model_validate(data)


@pytest.mark.asyncio
async def test_rejects_duplicate_sequence(state_factory):
    store = StateStore()
    await store.accept(state_factory(4))
    with pytest.raises(SequenceConflict):
        await store.accept(state_factory(4))


@pytest.mark.asyncio
async def test_rejects_stale_sequence(state_factory):
    store = StateStore()
    await store.accept(state_factory(5))
    with pytest.raises(SequenceConflict):
        await store.accept(state_factory(3))


def test_combat_started(state_factory):
    assert "combat_started" in names(synth(state_factory(1), state_factory(2, player__in_combat=True)))


def test_combat_ended(state_factory):
    assert "combat_ended" in names(synth(state_factory(1, player__in_combat=True), state_factory(2)))


def test_game_stop_is_not_duplicated_when_unloaded(state_factory):
    previous = state_factory(1)
    current = state_factory(2, game__running=False, game__loaded=False)
    assert names(synth(previous, current)).count("game_stopped") == 1


def test_crossing_health_50(state_factory):
    assert "player_health_below_50" in names(synth(state_factory(1, player__health=60), state_factory(2, player__health=50)))


def test_crossing_health_critical(state_factory):
    assert "player_health_critical" in names(synth(state_factory(1, player__health=30), state_factory(2, player__health=25)))


def test_health_threshold_not_repeated(state_factory):
    assert "player_health_critical" not in names(synth(state_factory(1, player__health=24), state_factory(2, player__health=20)))


def test_damage_aggregation():
    base = datetime.now(timezone.utc)
    filter_ = EventFilter(damage_window_seconds=0.5)
    assert filter_.process([event("player_damaged", damage=3)], base) == []
    filter_.process([event("player_damaged", damage=4), event("player_damaged", damage=5)], base + timedelta(milliseconds=200))
    result = filter_.flush_due(base + timedelta(milliseconds=501))
    assert result[0].payload == {"total_damage": 12, "hits": 3}


def test_enemy_detected_debounce():
    base = datetime.now(timezone.utc)
    filter_ = EventFilter(enemy_debounce_seconds=5)
    assert len(filter_.process([event("enemy_detected", "enemy:x")], base)) == 1
    assert filter_.process([event("enemy_detected", "enemy:x")], base + timedelta(seconds=2)) == []
    assert len(filter_.process([event("enemy_detected", "enemy:x")], base + timedelta(seconds=6))) == 1


def test_player_entered_vehicle(state_factory):
    assert "player_entered_vehicle" in names(synth(state_factory(1), state_factory(2, player__in_vehicle=True)))


def test_player_exited_vehicle(state_factory):
    assert "player_exited_vehicle" in names(synth(state_factory(1, player__in_vehicle=True), state_factory(2)))


def test_companion_too_far_crossing(state_factory):
    assert "companion_too_far" in names(synth(state_factory(1, companion__distance_to_player=19), state_factory(2, companion__distance_to_player=21)))


def test_companion_too_far_resets_with_regroup_event(state_factory):
    assert "companion_regrouped" in names(synth(state_factory(1, companion__distance_to_player=21), state_factory(2, companion__distance_to_player=19)))


def test_companion_stuck_after_five_seconds(state_factory):
    engine = EventSynthesizer(stuck_seconds=5)
    start = datetime.now(timezone.utc)
    previous = state_factory(1, companion__distance_to_player=10)
    current = state_factory(2, companion__distance_to_player=10)
    synth(previous, current, start, engine)
    result = synth(current, state_factory(3, companion__distance_to_player=10), start + timedelta(seconds=5), engine)
    assert "companion_stuck" in names(result)


def test_companion_stuck_resets_after_movement(state_factory):
    engine = EventSynthesizer(stuck_seconds=5)
    start = datetime.now(timezone.utc)
    one = state_factory(1, companion__distance_to_player=10)
    two = state_factory(2, companion__distance_to_player=10)
    synth(one, two, start, engine)
    synth(two, state_factory(3, companion__distance_to_player=10), start + timedelta(seconds=5), engine)
    result = synth(state_factory(3, companion__distance_to_player=10), state_factory(4, companion__distance_to_player=8), start + timedelta(seconds=6), engine)
    assert "companion_regrouped" in names(result)
    assert not engine.is_stuck("test-session")


@pytest.mark.asyncio
@pytest.mark.parametrize(("event_type","intent"), [("player_health_critical","retreat"),("combat_started","protect"),("companion_too_far","regroup"),("combat_ended","follow")])
async def test_scheduler_selects_command(event_type, intent):
    command = await DecisionScheduler().consider(event(event_type))
    assert command and command.intent == intent


@pytest.mark.asyncio
async def test_p0_interrupts_active_lower_priority():
    scheduler = DecisionScheduler()
    await scheduler.consider(event("combat_started"))
    critical = await scheduler.consider(event("player_health_critical"))
    assert critical and critical.intent == CommandIntent.RETREAT


@pytest.mark.asyncio
async def test_command_expiry():
    scheduler = DecisionScheduler()
    old = datetime.now(timezone.utc) - timedelta(seconds=20)
    await scheduler.set_manual(CompanionCommand(intent="hold", priority="P1_HIGH", created_at=old, valid_for_seconds=10))
    assert await scheduler.current() is None


@pytest.mark.asyncio
async def test_event_buffer_is_bounded():
    bus = EventBus(history_size=3)
    for index in range(6):
        await bus.publish("event", event(f"event_{index}"))
    assert [item.event_type for item in bus.events(10)] == ["event_5", "event_4", "event_3"]


@pytest.mark.asyncio
async def test_slow_websocket_queue_drops_oldest():
    bus = EventBus(client_queue_size=2)
    queue = await bus.connect()
    await bus.publish("status", {"n": 1}); await bus.publish("status", {"n": 2}); await bus.publish("status", {"n": 3})
    assert (await queue.get())["data"]["n"] == 2


def test_health_endpoint():
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health")
        assert response.status_code == 200 and response.json()["status"] == "ok"


def test_api_returns_conflict_for_duplicate_sequence(state_factory):
    with TestClient(create_app(Settings())) as client:
        payload = state_factory(1).model_dump(mode="json")
        assert client.post("/api/v1/telemetry/state", json=payload).status_code == 202
        assert client.post("/api/v1/telemetry/state", json=payload).status_code == 409


def test_websocket_publishes_state_and_event(state_factory):
    with TestClient(create_app(Settings())) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "status"
            response = client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
            assert response.status_code == 202
            received = [socket.receive_json() for _ in range(2)]
            assert received[0]["type"] == "state"
            assert any(message["type"] == "event" for message in received)
