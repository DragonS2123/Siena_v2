"""Atomic persistence and migration for user-facing Siena settings."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from config import LLAMA_CPP_MODEL, inference_model_roles

PERSISTABLE_FIELDS = {
    "settings_revision",
    "ollama_host",
    "inference_provider",
    "provider",  # Accepted input alias; runtime persists the canonical field.
    "llama_cpp_url",
    "llama_cpp_model",
    "llama_cpp_profile",
    "llama_cpp_managed",
    "llama_cpp_binary",
    "llama_cpp_model_path",
    "llama_cpp_host",
    "llama_cpp_port",
    "llama_cpp_device",
    "llama_cpp_startup_timeout",
    "llama_cpp_shutdown_timeout",
    "llama_cpp_reasoning_budget_tokens",
    "model_roles",
    "max_context_messages",
    "num_ctx",
    "num_predict",
    "code_num_predict",
    "request_timeout_seconds",
    "code_request_timeout_seconds",
    "auto_continue_on_length",
    "max_auto_continuations",
    "max_total_generation_tokens",
    "continuation_overlap_window_chars",
    "thinking_display",
    "context_size",
    "chat_output_tokens",
    "code_output_tokens",
    "temperature",
    "top_p",
    "top_k",
    "repeat_penalty",
    "seed",
    "auto_continue_enabled",
    "auto_continue_max_rounds",
    "auto_continue_max_total_tokens",
    "auto_continue_timeout_seconds",
    "auto_continue_overlap_window",
    "auto_continue_repair_rounds",
    "auto_resume_interrupted",
    "enable_ocr",
    "enable_image_understanding",
    "enable_translator",
    "enable_code_specialist_auto",
    "enable_reviewer_explicit",
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
        self._lock = threading.RLock()

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
            roles = inference_model_roles(cleaned.get("inference_provider", cleaned.get("provider")), cleaned.get("llama_cpp_model", LLAMA_CPP_MODEL))
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
        with self._lock:
            current, _ = self.load()
            current.update({key: value for key, value in values.items() if key in PERSISTABLE_FIELDS})
            self._write(current)
            return current

    def replace(self, values: dict[str, Any]) -> dict[str, Any]:
        """Atomically replace the persisted snapshot after validation."""
        with self._lock:
            cleaned = {key: value for key, value in values.items() if key in PERSISTABLE_FIELDS}
            self._write(cleaned)
            return cleaned

    def _write(self, values: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)


