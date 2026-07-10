"""Integration tests for GET/POST /api/settings (api/server.py) — real
FastAPI TestClient against the real app object, same pattern as
tests/test_attachment_persistence.py. `server.settings_store` is
monkeypatched to a tmp_path-backed SettingsStore so these tests never read
or write the real storage/settings.json.

Covers the task's "invalid values are sanitized or rejected" requirement at
the actual endpoint layer (storage/settings_store.py's own load/save
mechanics are covered separately in tests/test_settings_store.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from storage.settings_store import SettingsStore  # noqa: E402


def _client_with_temp_settings(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    return TestClient(server.app)


def test_get_settings_returns_current_values(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert "num_ctx" in body
    assert "enable_ocr" in body
    assert "stt_language" in body


def test_get_settings_includes_ui_preference_defaults(monkeypatch, tmp_path):
    # Settings Pass 2 — pure UI/display preferences must be present in GET
    # with safe defaults matching the current (untouched) look: dark theme,
    # sienna accent, default font size/density, timestamps + typing animation on.
    #
    # config.* are process-wide globals seeded once at import time from
    # whatever's actually on disk in the real storage/settings.json — not
    # guaranteed to already be at these literal defaults in a shared test
    # process (e.g. a developer who has genuinely customized accent_color/
    # ui_density via the real app). Pin them explicitly so this test verifies
    # the payload accurately reflects config, not that a fresh process
    # happens to start at these values.
    monkeypatch.setattr(server.config, "APPEARANCE_THEME", "dark")
    monkeypatch.setattr(server.config, "ACCENT_COLOR", "sienna")
    monkeypatch.setattr(server.config, "UI_FONT_SIZE", "default")
    monkeypatch.setattr(server.config, "UI_DENSITY", "comfortable")
    monkeypatch.setattr(server.config, "SHOW_MESSAGE_TIMESTAMPS", True)
    monkeypatch.setattr(server.config, "SHOW_TYPING_ANIMATION", True)
    monkeypatch.setattr(server.config, "COPY_BEFORE_CLEAR_CHAT", False)
    monkeypatch.setattr(server.config, "STARTUP_PAGE", "chat")
    monkeypatch.setattr(server.config, "CODE_FONT_SIZE", "default")
    monkeypatch.setattr(server.config, "CODE_LINE_WRAP", False)

    client = _client_with_temp_settings(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["appearance_theme"] == "dark"
    assert body["accent_color"] == "sienna"
    assert body["ui_font_size"] == "default"
    assert body["ui_density"] == "comfortable"
    assert body["show_message_timestamps"] is True
    assert body["show_typing_animation"] is True
    assert body["copy_before_clear_chat"] is False
    assert body["startup_page"] == "chat"
    assert body["code_font_size"] == "default"
    assert body["code_line_wrap"] is False


def test_post_settings_ui_preferences_roundtrip(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "appearance_theme": "light",
            "accent_color": "violet",
            "ui_font_size": "small",
            "ui_density": "compact",
            "show_message_timestamps": False,
            "show_typing_animation": False,
            "copy_before_clear_chat": True,
            "startup_page": "settings",
            "code_font_size": "large",
            "code_line_wrap": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["appearance_theme"] == "light"
    assert body["accent_color"] == "violet"
    assert body["ui_font_size"] == "small"
    assert body["ui_density"] == "compact"
    assert body["show_message_timestamps"] is False
    assert body["show_typing_animation"] is False
    assert body["copy_before_clear_chat"] is True
    assert body["startup_page"] == "settings"
    assert body["code_font_size"] == "large"
    assert body["code_line_wrap"] is True

    # Applied live to config.*.
    assert server.config.APPEARANCE_THEME == "light"
    assert server.config.ACCENT_COLOR == "violet"
    assert server.config.STARTUP_PAGE == "settings"

    # Persisted to the (tmp) settings.json.
    values, error = server.settings_store.load()
    assert error is None
    assert values["appearance_theme"] == "light"
    assert values["startup_page"] == "settings"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("appearance_theme", "purple"),
        ("accent_color", "chartreuse"),
        ("ui_font_size", "huge"),
        ("ui_density", "sparse"),
        ("startup_page", "games"),
        ("code_font_size", "tiny"),
    ],
)
def test_post_settings_invalid_ui_preference_enum_rejected(monkeypatch, tmp_path, field, bad_value):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={field: bad_value})
    assert response.status_code == 400


def test_get_settings_includes_pass3_defaults(monkeypatch, tmp_path):
    # Pinned for the same reason as test_get_settings_includes_ui_preference_defaults
    # above — config.* are shared process-wide globals, not guaranteed to
    # already be at these defaults.
    monkeypatch.setattr(server.config, "CODE_SYNTAX_HIGHLIGHTING", True)
    monkeypatch.setattr(server.config, "CODE_SHOW_LINE_NUMBERS", True)
    monkeypatch.setattr(server.config, "CODE_SHOW_LANGUAGE_BADGE", True)
    monkeypatch.setattr(server.config, "CODE_SHOW_COPY_BUTTON", True)
    monkeypatch.setattr(server.config, "CODE_SHOW_COLLAPSE_BUTTON", True)
    monkeypatch.setattr(server.config, "CODE_SHOW_SAVE_BUTTON", True)
    monkeypatch.setattr(server.config, "SHOW_EXPERIMENTAL_STREAM_BUTTON", True)
    monkeypatch.setattr(server.config, "PREFERRED_RESPONSE_LANGUAGE", "auto")
    monkeypatch.setattr(server.config, "INTERFACE_LANGUAGE", "en")

    client = _client_with_temp_settings(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["code_syntax_highlighting"] is True
    assert body["code_show_line_numbers"] is True
    assert body["code_show_language_badge"] is True
    assert body["code_show_copy_button"] is True
    assert body["code_show_collapse_button"] is True
    assert body["code_show_save_button"] is True
    assert body["show_experimental_stream_button"] is True
    assert body["preferred_response_language"] == "auto"
    assert body["interface_language"] == "en"


def test_post_settings_pass3_fields_roundtrip(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "code_syntax_highlighting": False,
            "code_show_line_numbers": False,
            "code_show_language_badge": False,
            "code_show_copy_button": False,
            "code_show_collapse_button": False,
            "code_show_save_button": False,
            "show_experimental_stream_button": False,
            "preferred_response_language": "ru",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code_syntax_highlighting"] is False
    assert body["show_experimental_stream_button"] is False
    assert body["preferred_response_language"] == "ru"

    assert server.config.CODE_SYNTAX_HIGHLIGHTING is False
    assert server.config.SHOW_EXPERIMENTAL_STREAM_BUTTON is False
    assert server.config.PREFERRED_RESPONSE_LANGUAGE == "ru"

    values, error = server.settings_store.load()
    assert error is None
    assert values["preferred_response_language"] == "ru"

    # Restore defaults so this test doesn't leak globally-mutated config
    # state (config.* are shared process-wide globals) into whichever test
    # happens to run after it in the same process.
    client.post(
        "/api/settings",
        json={
            "code_syntax_highlighting": True,
            "code_show_line_numbers": True,
            "code_show_language_badge": True,
            "code_show_copy_button": True,
            "code_show_collapse_button": True,
            "code_show_save_button": True,
            "show_experimental_stream_button": True,
            "preferred_response_language": "auto",
        },
    )


def test_post_settings_invalid_preferred_response_language_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = server.config.PREFERRED_RESPONSE_LANGUAGE
    response = client.post("/api/settings", json={"preferred_response_language": "fr"})
    assert response.status_code == 400
    assert server.config.PREFERRED_RESPONSE_LANGUAGE == before


def test_post_settings_interface_language_roundtrip(monkeypatch, tmp_path):
    # Real UI localization pass — interface_language is the application UI
    # language selector (Settings > Language > Interface language), distinct
    # from stt_language/preferred_response_language above.
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"interface_language": "ru"})
    assert response.status_code == 200
    body = response.json()
    assert body["interface_language"] == "ru"
    assert server.config.INTERFACE_LANGUAGE == "ru"

    values, error = server.settings_store.load()
    assert error is None
    assert values["interface_language"] == "ru"

    # Restore so this test doesn't leak globally-mutated config state.
    client.post("/api/settings", json={"interface_language": "en"})


def test_post_settings_invalid_interface_language_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = server.config.INTERFACE_LANGUAGE
    response = client.post("/api/settings", json={"interface_language": "de"})
    assert response.status_code == 400
    assert server.config.INTERFACE_LANGUAGE == before


def test_post_settings_persists_and_applies(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"num_ctx": 16384, "log_level": "debug"})
    assert response.status_code == 200
    body = response.json()
    assert body["num_ctx"] == 16384
    assert body["log_level"] == "debug"

    # Applied live to config.*, not just returned in the response.
    assert server.config.OLLAMA_NUM_CTX == 16384
    assert server.config.LOG_LEVEL == "debug"

    # Persisted to the (tmp) settings.json.
    values, error = server.settings_store.load()
    assert error is None
    assert values["num_ctx"] == 16384
    assert values["log_level"] == "debug"


def test_post_settings_invalid_log_level_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = server.config.LOG_LEVEL
    response = client.post("/api/settings", json={"log_level": "not_a_real_level"})
    assert response.status_code == 400
    assert server.config.LOG_LEVEL == before  # rejected change must not apply


def test_post_settings_invalid_num_ctx_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = server.config.OLLAMA_NUM_CTX
    response = client.post("/api/settings", json={"num_ctx": 10})
    assert response.status_code == 400
    assert server.config.OLLAMA_NUM_CTX == before


def test_post_settings_num_predict_zero_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"num_predict": 0})
    assert response.status_code == 400


def test_post_settings_invalid_stt_language_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    before = server.config.WHISPER_CPP_LANGUAGE
    response = client.post("/api/settings", json={"stt_language": "fr"})
    assert response.status_code == 400
    assert server.config.WHISPER_CPP_LANGUAGE == before


def test_post_settings_valid_stt_language_accepted(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"stt_language": "en"})
    assert response.status_code == 200
    assert response.json()["stt_language"] == "en"
    assert server.config.WHISPER_CPP_LANGUAGE == "en"


def test_post_settings_invalid_max_context_messages_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"max_context_messages": 0})
    assert response.status_code == 400


def test_post_settings_invalid_request_timeout_rejected(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={"request_timeout_seconds": 0})
    assert response.status_code == 400


def test_post_settings_toggle_feature_flags(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "enable_ocr": False,
            "enable_image_understanding": False,
            "enable_translator": False,
            "enable_code_specialist_auto": False,
            "enable_reviewer_explicit": False,
        },
    )
    assert response.status_code == 200
    assert server.config.ENABLE_OCR is False
    assert server.config.ENABLE_IMAGE_UNDERSTANDING is False
    assert server.config.ENABLE_TRANSLATOR is False
    assert server.config.ENABLE_CODE_SPECIALIST_AUTO is False
    assert server.config.ENABLE_REVIEWER_EXPLICIT is False

    # Restore so this test doesn't leak globally-mutated config state into
    # whichever test happens to run after it in the same process.
    client.post(
        "/api/settings",
        json={
            "enable_ocr": True,
            "enable_image_understanding": True,
            "enable_translator": True,
            "enable_code_specialist_auto": True,
            "enable_reviewer_explicit": True,
        },
    )


def test_post_settings_non_persistable_field_applies_live_but_not_written_to_disk(monkeypatch, tmp_path):
    # ollama_host is accepted and applied live (api/server.py) but
    # deliberately not in PERSISTABLE_FIELDS — HANDOFF_v2.md §6.
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    original_host = server.config.OLLAMA_HOST
    try:
        response = client.post("/api/settings", json={"ollama_host": "http://127.0.0.1:11434"})
        assert response.status_code == 200
        values, _ = server.settings_store.load()
        assert "ollama_host" not in values
    finally:
        monkeypatch.setattr(server.config, "OLLAMA_HOST", original_host)
        server._rebuild_ollama_client()


def test_post_settings_empty_body_is_a_no_op(monkeypatch, tmp_path):
    client = _client_with_temp_settings(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={})
    assert response.status_code == 200


def test_settings_json_survives_utf8_bom_on_disk(monkeypatch, tmp_path):
    # End-to-end version of tests/test_settings_store.py's BOM test, but
    # through the real GET /api/settings endpoint and the real startup-load
    # code path's error handling contract (settings_store.load() must not
    # raise, api/server.py must not crash constructing its response).
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"num_ctx": 8192}')
    monkeypatch.setattr(server, "settings_store", SettingsStore(path))

    client = TestClient(server.app)
    response = client.get("/api/settings")
    assert response.status_code == 200

    values, error = server.settings_store.load()
    assert error is None
    assert values == {"num_ctx": 8192}
