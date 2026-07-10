"""Persistent voice profiles для Qwen3-TTS-семейства провайдеров (qwen3_tts,
faster_qwen3_tts) — сохраняемые speaker/language/model_repo/instruct настройки
голоса Siena, вынесенные из кода.

Это НЕ personality prompt Siena. Voice profile описывает только то, КАК
звучит голос (тембр, теплота, "взрослость") — техническая инструкция для
TTS-движка. Характер/поведение Siena по-прежнему живёт только в
config.SYSTEM_PROMPT; instruct отсюда в system prompt не подмешивается, а
system prompt в TTS не уходит (см. voice/qwen_tts.py, voice/faster_qwen_tts.py —
в модель идёт только уже готовый финальный текст ответа + instruct голоса).

Runtime не решает, какой профиль "лучше" — выбор профиля/редактирование
instruct это явное действие человека (API/UI). Store здесь только читает и
пишет JSON, атомарно (tmp-файл + replace, как в memory/short_memory_store.py).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.errors import SienaInfraError

DEFAULT_ACTIVE_PROFILE_ID = "siena_default_adult"

_DEFAULT_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_DEFAULT_LANGUAGE = "russian"
_DEFAULT_SPEAKER = "serena"  # "vivian" звучал слишком по-детски/аниме — serena взрослее и естественнее

_DEFAULT_PROFILES_DATA: list[dict[str, str]] = [
    {
        "id": "siena_default_adult",
        "name": "Siena — mature warm female",
        "provider": "faster_qwen3_tts",
        "model_repo": _DEFAULT_MODEL_REPO,
        "language": _DEFAULT_LANGUAGE,
        "speaker": _DEFAULT_SPEAKER,
        "instruct": (
            "Consistent mature adult female Russian voice. Keep the same "
            "speaker identity, pitch, timbre, volume, and emotional tone "
            "throughout the entire utterance. Do not change voice between "
            "sentences. Calm, warm, soft, emotionally grounded. Lower pitch, "
            "less cute, less anime, less childish. Natural close conversation. "
            "Not theatrical, not announcer-like, not cartoon-like. No "
            "exaggerated acting, no character switching."
        ),
    },
    {
        "id": "siena_soft_companion",
        "name": "Siena — soft companion",
        "provider": "faster_qwen3_tts",
        "model_repo": _DEFAULT_MODEL_REPO,
        "language": _DEFAULT_LANGUAGE,
        "speaker": _DEFAULT_SPEAKER,
        "instruct": (
            "Warm adult female Russian voice. Gentle, intimate, calm, "
            "emotionally present. Speak like a close AI companion. Natural "
            "pauses, soft tone, no anime style, no childish cuteness, no "
            "exaggerated acting."
        ),
    },
    {
        "id": "siena_clear_technical",
        "name": "Siena — clear technical",
        "provider": "faster_qwen3_tts",
        "model_repo": _DEFAULT_MODEL_REPO,
        "language": _DEFAULT_LANGUAGE,
        "speaker": _DEFAULT_SPEAKER,
        "instruct": (
            "Adult female Russian voice. Clear, calm, focused, natural. "
            "Slight warmth, but not playful. Suitable for technical "
            "explanations. No announcer style, no cartoon style, no "
            "exaggerated emotion."
        ),
    },
]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class VoiceProfile:
    id: str
    name: str
    provider: str
    model_repo: str
    language: str
    speaker: str
    instruct: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "VoiceProfile":
        now = _now_iso()
        return VoiceProfile(
            id=data["id"],
            name=data.get("name") or data["id"],
            provider=data.get("provider") or "qwen3_tts",
            model_repo=data.get("model_repo") or _DEFAULT_MODEL_REPO,
            language=data.get("language") or _DEFAULT_LANGUAGE,
            speaker=data.get("speaker") or _DEFAULT_SPEAKER,
            instruct=data.get("instruct") or "",
            created_at=data.get("created_at") or now,
            updated_at=data.get("updated_at") or now,
        )


def _build_default_profiles() -> list[VoiceProfile]:
    now = _now_iso()
    return [
        VoiceProfile(
            id=p["id"],
            name=p["name"],
            provider=p["provider"],
            model_repo=p["model_repo"],
            language=p["language"],
            speaker=p["speaker"],
            instruct=p["instruct"],
            created_at=now,
            updated_at=now,
        )
        for p in _DEFAULT_PROFILES_DATA
    ]


class VoiceProfileStore:
    def __init__(self, path: Path, logger: Any | None = None):
        self._path = path
        self._logger = logger
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(DEFAULT_ACTIVE_PROFILE_ID, _build_default_profiles())

    def _write(self, active_profile_id: str, profiles: list[VoiceProfile]) -> None:
        payload = {
            "active_profile_id": active_profile_id,
            "profiles": [p.to_dict() for p in profiles],
        }
        tmp_path = self._path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError as exc:
            raise SienaInfraError(f"Не удалось записать voice_profiles.json: {exc}") from exc

    def _reset_to_defaults(self, reason: str) -> tuple[str, list[VoiceProfile]]:
        if self._logger:
            self._logger.error(
                "voice_profiles_error",
                console_message=f"[VOICE][PROFILES] {reason} — восстанавливаю дефолтные профили",
                error=reason,
            )
        defaults = _build_default_profiles()
        self._write(DEFAULT_ACTIVE_PROFILE_ID, defaults)
        return DEFAULT_ACTIVE_PROFILE_ID, defaults

    def _read(self) -> tuple[str, list[VoiceProfile]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            return self._reset_to_defaults(f"файл voice_profiles.json повреждён/недоступен: {exc}")

        profiles_data = data.get("profiles") or []
        try:
            profiles = [VoiceProfile.from_dict(p) for p in profiles_data]
        except (KeyError, TypeError) as exc:
            return self._reset_to_defaults(f"некорректный формат voice_profiles.json: {exc}")

        if not profiles:
            return self._reset_to_defaults("voice_profiles.json без единого профиля")

        active_id = data.get("active_profile_id") or DEFAULT_ACTIVE_PROFILE_ID
        return active_id, profiles

    def list_profiles(self) -> list[VoiceProfile]:
        _, profiles = self._read()
        return profiles

    def get_profile(self, profile_id: str) -> VoiceProfile | None:
        _, profiles = self._read()
        return next((p for p in profiles if p.id == profile_id), None)

    def get_active_profile(self) -> VoiceProfile:
        active_id, profiles = self._read()
        profile = next((p for p in profiles if p.id == active_id), None)
        if profile is not None:
            return profile

        # active_profile_id указывает на несуществующий профиль (например, был
        # удалён вручную из файла) — не падаем, используем дефолтный и логируем.
        if self._logger:
            self._logger.error(
                "voice_profiles_error",
                console_message=f"[VOICE][PROFILES] active_profile_id={active_id!r} не найден, использую дефолт",
                active_profile_id=active_id,
            )
        fallback = next((p for p in profiles if p.id == DEFAULT_ACTIVE_PROFILE_ID), None)
        return fallback or profiles[0]

    def save_profile(self, profile: VoiceProfile) -> VoiceProfile:
        active_id, profiles = self._read()
        now = _now_iso()
        existing_index = next((i for i, p in enumerate(profiles) if p.id == profile.id), None)
        if existing_index is not None:
            profile.created_at = profiles[existing_index].created_at
            profile.updated_at = now
            profiles[existing_index] = profile
        else:
            profile.created_at = profile.created_at or now
            profile.updated_at = now
            profiles.append(profile)
        self._write(active_id, profiles)
        return profile

    def update_profile(self, profile_id: str, **fields: Any) -> VoiceProfile:
        active_id, profiles = self._read()
        index = next((i for i, p in enumerate(profiles) if p.id == profile_id), None)
        if index is None:
            raise ValueError(f"Unknown voice profile id: {profile_id!r}")

        profile = profiles[index]
        updatable = {"name", "provider", "model_repo", "language", "speaker", "instruct"}
        for key, value in fields.items():
            if key in updatable and value is not None:
                setattr(profile, key, value)
        profile.updated_at = _now_iso()
        profiles[index] = profile
        self._write(active_id, profiles)
        return profile

    def set_active_profile(self, profile_id: str) -> VoiceProfile:
        active_id, profiles = self._read()
        profile = next((p for p in profiles if p.id == profile_id), None)
        if profile is None:
            raise ValueError(f"Unknown voice profile id: {profile_id!r}")
        self._write(profile_id, profiles)
        return profile
