"""Atomic persistence and migration for user-facing Siena settings."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from config import DEFAULT_MODEL_ROLES

PERSISTABLE_FIELDS = {
    "model_roles",
    "max_context_messages",
    "num_ctx",
    "num_predict",
    "code_num_predict",
    "request_timeout_seconds",
    "code_request_timeout_seconds",
    "stt_language",
    "tts_provider",
    "interface_language",
    "appearance_theme",
    "ui_font_size",
    "ui_density",
    "show_message_timestamps",
    "show_typing_animation",
    "startup_page",
    "log_level",
}


class SettingsStore:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> tuple[dict[str, Any], str | None]:
        if not self._path.exists():
            return {}, None
        try:
            raw = self._path.read_text(encoding="utf-8-sig")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            return {}, str(exc)
        if not isinstance(data, dict):
            return {}, "settings root must be an object"
        return {key: value for key, value in data.items() if key in PERSISTABLE_FIELDS}, None

    def migrate(self) -> dict[str, Any]:
        """Translate legacy model choices and drop obsolete keys after backup."""
        if not self._path.exists():
            return {}
        raw = self._path.read_text(encoding="utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return {}
        cleaned = {key: value for key, value in data.items() if key in PERSISTABLE_FIELDS}
        if not isinstance(cleaned.get("model_roles"), dict):
            roles = dict(DEFAULT_MODEL_ROLES)
            primary = data.get("primary_model")
            coder = data.get("code_model")
            if isinstance(primary, str) and primary.strip():
                roles["chat"] = primary
                roles["memory"] = primary
            if isinstance(coder, str) and coder.strip():
                roles["coder"] = coder
            cleaned["model_roles"] = roles
        else:
            aliases = {"glm-ocr": "glm-ocr:latest", "qwen2.5vl": "qwen2.5vl:latest"}
            cleaned["model_roles"] = {
                role: aliases.get(model, model)
                for role, model in cleaned["model_roles"].items()
            }
        if cleaned != data:
            backup = self._path.with_suffix(".pre-core-cleanup.bak")
            if not backup.exists():
                shutil.copy2(self._path, backup)
            self._write(cleaned)
        return cleaned

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        current, _ = self.load()
        current.update({key: value for key, value in values.items() if key in PERSISTABLE_FIELDS})
        self._write(current)
        return current

    def _write(self, values: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)
