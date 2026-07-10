"""Settings persistence (storage/settings_store.py) — load/save mechanics
only; the /api/settings endpoint's own field-level validation (log_level in
_LOG_LEVELS, num_ctx >= 512, etc., api/server.py::update_settings) lives in
api/server.py and isn't covered here since importing that module triggers
heavy side effects (Ollama client construction, registry build, settings
load) not designed for test isolation — same convention every other test in
this directory already follows.

Bugfix covered here: settings.json may be saved with a UTF-8 BOM (e.g. a
human editing it in Notepad, which writes "UTF-8" as UTF-8-with-BOM by
default) — json.loads() rejects a BOM'd file outright since it isn't valid
JSON syntax, which used to surface as a spurious settings_load_failed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.settings_store import PERSISTABLE_FIELDS, SettingsStore  # noqa: E402


def test_load_missing_file_returns_empty_defaults(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    values, error = store.load()
    assert values == {}
    assert error is None


def test_load_empty_file_returns_empty_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert values == {}
    assert error is None


def test_save_then_load_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"num_ctx": 16384, "log_level": "debug", "enable_ocr": False})
    values, error = store.load()
    assert error is None
    assert values == {"num_ctx": 16384, "log_level": "debug", "enable_ocr": False}


def test_save_merges_with_existing_content_instead_of_clobbering(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"num_ctx": 16384, "log_level": "debug"})
    store.save({"enable_ocr": False})  # a second, unrelated save later
    values, _ = store.load()
    assert values == {"num_ctx": 16384, "log_level": "debug", "enable_ocr": False}


def test_save_updates_existing_field(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"log_level": "debug"})
    store.save({"log_level": "warn"})
    values, _ = store.load()
    assert values == {"log_level": "warn"}


def test_load_tolerates_utf8_bom(tmp_path):
    path = tmp_path / "settings.json"
    payload = json.dumps({"num_ctx": 8192, "log_level": "debug"}).encode("utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + payload)  # UTF-8 BOM prefix
    store = SettingsStore(path)
    values, error = store.load()
    assert error is None
    assert values == {"num_ctx": 8192, "log_level": "debug"}


def test_load_tolerates_utf8_bom_with_no_bom_too(tmp_path):
    # utf-8-sig must behave identically to plain utf-8 when there's no BOM —
    # this is what makes it a safe universal replacement, not a special case.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"stt_language": "ru"}), encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert error is None
    assert values == {"stt_language": "ru"}


def test_load_malformed_json_does_not_raise(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json!!", encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert values == {}
    assert error is not None


def test_load_non_object_top_level_does_not_raise(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert values == {}
    assert error is not None


def test_load_filters_unknown_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"num_ctx": 8192, "totally_unknown_field": "x", "ollama_host": "http://evil"}), encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert error is None
    assert values == {"num_ctx": 8192}
    assert "totally_unknown_field" not in values
    assert "ollama_host" not in values  # accepted by the API but deliberately not persisted


def test_load_filters_null_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"num_ctx": 8192, "log_level": None}), encoding="utf-8")
    store = SettingsStore(path)
    values, error = store.load()
    assert error is None
    assert values == {"num_ctx": 8192}


def test_save_only_persists_known_fields(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"num_ctx": 8192, "some_made_up_field": "nope", "ollama_host": "http://evil"})
    values, _ = store.load()
    assert values == {"num_ctx": 8192}


def test_save_ignores_none_values(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"num_ctx": 8192})
    store.save({"num_ctx": None, "log_level": "warn"})
    values, _ = store.load()
    # num_ctx must survive — a None in a later save() call must not erase it
    assert values == {"num_ctx": 8192, "log_level": "warn"}


def test_persistable_fields_matches_expected_set():
    # Guards against silently adding a field here without also updating
    # api/server.py's SettingsUpdate/_settings_payload (and vice versa) —
    # this list is the single source of truth for what's allowed to touch
    # disk at all.
    assert set(PERSISTABLE_FIELDS) == {
        "primary_model",
        "code_model",
        "max_context_messages",
        "num_ctx",
        "num_predict",
        "request_timeout_seconds",
        "log_level",
        "enable_ocr",
        "enable_image_understanding",
        "enable_translator",
        "enable_code_specialist_auto",
        "enable_reviewer_explicit",
        "stt_language",
        # Settings Pass 2 — pure frontend UI/display preferences.
        "appearance_theme",
        "accent_color",
        "ui_font_size",
        "ui_density",
        "show_message_timestamps",
        "show_typing_animation",
        "copy_before_clear_chat",
        "startup_page",
        "code_font_size",
        "code_line_wrap",
        # Settings Pass 3 — remaining code-display visibility toggles, the
        # experimental Stream-button visibility toggle, and the soft
        # chat-prompt language preference.
        "code_syntax_highlighting",
        "code_show_line_numbers",
        "code_show_language_badge",
        "code_show_copy_button",
        "code_show_collapse_button",
        "code_show_save_button",
        "show_experimental_stream_button",
        "preferred_response_language",
        # Real UI localization pass — application UI language.
        "interface_language",
        # Presence layer (0.2.1, Phase 1) — presence/presence_service.py.
        "enable_presence",
        "allow_proactive_presence_messages",
        "presence_idle_minutes",
        "presence_max_messages_per_hour",
        "presence_quiet_hours_enabled",
        "presence_quiet_hours_start",
        "presence_quiet_hours_end",
        "presence_style",
        "show_presence_card",
        # Presence Behavior Layer (0.2.1, Phase 2).
        "presence_show_welcome_back",
        "presence_show_recent_event",
        "presence_allow_insert_to_chat",
        "presence_min_seconds_between_ui_messages",
    }


def test_save_then_load_roundtrip_ui_preference_fields(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({
        "appearance_theme": "light",
        "accent_color": "forest",
        "ui_font_size": "large",
        "ui_density": "compact",
        "show_message_timestamps": False,
        "show_typing_animation": False,
        "copy_before_clear_chat": True,
        "startup_page": "runtime",
        "code_font_size": "small",
        "code_line_wrap": True,
    })
    values, error = store.load()
    assert error is None
    assert values == {
        "appearance_theme": "light",
        "accent_color": "forest",
        "ui_font_size": "large",
        "ui_density": "compact",
        "show_message_timestamps": False,
        "show_typing_animation": False,
        "copy_before_clear_chat": True,
        "startup_page": "runtime",
        "code_font_size": "small",
        "code_line_wrap": True,
    }


def test_save_then_load_roundtrip_pass3_fields(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({
        "code_syntax_highlighting": False,
        "code_show_line_numbers": False,
        "code_show_language_badge": False,
        "code_show_copy_button": False,
        "code_show_collapse_button": False,
        "code_show_save_button": False,
        "show_experimental_stream_button": False,
        "preferred_response_language": "ru",
    })
    values, error = store.load()
    assert error is None
    assert values == {
        "code_syntax_highlighting": False,
        "code_show_line_numbers": False,
        "code_show_language_badge": False,
        "code_show_copy_button": False,
        "code_show_collapse_button": False,
        "code_show_save_button": False,
        "show_experimental_stream_button": False,
        "preferred_response_language": "ru",
    }


def test_save_then_load_roundtrip_interface_language(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"interface_language": "ru"})
    values, error = store.load()
    assert error is None
    assert values == {"interface_language": "ru"}


def test_saved_file_has_no_bom(tmp_path):
    # save() should never itself introduce a BOM — utf-8-sig is a read-side
    # tolerance, not something we want to start writing.
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save({"num_ctx": 8192})
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
