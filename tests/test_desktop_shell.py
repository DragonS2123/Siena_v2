"""Desktop Presence Shell (0.2.2) — GET /api/health and the tray/window
settings (enable_tray_icon / minimize_to_tray / close_to_tray /
show_tray_notifications / auto_start_backend_with_desktop). Same real
FastAPI TestClient pattern as tests/test_settings_endpoint.py. The actual
tray behavior lives in electron/main.cjs and is covered by the manual
Electron smoke, not here — the backend's whole job for these settings is
store/validate/echo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from storage.settings_store import SettingsStore  # noqa: E402


@pytest.fixture(autouse=True)
def _shell_defaults(monkeypatch):
    monkeypatch.setattr(server.config, "ENABLE_TRAY_ICON", True)
    monkeypatch.setattr(server.config, "MINIMIZE_TO_TRAY", True)
    monkeypatch.setattr(server.config, "CLOSE_TO_TRAY", True)
    monkeypatch.setattr(server.config, "SHOW_TRAY_NOTIFICATIONS", False)
    monkeypatch.setattr(server.config, "AUTO_START_BACKEND_WITH_DESKTOP", False)


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    return TestClient(server.app)


# --- GET /api/health ---------------------------------------------------------

def test_health_endpoint_ok(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["app"] == "Siena v2"
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["time"], str) and body["time"]


def test_health_version_matches_package_json(monkeypatch, tmp_path):
    # /api/health must report the same canonical version every APP_VERSION
    # display reads (frontend package.json) — not a second hand-typed one.
    import json

    package_json = Path(__file__).resolve().parent.parent / "Siena v2 Control Panel UI" / "package.json"
    expected = json.loads(package_json.read_text(encoding="utf-8-sig"))["version"]

    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/health").json()["version"] == expected


# --- shell settings ----------------------------------------------------------

def test_shell_settings_defaults(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["enable_tray_icon"] is True
    assert body["minimize_to_tray"] is True
    assert body["close_to_tray"] is True
    assert body["show_tray_notifications"] is False
    assert body["auto_start_backend_with_desktop"] is False


def test_shell_settings_roundtrip_and_persist(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "enable_tray_icon": False,
            "minimize_to_tray": False,
            "close_to_tray": False,
            "show_tray_notifications": True,
            "auto_start_backend_with_desktop": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enable_tray_icon"] is False
    assert body["show_tray_notifications"] is True
    assert body["auto_start_backend_with_desktop"] is True

    # Applied to config.*.
    assert server.config.ENABLE_TRAY_ICON is False
    assert server.config.CLOSE_TO_TRAY is False

    # Persisted to the (tmp) settings.json — this file is exactly what
    # electron/main.cjs reads, so persistence here IS the live contract.
    values, error = server.settings_store.load()
    assert error is None
    assert values["enable_tray_icon"] is False
    assert values["minimize_to_tray"] is False
    assert values["close_to_tray"] is False
    assert values["show_tray_notifications"] is True
    assert values["auto_start_backend_with_desktop"] is True
