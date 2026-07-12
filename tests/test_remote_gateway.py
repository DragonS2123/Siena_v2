"""Siena Remote Presence — Home Gateway Phase 1 (remote_gateway/).

Unit tests for credentials (fake protector — DPAPI itself is exercised only
manually), protocol validation, and the HomeGatewayAgent state machine on a
fake in-memory transport, plus a real local integration smoke against a
temporary `websockets` server acting as a fake Relay (Bearer auth →
gateway.connected → heartbeat/ack → server close → Reconnecting).

No test ever touches the production Relay, real credentials, or DPAPI.
Secrets discipline is itself under test: token bytes must not appear in the
credentials file, status payloads, or captured log/trace events.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from remote_gateway import protocol  # noqa: E402
from remote_gateway.agent import GatewayState, HomeGatewayAgent  # noqa: E402
from remote_gateway.credentials import CredentialsStore  # noqa: E402
from remote_gateway.service import RemoteGatewayService  # noqa: E402
from remote_gateway.transport import TransportAuthError, TransportClosed, TransportTlsError  # noqa: E402

TEST_GATEWAY_ID = "gw_test1234_ABCD"
TEST_TOKEN = "srg_fake_test_token_never_real_12345"


def run_async(coro, timeout: float = 15.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


class FakeProtector:
    """Reversible obfuscation for tests — NOT crypto, just proves the store
    round-trips through protect/unprotect and never writes plaintext."""

    def protect(self, plaintext: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in plaintext)

    def unprotect(self, blob: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in blob)


# ─── credentials ────────────────────────────────────────────────────────────

def _store(tmp_path: Path) -> CredentialsStore:
    return CredentialsStore(path=tmp_path / "credentials.json", protector=FakeProtector())


def test_credentials_roundtrip(tmp_path):
    store = _store(tmp_path)
    assert store.is_configured() is False
    store.save(TEST_GATEWAY_ID, TEST_TOKEN)
    assert store.is_configured() is True
    assert store.gateway_id() == TEST_GATEWAY_ID
    assert store.decrypt_token() == TEST_TOKEN


def test_credentials_file_never_contains_plaintext_token(tmp_path):
    store = _store(tmp_path)
    store.save(TEST_GATEWAY_ID, TEST_TOKEN)
    raw = (tmp_path / "credentials.json").read_text(encoding="utf-8")
    assert TEST_TOKEN not in raw
    data = json.loads(raw)
    assert data["schema_version"] == 1
    assert data["gateway_id"] == TEST_GATEWAY_ID
    assert data["encrypted_token"]
    assert data["created_at"] and data["updated_at"]


def test_credentials_clear(tmp_path):
    store = _store(tmp_path)
    store.save(TEST_GATEWAY_ID, TEST_TOKEN)
    assert store.clear() is True
    assert store.is_configured() is False
    assert store.decrypt_token() is None
    assert store.clear() is False  # idempotent


def test_credentials_corrupt_file_is_not_configured(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{broken json", encoding="utf-8")
    store = CredentialsStore(path=path, protector=FakeProtector())
    assert store.is_configured() is False
    assert store.decrypt_token() is None


# ─── protocol / relay URL validation ────────────────────────────────────────

def test_production_relay_url_accepted():
    assert protocol.validate_relay_url("wss://relay.sienaai.ru") == "wss://relay.sienaai.ru"


@pytest.mark.parametrize("bad_url", [
    "ws://relay.sienaai.ru",           # scheme downgrade
    "wss://evil.example.com",          # wrong host
    "wss://relay.sienaai.ru.evil.io",  # suffix trick
    "https://relay.sienaai.ru",        # wrong protocol entirely
    "ws://127.0.0.1:8777",             # localhost without the explicit dev flag
])
def test_invalid_relay_urls_rejected(bad_url):
    with pytest.raises(protocol.RelayUrlError):
        protocol.validate_relay_url(bad_url)


def test_localhost_allowed_only_with_dev_flag():
    assert protocol.validate_relay_url("ws://127.0.0.1:8777", allow_insecure_localhost=True)
    with pytest.raises(protocol.RelayUrlError):
        # even the dev flag never allows an arbitrary remote host
        protocol.validate_relay_url("ws://evil.example.com", allow_insecure_localhost=True)


def test_gateway_id_validation():
    assert protocol.validate_gateway_id(TEST_GATEWAY_ID) == TEST_GATEWAY_ID
    for bad in ("", "gw_short", "device_123456789", "gw_" + "x" * 100, "gw_bad space"):
        with pytest.raises(ValueError):
            protocol.validate_gateway_id(bad)


def test_heartbeat_build_and_request_id_pattern():
    request_id = protocol.new_request_id(7)
    assert protocol.REQUEST_ID_PATTERN.fullmatch(request_id)
    frame = json.loads(protocol.build_heartbeat(request_id))
    assert frame == {"type": "heartbeat", "request_id": request_id}


def test_parse_message_strictness():
    parsed = protocol.parse_message('{"type": "gateway.connected", "protocol_version": 1}')
    assert parsed["type"] == "gateway.connected"
    with pytest.raises(ValueError):
        protocol.parse_message("[1, 2, 3]")
    with pytest.raises(ValueError):
        protocol.parse_message('{"no_type": true}')
    with pytest.raises(ValueError):
        protocol.parse_message('{"type": "x", "pad": "' + "a" * protocol.MAX_MESSAGE_BYTES + '"}')


def test_redact_strips_credential_shaped_fields():
    clean = protocol.redact({"state": "connected", "token": "oops", "nested": {"authorization": "Bearer x"}})
    assert clean["state"] == "connected"
    assert clean["token"] == "[redacted]"
    assert clean["nested"]["authorization"] == "[redacted]"


def test_shorten_gateway_id():
    assert protocol.shorten_gateway_id(TEST_GATEWAY_ID) == "gw_…_ABCD"
    assert protocol.shorten_gateway_id(None) is None


# ─── fake transport ──────────────────────────────────────────────────────────

class FakeConnection:
    def __init__(self):
        self.sent: list[str] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def push(self, message: dict) -> None:
        self._inbox.put_nowait(json.dumps(message))

    def push_close(self, code: int | None) -> None:
        self._inbox.put_nowait(TransportClosed(code=code))

    async def send(self, text: str) -> None:
        if self.closed:
            raise TransportClosed(code=None)
        self.sent.append(text)

    async def recv(self) -> str:
        item = await self._inbox.get()
        if isinstance(item, TransportClosed):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True

    def ack_last_heartbeat(self) -> str:
        frame = json.loads(self.sent[-1])
        assert frame["type"] == "heartbeat"
        self.push({"type": "heartbeat.ack", "request_id": frame["request_id"], "server_time_utc": "t"})
        return frame["request_id"]


def _connected_message(interval: float = 0.1, timeout: float = 0.5) -> dict:
    return {
        "type": "gateway.connected",
        "protocol_version": 1,
        "gateway_id": TEST_GATEWAY_ID,
        "session_id": "gws_test",
        "heartbeat_interval_seconds": interval,
        "heartbeat_timeout_seconds": timeout,
        "max_message_bytes": 4096,
        "server_time_utc": "t",
    }


class FakeTransport:
    """Scripted transport: each connect() consumes the next scenario item —
    a FakeConnection to hand out, or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.connect_calls: list[dict] = []
        self.connected = asyncio.Event()

    async def connect(self, url: str, *, bearer_token: str, user_agent: str):
        self.connect_calls.append({"url": url, "bearer_token": bearer_token, "user_agent": user_agent})
        if not self.script:
            await asyncio.sleep(3600)  # nothing scripted — park forever
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.connected.set()
        return item


def _agent(transport, *, relay_url: str = "ws://127.0.0.1:9999", credentials=(TEST_GATEWAY_ID, TEST_TOKEN), events=None):
    def on_event(event, fields):
        if events is not None:
            events.append((event, fields))

    return HomeGatewayAgent(
        transport=transport,
        credentials_provider=lambda: credentials,
        relay_url=relay_url,
        allow_insecure_localhost=True,
        on_event=on_event,
    )


async def wait_for_state(agent: HomeGatewayAgent, *states: GatewayState, timeout: float = 5.0) -> GatewayState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if agent.state in states:
            return agent.state
        await asyncio.sleep(0.01)
    raise AssertionError(f"agent never reached {states}; stuck in {agent.state}")


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    import remote_gateway.agent as agent_module

    monkeypatch.setattr(agent_module, "BACKOFF_SCHEDULE_SECONDS", (0.05, 0.05, 0.05, 0.05, 0.05))


# ─── agent state machine ─────────────────────────────────────────────────────

def test_agent_connects_and_heartbeats():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message(interval=0.05))
        transport = FakeTransport([conn])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)

        # heartbeat goes out on the server-provided interval with a valid request_id
        deadline = time.monotonic() + 3
        while not conn.sent and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        frame = json.loads(conn.sent[0])
        assert frame["type"] == "heartbeat"
        assert protocol.REQUEST_ID_PATTERN.fullmatch(frame["request_id"])

        conn.ack_last_heartbeat()
        await asyncio.sleep(0.1)
        assert agent.state is GatewayState.CONNECTED
        snapshot = agent.snapshot()
        assert snapshot["last_server_contact_at"] is not None
        await agent.stop()

    run_async(scenario())


def test_agent_snapshot_never_contains_token():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        transport = FakeTransport([conn])
        events: list = []
        agent = _agent(transport, events=events)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)
        await agent.stop()

        everything = json.dumps({"snapshot": agent.snapshot(), "events": [dict(f) for _e, f in events]})
        assert TEST_TOKEN not in everything
        assert "Bearer" not in everything
        # but the transport itself did receive the correct bearer token
        assert transport.connect_calls[0]["bearer_token"] == TEST_TOKEN
        assert transport.connect_calls[0]["url"].endswith(f"/v1/ws/gateway/{TEST_GATEWAY_ID}")

    run_async(scenario())


def test_agent_not_configured():
    async def scenario():
        agent = _agent(FakeTransport([]), credentials=None)
        agent.start()
        await wait_for_state(agent, GatewayState.NOT_CONFIGURED)
        assert agent.running is False or agent.state is GatewayState.NOT_CONFIGURED
        await agent.stop(final_state=GatewayState.NOT_CONFIGURED)

    run_async(scenario())


def test_agent_auth_failure_stops_auto_reconnect():
    async def scenario():
        transport = FakeTransport([TransportAuthError(403)])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.AUTHENTICATION_FAILED)
        await asyncio.sleep(0.3)
        assert len(transport.connect_calls) == 1  # no retry loop on bad credentials
        assert agent.snapshot()["safe_error_code"] == "auth_http_403"
        await agent.stop(final_state=GatewayState.AUTHENTICATION_FAILED)

    run_async(scenario())


def test_agent_close_4401_is_auth_failure():
    async def scenario():
        conn = FakeConnection()
        conn.push_close(protocol.CLOSE_UNAUTHORIZED)
        transport = FakeTransport([conn])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.AUTHENTICATION_FAILED)
        assert agent.snapshot()["last_close_code"] == 4401
        await agent.stop(final_state=GatewayState.AUTHENTICATION_FAILED)

    run_async(scenario())


def test_agent_connection_replaced_4001_stops_auto_reconnect():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        transport = FakeTransport([conn])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)
        conn.push_close(protocol.CLOSE_CONNECTION_REPLACED)
        await wait_for_state(agent, GatewayState.CONNECTION_REPLACED)
        await asyncio.sleep(0.3)
        assert len(transport.connect_calls) == 1  # replaced connection must not fight back
        assert agent.snapshot()["last_close_code"] == 4001
        # manual reconnect is still allowed
        conn2 = FakeConnection()
        conn2.push(_connected_message())
        transport.script.append(conn2)
        await agent.reconnect_now()
        await wait_for_state(agent, GatewayState.CONNECTED)
        await agent.stop()

    run_async(scenario())


def test_agent_rate_limited_waits_at_least_60_seconds(monkeypatch):
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        transport = FakeTransport([conn])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)
        before = time.time()
        conn.push_close(protocol.CLOSE_RATE_LIMITED)
        await wait_for_state(agent, GatewayState.RATE_LIMITED)
        await asyncio.sleep(0.05)  # let _sleep_backoff set next_reconnect_at
        snapshot = agent.snapshot()
        next_at = time.mktime(time.strptime(snapshot["next_reconnect_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        assert next_at - before >= 59  # min 60s delay (1s tolerance for rounding)
        assert len(transport.connect_calls) == 1  # not retried yet
        await agent.stop()

    run_async(scenario())


def test_agent_network_loss_reconnects_and_backoff_resets():
    async def scenario():
        conn1 = FakeConnection()
        conn1.push(_connected_message())
        conn2 = FakeConnection()
        conn2.push(_connected_message())
        transport = FakeTransport([conn1, OSError("network down"), conn2])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)

        conn1.push_close(1006)  # abnormal loss
        await wait_for_state(agent, GatewayState.RECONNECTING)
        await wait_for_state(agent, GatewayState.CONNECTED)  # via failed attempt then conn2
        assert len(transport.connect_calls) == 3
        # gateway.connected resets the backoff sequence
        assert agent.snapshot()["reconnect_attempt"] == 0
        await agent.stop()

    run_async(scenario())


def test_agent_tls_failure_is_failed_and_not_retried():
    async def scenario():
        transport = FakeTransport([TransportTlsError("certificate_verify_failed")])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.FAILED)
        await asyncio.sleep(0.3)
        assert len(transport.connect_calls) == 1
        assert "tls" in agent.snapshot()["safe_error_code"]
        await agent.stop(final_state=GatewayState.FAILED)

    run_async(scenario())


def test_agent_heartbeat_ack_timeout_forces_reconnect():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message(interval=0.05, timeout=0.15))  # ack never sent
        conn2 = FakeConnection()
        conn2.push(_connected_message())
        transport = FakeTransport([conn, conn2])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)
        # no ack pushed → ack deadline passes → reconnect
        await wait_for_state(agent, GatewayState.RECONNECTING)
        await wait_for_state(agent, GatewayState.CONNECTED)
        assert len(transport.connect_calls) == 2
        await agent.stop()

    run_async(scenario())


def test_agent_start_is_idempotent_single_task():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        transport = FakeTransport([conn])
        agent = _agent(transport)
        assert agent.start() is True
        assert agent.start() is False  # second start is a no-op, no parallel task
        await wait_for_state(agent, GatewayState.CONNECTED)
        assert len(transport.connect_calls) == 1
        await agent.stop()

    run_async(scenario())


def test_stale_connection_cannot_mutate_new_state():
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        transport = FakeTransport([conn])
        agent = _agent(transport)
        agent.start()
        await wait_for_state(agent, GatewayState.CONNECTED)

        old_generation = agent._generation
        await agent.stop(final_state=GatewayState.DISABLED)
        # a callback from the old run must be ignored now
        agent._set_state(old_generation, GatewayState.CONNECTED)
        assert agent.state is GatewayState.DISABLED

    run_async(scenario())


def test_agent_stop_cancels_quickly():
    async def scenario():
        transport = FakeTransport([])  # connect parks forever
        agent = _agent(transport)
        agent.start()
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await agent.stop()
        assert time.monotonic() - started < 6  # bounded shutdown
        assert agent.state is GatewayState.DISABLED
        assert agent.running is False

    run_async(scenario())


# ─── service ────────────────────────────────────────────────────────────────

class RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, event_type, console_message=None, **fields):
        self.events.append((event_type, fields))

    def error(self, event_type, console_message=None, **fields):
        self.events.append((event_type, fields))


def _service(tmp_path, *, enabled=True, transport=None, configured=True):
    store = _store(tmp_path)
    if configured:
        store.save(TEST_GATEWAY_ID, TEST_TOKEN)
    logger = RecordingLogger()
    service = RemoteGatewayService(
        credentials_store=store,
        relay_url_provider=lambda: "ws://127.0.0.1:9999",
        enabled_provider=lambda: enabled,
        logger=logger,
        transport=transport if transport is not None else FakeTransport([]),
        allow_insecure_localhost=True,
    )
    return service, logger


def test_service_startup_configured_and_enabled(tmp_path):
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        service, logger = _service(tmp_path, transport=FakeTransport([conn]))
        assert service.start_if_enabled() == "started"
        await wait_for_state(service._agent, GatewayState.CONNECTED)
        await service.stop()
        event_names = [name for name, _ in logger.events]
        assert "remote_gateway_starting" in event_names
        assert "remote_gateway_connected" in event_names
        assert "remote_gateway_stopped" in event_names

    run_async(scenario())


def test_service_startup_not_configured(tmp_path):
    async def scenario():
        service, _logger = _service(tmp_path, configured=False)
        assert service.start_if_enabled() == "not_configured"
        assert service.status()["state"] == "not_configured"
        assert service.status()["configured"] is False

    run_async(scenario())


def test_service_startup_disabled(tmp_path):
    async def scenario():
        service, _logger = _service(tmp_path, enabled=False)
        assert service.start_if_enabled() == "disabled"
        assert service.status()["state"] == "disabled"

    run_async(scenario())


def test_service_status_and_logs_never_contain_token(tmp_path):
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        service, logger = _service(tmp_path, transport=FakeTransport([conn]))
        service.start_if_enabled()
        await wait_for_state(service._agent, GatewayState.CONNECTED)
        await service.stop()

        everything = json.dumps({"status": service.status(), "events": [f for _n, f in logger.events]}, ensure_ascii=False)
        assert TEST_TOKEN not in everything
        assert "Bearer" not in everything
        assert TEST_GATEWAY_ID not in everything  # only the shortened id appears
        assert "gw_…_ABCD" in everything

    run_async(scenario())


def test_service_apply_enabled_toggles_agent(tmp_path):
    async def scenario():
        conn = FakeConnection()
        conn.push(_connected_message())
        service, _logger = _service(tmp_path, transport=FakeTransport([conn]))
        assert await service.apply_enabled(True) == "started"
        await wait_for_state(service._agent, GatewayState.CONNECTED)
        assert await service.apply_enabled(False) == "stopped"
        assert service._agent.running is False
        assert service.status()["state"] == "disabled"

    run_async(scenario())


# ─── backend API (api/server.py) ────────────────────────────────────────────

def test_remote_gateway_endpoints_and_settings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from storage.settings_store import SettingsStore

    import api.server as server

    conn = FakeConnection()
    conn.push(_connected_message())
    # A real service on a tmp store + fake transport, swapped in for the
    # module-level instance so no test touches DPAPI or the network.
    store = _store(tmp_path)
    store.save(TEST_GATEWAY_ID, TEST_TOKEN)
    service = RemoteGatewayService(
        credentials_store=store,
        relay_url_provider=lambda: "ws://127.0.0.1:9999",
        enabled_provider=lambda: server.config.REMOTE_GATEWAY_ENABLED,
        logger=RecordingLogger(),
        transport=FakeTransport([conn]),
        allow_insecure_localhost=True,
    )
    monkeypatch.setattr(server, "remote_gateway_service", service)
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    monkeypatch.setattr(server.config, "REMOTE_GATEWAY_ENABLED", False)

    client = TestClient(server.app)

    # status: safe fields only
    body = client.get("/api/remote-gateway/status").json()
    assert body["configured"] is True
    assert body["enabled"] is False
    assert TEST_TOKEN not in json.dumps(body)
    assert body["gateway_id"] == "gw_…_ABCD"

    # settings roundtrip persists the flag and applies it live
    response = client.post("/api/settings", json={"remote_gateway_enabled": True})
    assert response.status_code == 200
    assert response.json()["remote_gateway_enabled"] is True
    values, error = server.settings_store.load()
    assert error is None
    assert values["remote_gateway_enabled"] is True
    assert server.config.REMOTE_GATEWAY_ENABLED is True

    status = client.get("/api/remote-gateway/status").json()
    assert status["enabled"] is True
    # NOTE: `running` isn't asserted here — TestClient gives every request
    # its own event loop, so the background task can't be observed across
    # requests; single-loop start/stop mechanics are covered by
    # test_service_apply_enabled_toggles_agent above.

    # disable stops the agent
    client.post("/api/settings", json={"remote_gateway_enabled": False})
    status = client.get("/api/remote-gateway/status").json()
    assert status["running"] is False
    assert status["state"] == "disabled"

    # manual disconnect endpoint is idempotent + safe
    body = client.post("/api/remote-gateway/disconnect").json()
    assert body["disconnected"] is True

    # settings payload includes the flag; the payload never includes a token field
    settings_payload = client.get("/api/settings").json()
    assert "remote_gateway_enabled" in settings_payload
    assert "gateway_token" not in json.dumps(settings_payload)


def test_remote_gateway_connect_requires_configuration(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.server as server

    service = RemoteGatewayService(
        credentials_store=_store(tmp_path),  # empty — not configured
        relay_url_provider=lambda: "ws://127.0.0.1:9999",
        enabled_provider=lambda: True,
        logger=RecordingLogger(),
        transport=FakeTransport([]),
        allow_insecure_localhost=True,
    )
    monkeypatch.setattr(server, "remote_gateway_service", service)
    client = TestClient(server.app)
    assert client.post("/api/remote-gateway/connect").status_code == 409
    assert client.post("/api/remote-gateway/reconnect").status_code == 409


def test_lifespan_starts_and_stops_service(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.server as server

    calls: list[str] = []

    class StubService:
        def start_if_enabled(self):
            calls.append("start")
            return "started"

        async def stop(self):
            calls.append("stop")

    monkeypatch.setattr(server, "remote_gateway_service", StubService())
    with TestClient(server.app):
        pass
    assert calls == ["start", "stop"]


# ─── integration smoke: real local fake Relay ────────────────────────────────

def test_fake_relay_integration_smoke(tmp_path):
    """Full loop over a real websocket: Bearer auth → gateway.connected →
    heartbeat → ack → server closes → agent goes Reconnecting. No secrets
    in any produced output."""

    async def scenario():
        from websockets.asyncio.server import serve

        seen: dict = {"auth": None, "heartbeats": 0}
        close_after_ack = asyncio.Event()

        async def handler(websocket):
            seen["auth"] = websocket.request.headers.get("authorization", "")
            if seen["auth"] != f"Bearer {TEST_TOKEN}":
                await websocket.close(4401, "Unauthorized")
                return
            await websocket.send(json.dumps(_connected_message(interval=0.05, timeout=5)))
            frame = json.loads(await websocket.recv())
            assert frame["type"] == "heartbeat"
            assert protocol.REQUEST_ID_PATTERN.fullmatch(frame["request_id"])
            seen["heartbeats"] += 1
            await websocket.send(json.dumps({
                "type": "heartbeat.ack", "request_id": frame["request_id"], "server_time_utc": "t",
            }))
            await close_after_ack.wait()

        async with serve(handler, "127.0.0.1", 0) as server_instance:
            port = server_instance.sockets[0].getsockname()[1]

            store = _store(tmp_path)
            store.save(TEST_GATEWAY_ID, TEST_TOKEN)
            logger = RecordingLogger()
            service = RemoteGatewayService(
                credentials_store=store,
                relay_url_provider=lambda: f"ws://127.0.0.1:{port}",
                enabled_provider=lambda: True,
                logger=logger,
                transport=None,  # the REAL WebsocketsTransport
                allow_insecure_localhost=True,
            )
            assert service.start_if_enabled() == "started"
            await wait_for_state(service._agent, GatewayState.CONNECTED, timeout=10)

            deadline = time.monotonic() + 5
            while seen["heartbeats"] == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert seen["heartbeats"] >= 1
            assert service._agent.snapshot()["last_server_contact_at"] is not None

            # server drops the connection → agent reconnects
            close_after_ack.set()
            await wait_for_state(
                service._agent, GatewayState.RECONNECTING, GatewayState.CONNECTING, GatewayState.CONNECTED, timeout=10,
            )
            await service.stop()

            everything = json.dumps([f for _n, f in logger.events], ensure_ascii=False) + json.dumps(service.status(), ensure_ascii=False)
            assert TEST_TOKEN not in everything
            assert "Bearer" not in everything

    run_async(scenario(), timeout=30)
