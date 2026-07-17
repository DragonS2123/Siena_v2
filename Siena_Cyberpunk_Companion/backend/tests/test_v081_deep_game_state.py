from copy import deepcopy

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.game_state import GameState
from app.models.bridge import BridgeHello
from conftest import BASE


def payload_with_deep(**domains) -> dict:
    payload = deepcopy(BASE)
    payload["source"] = "cet"
    payload["bridge_version"] = "0.8.1"
    payload["deep_game_state"] = {
        "capabilities": {
            "player": True,
            "stats": True,
            "stat_pools": True,
            "weapon": True,
            "status_effects": True,
        },
        "player": None,
        "stats": None,
        "stat_pools": None,
        "weapon": None,
        "status_effects": None,
    }
    payload["deep_game_state"].update(domains)
    return payload


def test_weapon_drawn_uses_active_record_id():
    state = GameState.model_validate(payload_with_deep(
        weapon={"drawn": True, "record_id": "Items.Preset_Sidewinder_Default", "source": "active"}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.weapon is not None
    assert state.deep_game_state.weapon.drawn is True
    assert state.deep_game_state.weapon.source == "active"


def test_weapon_holstered_accepts_weapon_right_fallback():
    state = GameState.model_validate(payload_with_deep(
        weapon={"drawn": False, "record_id": "Items.Preset_Sidewinder_Default", "source": "weapon_right"}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.weapon is not None
    assert state.deep_game_state.weapon.drawn is False
    assert state.deep_game_state.weapon.source == "weapon_right"


def test_no_player_keeps_all_session_dependent_values_nullable():
    state = GameState.model_validate(payload_with_deep(
        capabilities={"player": True, "stats": False, "stat_pools": False, "weapon": False, "status_effects": False},
        player={"entity_available": False, "session_available": False, "is_pre_game": True, "position": None, "mounted_vehicle": None},
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.player is not None
    assert state.deep_game_state.player.entity_available is False
    assert state.deep_game_state.player.session_available is False
    assert state.deep_game_state.stats is None
    assert state.deep_game_state.weapon is None


def test_current_and_maximum_health_are_preserved():
    state = GameState.model_validate(payload_with_deep(
        stat_pools={"current_health": 351.25, "maximum_health": 551.25, "current_memory": None, "maximum_memory": None}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.stat_pools is not None
    assert state.deep_game_state.stat_pools.current_health == 351.25
    assert state.deep_game_state.stat_pools.maximum_health == 551.25


def test_current_and_maximum_ram_are_preserved():
    state = GameState.model_validate(payload_with_deep(
        stat_pools={"current_health": None, "maximum_health": None, "current_memory": 12, "maximum_memory": 33}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.stat_pools is not None
    assert state.deep_game_state.stat_pools.current_memory == 12
    assert state.deep_game_state.stat_pools.maximum_memory == 33


def test_empty_status_effects_are_explicit_and_bounded():
    state = GameState.model_validate(payload_with_deep(
        status_effects={"observed_count": 0, "truncated": False, "limit": 32}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.status_effects is not None
    assert state.deep_game_state.status_effects.observed_count == 0
    assert state.deep_game_state.status_effects.truncated is False


def test_status_effect_count_never_exceeds_bound():
    state = GameState.model_validate(payload_with_deep(
        status_effects={"observed_count": 32, "truncated": True, "limit": 32}
    ))
    assert state.deep_game_state is not None
    assert state.deep_game_state.status_effects is not None
    assert state.deep_game_state.status_effects.observed_count == state.deep_game_state.status_effects.limit
    assert state.deep_game_state.status_effects.truncated is True


def test_old_v07_payload_without_deep_game_state_is_accepted():
    old_payload = deepcopy(BASE)
    state = GameState.model_validate(old_payload)
    assert state.deep_game_state is None

    with TestClient(create_app(Settings())) as client:
        response = client.post("/api/v1/telemetry/state", json=old_payload)
        assert response.status_code == 202
        assert client.get("/api/v1/telemetry/latest").json()["deep_game_state"] is None


def test_old_v07_hello_defaults_new_domain_capabilities_to_false():
    hello = BridgeHello.model_validate({
        "bridge_id": "old-v07",
        "bridge_version": "0.7.0",
        "protocol_version": "1.0",
        "transport": "red_http_client",
        "capabilities": {
            "player_health": True,
            "player_position": True,
            "combat_state": True,
            "vehicle_state": True,
            "pause_state": False,
            "district": False,
        },
    })
    assert hello.capabilities.deep_player is False
    assert hello.capabilities.deep_stats is False
    assert hello.capabilities.deep_stat_pools is False
    assert hello.capabilities.deep_weapon is False
    assert hello.capabilities.deep_status_effects is False
