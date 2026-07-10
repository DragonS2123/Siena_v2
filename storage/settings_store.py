"""Persistent settings store — survives a backend restart for the small
subset of config.* fields that are actually wired to a live effect via
POST /api/settings (Settings > Model section, see HANDOFF_v2.md). Everything
else in the Settings UI (Appearance, Startup, Tools, Code, Voice, Developer)
is still local-only and never reaches this file.

Not a general key-value store: only PERSISTABLE_FIELDS are ever read or
written, so nothing else (ollama_host, secrets, timeouts not exposed to the
Settings UI) accidentally leaks onto disk. Atomic write via tmp-file +
replace, same pattern as voice/voice_profiles.py.

Runtime doesn't decide what these values should be — a human sets them via
the Settings UI (POST /api/settings, api/server.py); this store only persists
and reloads whatever was already explicitly applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PERSISTABLE_FIELDS = (
    "primary_model",
    "code_model",
    "max_context_messages",
    "num_ctx",
    "num_predict",
    "request_timeout_seconds",
    "log_level",
    # Settings unfreeze pass (HANDOFF_v2.md) — simple boolean feature flags
    # and the STT default language, all of which config.py/api/server.py
    # already read live at call time (same "no restart needed" discipline
    # as log_level above). keep_alive/model-lifecycle fields are
    # deliberately NOT here — out of scope for this pass.
    "enable_ocr",
    "enable_image_understanding",
    "enable_translator",
    "enable_code_specialist_auto",
    "enable_reviewer_explicit",
    "stt_language",
    # Settings Pass 2 — pure frontend UI/display preferences. The backend
    # has no behavioral use for any of these; they're persisted purely so
    # they survive a restart, same discipline as everything else here.
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
    # Settings Pass 3 — remaining code-display visibility toggles + the
    # experimental Stream-button visibility toggle (both pure frontend, no
    # backend behavior) and the one real addition: a soft chat-prompt
    # language preference (see config.py's own comment on this field).
    "code_syntax_highlighting",
    "code_show_line_numbers",
    "code_show_language_badge",
    "code_show_copy_button",
    "code_show_collapse_button",
    "code_show_save_button",
    "show_experimental_stream_button",
    "preferred_response_language",
    # Real UI localization pass — application UI language, separate from
    # stt_language/preferred_response_language above (see config.py).
    "interface_language",
)


class SettingsStore:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> tuple[dict[str, Any], str | None]:
        """Returns (values, error). `values` is always a dict filtered to
        PERSISTABLE_FIELDS — never raises. A missing file is not an error
        (nothing persisted yet); a corrupt/unreadable file returns an empty
        dict plus an error string for the caller to log as
        settings_load_failed, so a broken settings.json can never prevent
        the backend from starting."""
        if not self._path.exists():
            return {}, None
        try:
            # utf-8-sig transparently strips a leading UTF-8 BOM if present
            # (e.g. a human editing settings.json in Notepad, which writes
            # "UTF-8" as UTF-8-with-BOM by default) and behaves identically
            # to plain utf-8 when there's no BOM — json.loads() otherwise
            # rejects a BOM'd file outright (it's not valid JSON syntax),
            # which used to surface as a spurious settings_load_failed.
            raw = self._path.read_text(encoding="utf-8-sig")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            return {}, str(exc)

        if not isinstance(data, dict):
            return {}, "top-level value in settings.json is not a JSON object"

        return {k: v for k, v in data.items() if k in PERSISTABLE_FIELDS and v is not None}, None

    def save(self, values: dict[str, Any]) -> None:
        """Merges `values` into whatever is currently on disk (filtered to
        PERSISTABLE_FIELDS) and writes it back atomically. Raises OSError on
        write failure — the caller (api/server.py) treats persistence as
        best-effort and logs settings_save_failed rather than undoing an
        already-applied runtime change."""
        current, _ = self.load()
        current.update({k: v for k, v in values.items() if k in PERSISTABLE_FIELDS and v is not None})

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)
