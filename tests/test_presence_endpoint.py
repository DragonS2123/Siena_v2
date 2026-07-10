"""Integration tests for GET/POST /api/presence/* and the presence_* fields
on GET/POST /api/settings (api/server.py) — real FastAPI TestClient against
the real app object, same pattern as tests/test_settings_endpoint.py.

`server.presence_service` is reset to a fresh PresenceService() before every
test (it's a process-wide singleton, same discipline as config.* — see the
restore-defaults calls at the end of mutating tests) so tests don't leak
idle/quiet state into each other.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from presence.presence_service import PresenceService  # noqa: E402
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


def _client_with_temp_settings(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    return TestClient(server.app)


def test_presence_status_default(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.get("/api/presence/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "available"
    assert body["is_quiet_mode"] is False
    assert "last_user_activity_at" in body
    assert "uptime_seconds" in body


def test_presence_status_respects_enable_presence(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "ENABLE_PRESENCE", False)
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    body = client.get("/api/presence/status").json()
    assert body["state"] == "offline"


def test_presence_ping_updates_activity_timestamp(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = client.get("/api/presence/status").json()["last_user_activity_at"]
    response = client.post("/api/presence/ping")
    assert response.status_code == 200
    after = response.json()["last_user_activity_at"]
    assert after >= before


def test_presence_quiet_mode_enable_and_status(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/presence/quiet")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "quiet"
    assert body["is_quiet_mode"] is True

    status = client.get("/api/presence/status").json()
    assert status["state"] == "quiet"


def test_presence_wake_disables_quiet_mode(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    client.post("/api/presence/quiet")
    response = client.post("/api/presence/wake")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "available"
    assert body["is_quiet_mode"] is False


def test_presence_say_returns_deterministic_message(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/presence/say")
    assert response.status_code == 200
    body = response.json()
    assert body["throttled"] is False
    assert isinstance(body["message"], str) and body["message"]


def test_presence_say_throttled_after_max_per_hour(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "PRESENCE_MAX_MESSAGES_PER_HOUR", 1)
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    first = client.post("/api/presence/say").json()
    second = client.post("/api/presence/say").json()
    assert first["throttled"] is False
    assert second["throttled"] is True
    assert second["message"] is None


def test_get_settings_includes_presence_defaults(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["enable_presence"] is True
    assert body["allow_proactive_presence_messages"] is False
    assert body["presence_idle_minutes"] == 15
    assert body["presence_max_messages_per_hour"] == 2
    assert body["presence_quiet_hours_enabled"] is False
    assert body["presence_quiet_hours_start"] == "23:00"
    assert body["presence_quiet_hours_end"] == "08:00"
    assert body["presence_style"] == "calm"
    assert body["show_presence_card"] is True


def test_post_settings_presence_roundtrip(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "enable_presence": False,
            "allow_proactive_presence_messages": True,
            "presence_idle_minutes": 30,
            "presence_max_messages_per_hour": 5,
            "presence_quiet_hours_enabled": True,
            "presence_quiet_hours_start": "22:00",
            "presence_quiet_hours_end": "07:30",
            "presence_style": "playful",
            "show_presence_card": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enable_presence"] is False
    assert body["allow_proactive_presence_messages"] is True
    assert body["presence_idle_minutes"] == 30
    assert body["presence_quiet_hours_start"] == "22:00"
    assert body["presence_style"] == "playful"
    assert body["show_presence_card"] is False

    assert server.config.ENABLE_PRESENCE is False
    assert server.config.PRESENCE_STYLE == "playful"

    values, error = server.settings_store.load()
    assert error is None
    assert values["presence_idle_minutes"] == 30
    assert values["presence_style"] == "playful"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("presence_idle_minutes", 0),
        ("presence_max_messages_per_hour", -1),
        ("presence_style", "moody"),
        ("presence_quiet_hours_start", "25:99"),
        ("presence_quiet_hours_end", "not-a-time"),
    ],
)
def test_post_settings_invalid_presence_fields_rejected(monkeypatch, tmp_path, field, bad_value):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={field: bad_value})
    assert response.status_code == 400


def test_presence_endpoints_present_in_openapi_but_not_toolable(monkeypatch, tmp_path):
    # Human-in-the-loop-style guarantee (mirrors the candidate_memory pattern,
    # ARCHITECTURE.md): quiet/wake/say are plain REST actions, never
    # registered as tools the model itself could call.
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    tool_names = client.get("/api/tools").json()["tools"]
    names = {t["name"] for t in tool_names}
    assert "presence_quiet" not in names
    assert "presence_wake" not in names
    assert "presence_say" not in names
