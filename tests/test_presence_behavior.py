"""Presence Behavior Layer (0.2.1, Phase 2) — welcome-back events, recent
event dismissal, POST /api/presence/activity, and the No Chat Pollution
rule. Endpoint tests use the real FastAPI TestClient (same pattern as
tests/test_presence_endpoint.py); the service-level welcome-back logic is
also covered directly on PresenceService.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from presence.presence_service import PresenceService, PresenceSettings  # noqa: E402
from storage.settings_store import SettingsStore  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_presence_service(monkeypatch):
    monkeypatch.setattr(server, "presence_service", PresenceService())
    monkeypatch.setattr(server.config, "ENABLE_PRESENCE", True)
    monkeypatch.setattr(server.config, "PRESENCE_IDLE_MINUTES", 15)
    monkeypatch.setattr(server.config, "PRESENCE_MAX_MESSAGES_PER_HOUR", 2)
    monkeypatch.setattr(server.config, "PRESENCE_QUIET_HOURS_ENABLED", False)
    monkeypatch.setattr(server.config, "PRESENCE_QUIET_HOURS_START", "23:00")
    monkeypatch.setattr(server.config, "PRESENCE_QUIET_HOURS_END", "08:00")
    monkeypatch.setattr(server.config, "PRESENCE_STYLE", "calm")
    monkeypatch.setattr(server.config, "ALLOW_PROACTIVE_PRESENCE_MESSAGES", False)
    monkeypatch.setattr(server.config, "SHOW_PRESENCE_CARD", True)
    monkeypatch.setattr(server.config, "PRESENCE_SHOW_WELCOME_BACK", True)
    monkeypatch.setattr(server.config, "PRESENCE_SHOW_RECENT_EVENT", True)
    monkeypatch.setattr(server.config, "PRESENCE_ALLOW_INSERT_TO_CHAT", True)
    monkeypatch.setattr(server.config, "PRESENCE_MIN_SECONDS_BETWEEN_UI_MESSAGES", 60)


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    return TestClient(server.app)


def _make_idle(idle_seconds: float = 3600.0) -> None:
    """Simulates a long-idle user by rewinding the in-memory activity
    anchor — the same thing a real 15+ minute gap produces, without waiting."""
    server.presence_service._last_user_activity_at = time.time() - idle_seconds


def _settings(**overrides) -> PresenceSettings:
    base = dict(
        enabled=True,
        idle_minutes=15,
        quiet_hours_enabled=False,
        quiet_hours_start="23:00",
        quiet_hours_end="08:00",
        style="calm",
        max_messages_per_hour=2,
        show_welcome_back=True,
        min_seconds_between_ui_messages=60,
    )
    base.update(overrides)
    return PresenceSettings(**base)


# --- welcome back: endpoint level -------------------------------------------

def test_welcome_back_created_after_idle_and_ping(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _make_idle()
    body = client.post("/api/presence/ping").json()
    event = body["recent_event"]
    assert event is not None
    assert event["type"] == "welcome_back"
    assert event["style"] == "calm"
    assert isinstance(event["message"], str) and event["message"]
    # And the event survives into the next status poll.
    assert client.get("/api/presence/status").json()["recent_event"]["type"] == "welcome_back"


def test_no_welcome_back_without_idle_gap(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.post("/api/presence/ping").json()
    assert body["recent_event"] is None


def test_no_welcome_back_in_quiet_mode(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/presence/quiet")
    _make_idle()
    body = client.post("/api/presence/ping").json()
    assert body["recent_event"] is None
    assert body["state"] == "quiet"


def test_no_welcome_back_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "PRESENCE_SHOW_WELCOME_BACK", False)
    client = _client(monkeypatch, tmp_path)
    _make_idle()
    body = client.post("/api/presence/ping").json()
    assert body["recent_event"] is None


def test_quiet_hours_suppress_welcome_back(monkeypatch, tmp_path):
    import presence.presence_service as mod

    monkeypatch.setattr(server.config, "PRESENCE_QUIET_HOURS_ENABLED", True)
    fake_struct = mod.time.struct_time((2026, 1, 1, 23, 30, 0, 0, 1, 0))
    monkeypatch.setattr(mod.time, "localtime", lambda *_: fake_struct)

    client = _client(monkeypatch, tmp_path)
    _make_idle()
    body = client.post("/api/presence/ping").json()
    assert body["recent_event"] is None
    assert body["state"] == "quiet"


def test_welcome_back_throttled_by_min_seconds(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _make_idle()
    first = client.post("/api/presence/ping").json()
    assert first["recent_event"]["type"] == "welcome_back"
    first_created_at = first["recent_event"]["created_at"]

    # Immediately idle again and return — inside the 60s window, so the
    # second return must NOT mint a new event (the first one is kept).
    _make_idle()
    second = client.post("/api/presence/ping").json()
    assert second["recent_event"]["created_at"] == first_created_at


def test_welcome_back_style_deterministic(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "PRESENCE_STYLE", "playful")
    client = _client(monkeypatch, tmp_path)
    _make_idle()
    event = client.post("/api/presence/ping").json()["recent_event"]
    assert event["style"] == "playful"
    assert event["variant"] == 0
    from presence.presence_service import _WELCOME_BACK_POOL

    assert event["message"] == _WELCOME_BACK_POOL["playful"][0]


# --- welcome back: service level --------------------------------------------

def test_service_welcome_back_outcomes():
    service = PresenceService()

    disabled = service.maybe_create_welcome_back(_settings(show_welcome_back=False))
    assert disabled.outcome == "disabled"

    service.enable_quiet(_settings())
    quiet = service.maybe_create_welcome_back(_settings())
    assert quiet.outcome == "skipped_quiet"
    service.disable_quiet(_settings())

    created = service.maybe_create_welcome_back(_settings())
    assert created.outcome == "created"
    assert created.event["type"] == "welcome_back"

    throttled = service.maybe_create_welcome_back(_settings())
    assert throttled.outcome == "throttled"

    offline = PresenceService().maybe_create_welcome_back(_settings(enabled=False))
    assert offline.outcome == "not_applicable"


def test_service_welcome_back_variants_rotate():
    service = PresenceService()
    settings = _settings(min_seconds_between_ui_messages=0)
    variants = [service.maybe_create_welcome_back(settings).event["variant"] for _ in range(4)]
    assert variants == [0, 1, 2, 0]


# --- recent event / dismiss ---------------------------------------------------

def test_say_sets_recent_event_and_dismiss_clears_it(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    say = client.post("/api/presence/say").json()
    assert say["throttled"] is False
    assert say["variant"] == 0

    status = client.get("/api/presence/status").json()
    assert status["recent_event"]["type"] == "say"
    assert status["recent_event"]["message"] == say["message"]

    dismissed = client.post("/api/presence/event/dismiss").json()
    assert dismissed["dismissed"] is True
    assert dismissed["recent_event"] is None
    assert client.get("/api/presence/status").json()["recent_event"] is None

    # Dismissing again is a harmless no-op, not an error.
    assert client.post("/api/presence/event/dismiss").json()["dismissed"] is False


# --- POST /api/presence/activity ----------------------------------------------

def test_frontend_activity_listening_speaking_available(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.post("/api/presence/activity", json={"activity": "listening", "source": "frontend"}).json()
    assert body["state"] == "listening"

    body = client.post("/api/presence/activity", json={"activity": "speaking", "source": "frontend"}).json()
    assert body["state"] == "speaking"

    body = client.post("/api/presence/activity", json={"activity": "available", "source": "frontend"}).json()
    assert body["state"] == "available"


def test_frontend_activity_invalid_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/api/presence/activity", json={"activity": "thinking", "source": "frontend"})
    assert response.status_code == 400


def test_frontend_available_cannot_wipe_backend_thinking(monkeypatch, tmp_path):
    # A chat turn sets "thinking" backend-side; a stale playback-finished
    # "available" from the frontend must not clear it (only_if guard).
    client = _client(monkeypatch, tmp_path)
    server.presence_service.set_activity("thinking", server._presence_settings())
    body = client.post("/api/presence/activity", json={"activity": "available", "source": "frontend"}).json()
    assert body["state"] == "thinking"


# --- No Chat Pollution rule ----------------------------------------------------

class _LLMBomb:
    """Any attribute access explodes — proves an endpoint never touched the
    Ollama client at all."""

    def __getattr__(self, name):
        raise AssertionError(f"presence endpoint touched the LLM client (.{name})")


def test_presence_endpoints_never_touch_llm_or_chat_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    appended: list = []
    monkeypatch.setattr(
        server.conversation_store, "append_message",
        lambda *a, **k: appended.append((a, k)),
    )
    monkeypatch.setattr(server, "ollama_client", _LLMBomb())
    monkeypatch.setattr(server, "_build_routed_client", lambda model: _LLMBomb())

    _make_idle()
    client.post("/api/presence/ping")            # creates a welcome-back event
    client.get("/api/presence/status")
    client.post("/api/presence/say")
    client.post("/api/presence/event/dismiss")
    client.post("/api/presence/quiet")
    client.post("/api/presence/wake")
    client.post("/api/presence/activity", json={"activity": "listening"})
    client.post("/api/presence/activity", json={"activity": "available"})
    client.get("/api/presence/events")

    assert appended == []  # nothing ever written into conversation history


# --- Phase 2 settings ------------------------------------------------------------

def test_phase2_settings_defaults_and_roundtrip(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["presence_show_welcome_back"] is True
    assert body["presence_show_recent_event"] is True
    assert body["presence_allow_insert_to_chat"] is True
    assert body["presence_min_seconds_between_ui_messages"] == 60

    response = client.post(
        "/api/settings",
        json={
            "presence_show_welcome_back": False,
            "presence_show_recent_event": False,
            "presence_allow_insert_to_chat": False,
            "presence_min_seconds_between_ui_messages": 120,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["presence_show_welcome_back"] is False
    assert body["presence_min_seconds_between_ui_messages"] == 120
    assert server.config.PRESENCE_SHOW_WELCOME_BACK is False

    values, error = server.settings_store.load()
    assert error is None
    assert values["presence_show_welcome_back"] is False
    assert values["presence_min_seconds_between_ui_messages"] == 120


def test_phase2_invalid_min_seconds_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"presence_min_seconds_between_ui_messages": -1})
    assert response.status_code == 400
