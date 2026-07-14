from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.bridge import BridgeCapabilities, BridgeHeartbeat, BridgeHello
from app.services.bridge_registry import BridgeRegistry, ProtocolMismatch


def hello_payload(bridge_id: str = "siena-cyberpunk-cet", protocol: str = "1.0", **capabilities) -> dict:
    values = {
        "player_health": True, "player_position": True, "combat_state": False,
        "vehicle_state": False, "pause_state": False, "district": False,
    }
    values.update(capabilities)
    return {
        "bridge_id": bridge_id, "bridge_version": "0.2.0", "protocol_version": protocol,
        "transport": "red_http_client", "game_version": "2.31", "cet_version": "1.37.0",
        "capabilities": values,
    }


def register(client: TestClient, **kwargs) -> dict:
    response = client.post("/api/v1/bridge/hello", json=hello_payload(**kwargs))
    assert response.status_code == 200
    return response.json()


def cet_state(state_factory, sequence: int, session: str = "cet-session", **patches) -> dict:
    state = state_factory(sequence, session_id=session, source="cet", bridge_version="0.2.0", **patches)
    return state.model_dump(mode="json")


def test_successful_bridge_hello():
    with TestClient(create_app(Settings())) as client:
        status = register(client)
        assert status["connected"] is True
        assert status["compatible"] is True
        assert status["active_source"] == "cet"


def test_incompatible_protocol_is_clear_and_nonfatal():
    with TestClient(create_app(Settings())) as client:
        response = client.post("/api/v1/bridge/hello", json=hello_payload(protocol="2.0"))
        assert response.status_code == 400
        assert "requires '1.0'" in response.json()["detail"]
        status = client.get("/api/v1/bridge/status").json()
        assert status["compatible"] is False and status["connected"] is False


def test_bridge_heartbeat_updates_status():
    with TestClient(create_app(Settings())) as client:
        register(client)
        response = client.post("/api/v1/bridge/heartbeat", json={"bridge_id": "siena-cyberpunk-cet", "session_id": "s", "sequence": 8, "dropped_stale_states": 3, "telemetry_rate": 4.0})
        assert response.status_code == 200
        assert response.json()["last_sequence"] == 8
        assert response.json()["dropped_stale_states"] == 3


@pytest.mark.asyncio
async def test_bridge_times_out_after_five_seconds():
    registry = BridgeRegistry(timeout_seconds=5)
    now = datetime.now(timezone.utc)
    await registry.hello(BridgeHello.model_validate(hello_payload()), now)
    assert (await registry.status(now + timedelta(seconds=4.9))).connected
    assert not (await registry.status(now + timedelta(seconds=5.1))).connected


def test_capabilities_are_registered_without_treating_false_as_error():
    with TestClient(create_app(Settings())) as client:
        status = register(client, player_health=True, player_position=False)
        assert status["capabilities"]["player_health"] is True
        assert status["capabilities"]["player_position"] is False
        assert status["last_error"] is None


def test_cet_telemetry_source_is_accepted(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        response = client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1))
        assert response.status_code == 202
        assert response.json()["active"] is True
        assert client.get("/api/v1/telemetry/latest").json()["source"] == "cet"


def test_simulator_telemetry_source_remains_backward_compatible(state_factory):
    with TestClient(create_app(Settings())) as client:
        response = client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        assert response.status_code == 202
        assert response.json()["active_source"] == "simulator"
        assert client.get("/api/v1/telemetry/latest").json()["source"] == "simulator"


def test_cet_sequence_is_monotonic(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        payload = cet_state(state_factory, 1)
        assert client.post("/api/v1/telemetry/state", json=payload).status_code == 202
        assert client.post("/api/v1/telemetry/state", json=payload).status_code == 409


def test_new_cet_session_can_restart_sequence(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        assert client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 9, "old-session")).status_code == 202
        assert client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1, "new-session")).status_code == 202


def test_source_conflict_is_reported(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1))
        status = client.get("/api/v1/bridge/status").json()
        assert status["source_conflict"] is True


def test_cet_has_priority_over_simulator(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        simulator = client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        cet = client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1))
        assert simulator.json()["active"] is False
        assert cet.json()["active"] is True
        assert client.get("/api/v1/telemetry/latest").json()["session_id"] == "cet-session"


def test_config_can_force_simulator_priority(state_factory):
    with TestClient(create_app(Settings(telemetry_source="simulator"))) as client:
        register(client)
        cet = client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1))
        simulator = client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
        assert cet.json()["active"] is False
        assert simulator.json()["active"] is True


def test_v01_endpoints_remain_available():
    with TestClient(create_app(Settings())) as client:
        for path in ("/health", "/api/v1/status", "/api/v1/telemetry/latest", "/api/v1/events", "/api/v1/commands/current"):
            assert client.get(path).status_code == 200


def test_websocket_publishes_bridge_status():
    with TestClient(create_app(Settings())) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "status"
            register(client)
            message = socket.receive_json()
            assert message["type"] == "bridge_status"
            assert message["data"]["bridge_version"] == "0.2.0"


def test_event_generation_from_cet_state(state_factory):
    with TestClient(create_app(Settings())) as client:
        register(client)
        client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 1, player__health=60))
        response = client.post("/api/v1/telemetry/state", json=cet_state(state_factory, 2, player__health=50))
        assert response.status_code == 202
        assert any(item["event_type"] == "player_health_below_50" for item in client.get("/api/v1/events").json())


@pytest.mark.asyncio
async def test_bridge_registry_is_bounded():
    registry = BridgeRegistry(max_bridges=2)
    for index in range(4):
        await registry.hello(BridgeHello.model_validate(hello_payload(bridge_id=f"bridge-{index}")))
    assert (await registry.status()).registered_bridges == 2


@pytest.mark.asyncio
async def test_incompatible_registry_hello_raises_protocol_mismatch():
    registry = BridgeRegistry()
    with pytest.raises(ProtocolMismatch):
        await registry.hello(BridgeHello.model_validate(hello_payload(protocol="9.0")))
