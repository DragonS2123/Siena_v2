from copy import deepcopy
from datetime import datetime, timedelta, timezone

from conftest import BASE
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.priorities import EventPriority
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventType, GameEvent
from app.models.game_state import GameState
from app.models.scene import ReactionFocus
from app.main import create_app
from app.services.companion_behavior_policy import CompanionBehaviorPolicy
from app.services.deep_game_state_awareness import DeepGameStateAwareness
from app.services.event_filter import EventFilter
from app.services.event_synthesizer import EventSynthesizer
from app.services.scene_context import SceneContextBuilder
from app.services.state_diff import diff_states

NOW = datetime(2026, 7, 16, 18, tzinfo=timezone.utc)
CAPS = BridgeCapabilities(
    player_health=True, player_position=True, combat_state=True, vehicle_state=True,
    deep_player=True, deep_stats=True, deep_stat_pools=True, deep_weapon=True, deep_status_effects=True,
)


def deep_state(
    sequence: int,
    *,
    session: str = "deep-session",
    health: float | None = 100,
    max_health: float | None = 100,
    ram: float | None = 20,
    max_ram: float | None = 20,
    weapon_drawn: bool | None = False,
    weapon_id: str | None = "Items.Preset_Ajax_Default",
    effects: int | None = 2,
    pre_game: bool | None = False,
    available: bool | None = True,
) -> GameState:
    payload = deepcopy(BASE)
    payload.update({
        "session_id": session,
        "sequence": sequence,
        "captured_at": (NOW + timedelta(seconds=sequence)).isoformat(),
        "source": "cet",
        "bridge_version": "0.8.1",
    })
    payload["deep_game_state"] = {
        "capabilities": {"player": True, "stats": True, "stat_pools": True, "weapon": True, "status_effects": True},
        "player": {
            "entity_available": available, "session_available": available,
            "is_pre_game": pre_game, "position": {"x": 0, "y": 0, "z": 0}, "mounted_vehicle": False,
        },
        "stats": {"level": 50, "street_cred": 42, "armor": 610, "power_level": 50, "health": max_health, "memory": max_ram},
        "stat_pools": {
            "current_health": health, "maximum_health": max_health,
            "current_memory": ram, "maximum_memory": max_ram,
        },
        "weapon": {"drawn": weapon_drawn, "record_id": weapon_id, "source": "active" if weapon_drawn else "weapon_right"},
        "status_effects": {"observed_count": effects, "truncated": False, "limit": 32},
    }
    if health is not None and max_health:
        payload["player"]["health"] = health
        payload["player"]["max_health"] = max_health
    return GameState.model_validate(payload)


def detect(engine: EventSynthesizer, previous: GameState | None, current: GameState, seconds: int = 0):
    return engine.synthesize(previous, current, diff_states(previous, current), NOW + timedelta(seconds=seconds), CAPS)


def event_types(events):
    return [event.event_type for event in events]


def test_normal_to_low_health():
    found = detect(EventSynthesizer(), deep_state(1), deep_state(2, health=25))
    assert event_types(found).count(EventType.PLAYER_HEALTH_LOW) == 1
    assert EventType.HEALTH_LOW not in event_types(found)


def test_low_to_critical_health_and_no_repeated_low():
    engine = EventSynthesizer()
    normal, low, lower, critical = deep_state(1), deep_state(2, health=25), deep_state(3, health=20), deep_state(4, health=12)
    assert EventType.PLAYER_HEALTH_LOW in event_types(detect(engine, normal, low))
    assert EventType.PLAYER_HEALTH_LOW not in event_types(detect(engine, low, lower, 1))
    assert EventType.PLAYER_HEALTH_CRITICAL in event_types(detect(engine, lower, critical, 2))


def test_critical_to_recovered_health():
    engine = EventSynthesizer()
    normal, critical, recovered = deep_state(1), deep_state(2, health=10), deep_state(3, health=40)
    detect(engine, normal, critical)
    events = detect(engine, critical, recovered, 1)
    assert EventType.PLAYER_HEALTH_RECOVERED in event_types(events)


def test_missing_or_invalid_health_maximum_emits_nothing():
    engine = EventSynthesizer()
    missing = deep_state(2, health=None, max_health=None)
    assert not set(event_types(detect(engine, deep_state(1), missing))) & {
        EventType.PLAYER_HEALTH_LOW, EventType.PLAYER_HEALTH_CRITICAL, EventType.PLAYER_HEALTH_RECOVERED,
    }
    assert DeepGameStateAwareness._resource(5, 0) is None


def test_ram_low_exhausted_and_recovered_state_machine():
    engine = EventSynthesizer()
    normal, low = deep_state(1), deep_state(2, ram=5)
    exhausted, recovered = deep_state(3, ram=2), deep_state(4, ram=12)
    assert EventType.PLAYER_RAM_LOW in event_types(detect(engine, normal, low))
    assert EventType.PLAYER_RAM_EXHAUSTED in event_types(detect(engine, low, exhausted, 1))
    assert EventType.PLAYER_RAM_RECOVERED in event_types(detect(engine, exhausted, recovered, 2))


def test_weapon_drawn_holstered_and_changed_use_stable_identity():
    engine = EventSynthesizer()
    holstered = deep_state(1, weapon_drawn=False, weapon_id="Items.Preset_Ajax_Default")
    drawn = deep_state(2, weapon_drawn=True, weapon_id="Items.Preset_Ajax_Default")
    changed = deep_state(3, weapon_drawn=True, weapon_id="Items.Preset_Sidewinder_Default")
    rewrapped = deep_state(4, weapon_drawn=True, weapon_id="ToTweakDBID{ --[[ Items.Preset_Sidewinder_Default --]] }")
    assert EventType.WEAPON_DRAWN in event_types(detect(engine, holstered, drawn))
    assert EventType.WEAPON_CHANGED in event_types(detect(engine, drawn, changed, 1))
    assert EventType.WEAPON_CHANGED not in event_types(detect(engine, changed, rewrapped, 2))
    assert EventType.WEAPON_HOLSTERED in event_types(detect(engine, rewrapped, deep_state(5, weapon_drawn=False, weapon_id="Items.Preset_Sidewinder_Default"), 3))


def test_status_count_increased_and_decreased_only_within_session():
    engine = EventSynthesizer()
    two, three, one = deep_state(1, effects=2), deep_state(2, effects=3), deep_state(3, effects=1)
    assert EventType.STATUS_EFFECTS_INCREASED in event_types(detect(engine, two, three))
    assert EventType.STATUS_EFFECTS_DECREASED in event_types(detect(engine, three, one, 1))
    new_session = deep_state(1, session="new", effects=5)
    assert not set(event_types(detect(engine, one, new_session, 2))) & {
        EventType.STATUS_EFFECTS_INCREASED, EventType.STATUS_EFFECTS_DECREASED,
    }


def test_session_reset_seeds_threshold_state_without_stale_transition():
    engine = EventSynthesizer()
    detect(engine, deep_state(1), deep_state(2, health=25))
    new_low = deep_state(1, session="new", health=20)
    events = detect(engine, deep_state(2, health=25), new_low, 1)
    assert EventType.PLAYER_HEALTH_LOW not in event_types(events)
    assert EventType.PLAYER_HEALTH_CRITICAL not in event_types(events)


def test_pre_game_and_unavailable_player_suppress_awareness_events():
    engine = EventSynthesizer()
    previous = deep_state(1)
    pre_game = deep_state(2, health=10, ram=1, weapon_drawn=True, effects=5, pre_game=True)
    unavailable = deep_state(3, health=10, ram=1, weapon_drawn=True, effects=6, available=False)
    unknown = deep_state(4, health=10, ram=1, weapon_drawn=True, effects=7, available=None, pre_game=None)
    awareness = {
        EventType.PLAYER_HEALTH_LOW, EventType.PLAYER_HEALTH_CRITICAL,
        EventType.PLAYER_RAM_LOW, EventType.PLAYER_RAM_EXHAUSTED,
        EventType.WEAPON_DRAWN, EventType.STATUS_EFFECTS_INCREASED,
    }
    assert not awareness & set(event_types(detect(engine, previous, pre_game)))
    assert not awareness & set(event_types(detect(engine, pre_game, unavailable, 1)))
    assert not awareness & set(event_types(detect(engine, unavailable, unknown, 2)))


def test_old_v07_and_v081_payloads_remain_compatible():
    old = deepcopy(BASE)
    old["captured_at"] = NOW.isoformat()
    assert GameState.model_validate(old).deep_game_state is None
    v081 = deep_state(1)
    assert detect(EventSynthesizer(), None, v081)


def test_scene_context_observes_current_deep_state_without_raw_handles():
    builder = SceneContextBuilder()
    started = GameEvent(
        session_id="deep-session", event_type=EventType.SESSION_STARTED, priority=EventPriority.P2_MEDIUM,
        created_at=NOW, source="cet", sequence=1, summary="start", deduplication_key="start",
    )
    builder.apply(started, CAPS)
    assert builder.observe_state(deep_state(2, health=75, ram=10, weapon_drawn=True, effects=4), CAPS)
    scene = builder.current()
    assert scene is not None
    assert (scene.health_current, scene.health_maximum, scene.health_percent, scene.health_state) == (75, 100, 75, "normal")
    assert (scene.ram_current, scene.ram_maximum, scene.ram_percent, scene.ram_state) == (10, 20, 50, "normal")
    assert scene.active_weapon_record_id == "Items.Preset_Ajax_Default" and scene.weapon_drawn is True
    assert (scene.status_effect_count, scene.level, scene.street_cred, scene.armor) == (4, 50, 42, 610)


def test_telemetry_endpoint_exposes_deep_state_in_current_scene():
    state = deep_state(1).model_copy(update={"source": "simulator", "bridge_version": None})
    with TestClient(create_app(Settings(general_reaction_cooldown_seconds=0, same_event_cooldown_seconds=0))) as client:
        assert client.post("/api/v1/telemetry/state", json=state.model_dump(mode="json")).status_code == 202
        scene = client.get("/api/v1/scenes/current").json()["scene"]
        assert scene["health_current"] == 100
        assert scene["ram_current"] == 20
        assert scene["active_weapon_record_id"] == "Items.Preset_Ajax_Default"
        assert scene["level"] == 50 and scene["armor"] == 610


def test_policy_priorities_cooldown_repetition_and_loading_guard():
    builder = SceneContextBuilder()
    policy = CompanionBehaviorPolicy(min_reaction_interval_seconds=15)
    low_event = next(item for item in detect(EventSynthesizer(), deep_state(1), deep_state(2, health=25)) if item.event_type == EventType.PLAYER_HEALTH_LOW)
    low = builder.apply(low_event, CAPS)
    assert policy.evaluate(low) is not None
    duplicate = builder.apply(low_event.model_copy(update={"event_id": "duplicate", "sequence": 3}), CAPS)
    assert policy.evaluate(duplicate) is None
    critical_event = next(item for item in detect(EventSynthesizer(), deep_state(2, health=25), deep_state(3, health=10)) if item.event_type == EventType.PLAYER_HEALTH_CRITICAL)
    critical = policy.evaluate(builder.apply(critical_event, CAPS))
    assert critical is not None and critical.priority == EventPriority.P0_CRITICAL

    loading_builder = SceneContextBuilder()
    loading = loading_builder.apply(GameEvent(
        session_id="loading", event_type=EventType.SESSION_STARTED, priority=EventPriority.P2_MEDIUM,
        created_at=NOW, source="cet", summary="loading", deduplication_key="loading",
        payload={"player_available": True, "session_available": True, "is_pre_game": True},
    ), CAPS)
    assert CompanionBehaviorPolicy(min_reaction_interval_seconds=0).evaluate(loading) is None


def test_ram_and_weapon_policy_are_bounded_and_weapon_voice_focus_is_text_only():
    builder = SceneContextBuilder()
    policy = CompanionBehaviorPolicy(min_reaction_interval_seconds=0)
    ram_event = next(item for item in detect(EventSynthesizer(), deep_state(1), deep_state(2, ram=2)) if item.event_type == EventType.PLAYER_RAM_EXHAUSTED)
    ram = policy.evaluate(builder.apply(ram_event, CAPS))
    assert ram is not None and ram.focus == ReactionFocus.RAM_WARNING

    weapon_event = next(item for item in detect(EventSynthesizer(), deep_state(2, weapon_drawn=False), deep_state(3, weapon_drawn=True)) if item.event_type == EventType.WEAPON_DRAWN)
    assert policy.evaluate(builder.apply(weapon_event, CAPS)) is None

    combat = GameEvent(
        session_id="deep-session", event_type=EventType.COMBAT_STARTED, priority=EventPriority.P1_HIGH,
        created_at=NOW + timedelta(seconds=4), source="cet", summary="combat", deduplication_key="combat",
    )
    builder.apply(combat, CAPS)
    meaningful_weapon = weapon_event.model_copy(update={"event_id": "weapon-in-combat", "sequence": 5, "created_at": NOW + timedelta(seconds=5)})
    opportunity = policy.evaluate(builder.apply(meaningful_weapon, CAPS))
    assert opportunity is not None and opportunity.focus == ReactionFocus.EQUIPMENT_CHANGE


def test_event_filter_cooldown_suppresses_duplicate_wire_event():
    filter_ = EventFilter(same_event_cooldown_seconds=60)
    first = GameEvent(
        session_id="s", event_type=EventType.STATUS_EFFECTS_INCREASED, priority=EventPriority.P3_LOW,
        created_at=NOW, summary="status", deduplication_key="s:status_effects_increased",
    )
    second = first.model_copy(update={"event_id": "second", "created_at": NOW + timedelta(seconds=1)})
    assert filter_.process([first], NOW) == [first]
    assert filter_.process([second], NOW + timedelta(seconds=1)) == []
