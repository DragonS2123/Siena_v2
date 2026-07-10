"""Qwen3-TTS provider — работает рядом с Silero (voice/tts.py), не заменяет
его. Тот же контракт: is_available()/synthesize_to_file() (см.
voice/tts.py::SileroTTSProvider), тот же принцип "только рот, не мозг" —
Runtime не решает, ЧТО сказать, только как озвучить уже готовый текст модели.

model_repo/language/speaker/instruct берутся из активного voice profile
(voice/voice_profiles.py, storage/voice_profiles.json) — сохраняемых
настроек тембра голоса, не из кода. Конструктор всё ещё принимает те же
параметры как ЗАПАСНЫЕ дефолты (config.QWEN_TTS_*) на случай, если
voice_profile_store не передан или профиль недоступен/сломан — тогда
используется дефолт и логируется предупреждение, без падения.

instruct — техническая инструкция ТОЛЬКО для тембра/манеры голоса. Это не
system prompt и не personality Siena: в Qwen3-TTS уходит исключительно уже
готовый финальный текст ответа + instruct голоса, никаких скрытых
memory/system-промптов Siena.

Установка (отдельный venv, БЕЗ conda) и происхождение API — см.
scripts/test_qwen3_tts.py и README.md ("Experimental: Qwen3-TTS"). Пакет:
https://github.com/QwenLM/Qwen3-TTS (PyPI: qwen-tts).

Модель НЕ загружается при импорте и не грузится при старте backend — только
лениво на первый реальный synthesize_to_file()/is_available() (is_available()
тоже не грузит модель — только проверяет, что пакет установлен), симметрично
SileroTTSProvider._ensure_model().
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from voice import text_chunking
from voice.text_sanitize import sanitize_text_for_tts_detailed
from voice.tts import TTSUnavailableError, _LoggerLike
from voice.voice_profiles import VoiceProfileStore

# Пресетные CustomVoice-спикеры Qwen3-TTS (см. README проекта / README
# https://github.com/QwenLM/Qwen3-TTS). Полностью другой набор имён, чем у
# Silero (aidar/baya/kseniya/xenia/eugene/random) — voice= с фронтенда может
# прийти как имя спикера ДРУГОГО провайдера (например, если пользователь
# выбрал голос ещё в Silero-режиме или наоборот). Раньше это отправлялось в
# Qwen3-TTS как есть, движок кидал ValueError, и VoiceService откатывался на
# Silero — то есть Qwen3-TTS фактически никогда не использовался, если во
# фронтенде был выбран Silero-голос. Теперь неизвестный override просто
# игнорируется (используется speaker активного профиля), а не ломает вызов.
_KNOWN_SPEAKERS = {
    "vivian", "serena", "uncle_fu", "dylan", "eric", "ryan", "aiden", "ono_anna", "sohee",
}


class Qwen3TTSProvider:
    PROVIDER_NAME = "qwen3_tts"

    def __init__(
        self,
        model_repo: str,
        language: str,
        speaker: str,
        instruct: str,
        device: str,
        output_dir: Path,
        sample_rate: int,
        strip_all_numbers: bool = False,
        voice_profile_store: VoiceProfileStore | None = None,
        logger: _LoggerLike | None = None,
    ):
        # Запасные дефолты — используются только если voice_profile_store
        # не передан или активный профиль недоступен/сломан.
        self._default_model_repo = model_repo
        self._default_language = language
        self._default_speaker = speaker
        self._default_instruct = instruct
        self._device = device
        self._output_dir = output_dir
        self._sample_rate = sample_rate
        self._strip_all_numbers = strip_all_numbers
        self._voice_profile_store = voice_profile_store
        self._logger = logger
        self._model = None  # ленивая загрузка — см. докстринг модуля
        self._loaded_model_repo: str | None = None

    @property
    def voice(self) -> str:
        if self._voice_profile_store is not None:
            try:
                return self._voice_profile_store.get_active_profile().speaker
            except Exception:
                pass
        return self._default_speaker

    @property
    def language(self) -> str:
        if self._voice_profile_store is not None:
            try:
                return self._voice_profile_store.get_active_profile().language
            except Exception:
                pass
        return self._default_language

    @property
    def device(self) -> str:
        return self._device

    def is_available(self) -> bool:
        """Дешёвая проверка: пакет qwen-tts и torch установлены. НЕ грузит
        модель (первая реальная загрузка идёт по сети с HuggingFace и может
        занять минуты) — см. SileroTTSProvider.is_available() за тем же
        рассуждением."""
        try:
            import torch  # noqa: F401
            from qwen_tts import Qwen3TTSModel  # noqa: F401
        except ImportError:
            return False
        return True

    def _resolve_profile(self) -> tuple[str, str, str, str, str]:
        """Возвращает (profile_id, model_repo, language, speaker, instruct) из
        активного voice profile. Runtime не решает, как должен звучать голос —
        это уже сохранённое решение человека (voice profile) либо, если оно
        недоступно, технический дефолт из config.py."""
        if self._voice_profile_store is not None:
            try:
                profile = self._voice_profile_store.get_active_profile()
                return profile.id, profile.model_repo, profile.language, profile.speaker, profile.instruct
            except Exception as exc:
                if self._logger:
                    self._logger.error(
                        "voice_profile_error",
                        console_message=(
                            f"[VOICE][TTS][qwen3_tts] не удалось получить active voice profile, "
                            f"использую дефолт из config.py: {exc}"
                        ),
                        error=str(exc),
                    )
        return (
            "config_default",
            self._default_model_repo,
            self._default_language,
            self._default_speaker,
            self._default_instruct,
        )

    def _ensure_model(self, model_repo: str):
        if self._model is not None and self._loaded_model_repo == model_repo:
            return self._model

        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise TTSUnavailableError(
                f"qwen-tts не установлен: {exc}. См. README ('Experimental: Qwen3-TTS') "
                "или scripts/test_qwen3_tts.py для установки в отдельный venv."
            ) from exc

        device = self._device
        if device == "cuda":
            try:
                if not torch.cuda.is_available():
                    raise RuntimeError("torch.cuda.is_available() == False")
            except Exception as exc:
                if self._logger:
                    self._logger.error(
                        "tts_unavailable",
                        console_message=f"[VOICE][TTS][qwen3_tts] CUDA недоступна ({exc}) — переключаюсь на cpu",
                        provider=self.PROVIDER_NAME,
                        device=device,
                        error=str(exc),
                    )
                device = "cpu"

        load_kwargs: dict[str, Any] = {"dtype": torch.bfloat16 if device == "cuda" else torch.float32}
        if device == "cuda":
            load_kwargs["device_map"] = "cuda:0"
        # attn_implementation="flash_attention_2" сознательно не указываем —
        # flash-attn опционален и не ставится на baseline-этапе (см. README).

        start = time.monotonic()
        try:
            model = Qwen3TTSModel.from_pretrained(model_repo, **load_kwargs)
        except Exception as exc:
            raise TTSUnavailableError(f"Не удалось загрузить Qwen3-TTS ({model_repo}): {exc}") from exc
        elapsed_sec = time.monotonic() - start

        self._model = model
        self._loaded_model_repo = model_repo
        self._device = device
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if self._logger:
            self._logger.event(
                "tts_model_loaded",
                provider=self.PROVIDER_NAME,
                model_id=model_repo,
                device=device,
                elapsed_sec=round(elapsed_sec, 3),
                console_message=(
                    f"[VOICE][TTS][qwen3_tts] модель загружена за {elapsed_sec:.1f}с (device={device}, model={model_repo})"
                ),
            )
        return model

    def synthesize_to_file(self, text: str, voice: str | None = None) -> dict[str, Any]:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise TTSUnavailableError(f"Не установлен soundfile: {exc}") from exc

        profile_id, model_repo, language, speaker, instruct = self._resolve_profile()
        # Явный voice override меняет только speaker — instruct/model_repo/
        # language по-прежнему берутся из активного профиля. Если override —
        # не спикер Qwen3-TTS (например, имя Silero-голоса с фронтенда),
        # игнорируем его вместо того, чтобы отправлять в движок и падать.
        if voice:
            if voice.strip().lower() in _KNOWN_SPEAKERS:
                speaker = voice
            elif self._logger:
                self._logger.error(
                    "voice_override_ignored",
                    console_message=(
                        f"[VOICE][TTS][qwen3_tts] voice={voice!r} — не Qwen3-TTS speaker, "
                        f"использую speaker активного профиля ({speaker!r})"
                    ),
                    requested_voice=voice,
                    active_speaker=speaker,
                )

        model = self._ensure_model(model_repo)

        sanitized = sanitize_text_for_tts_detailed(text, strip_all_numbers=self._strip_all_numbers)
        text = sanitized.text

        if self._logger:
            self._logger.event(
                "tts_text_received",
                provider=self.PROVIDER_NAME,
                active_profile_id=profile_id,
                speaker=speaker,
                model_id=model_repo,
                language=language,
                text_preview=repr(text[:300]),
                original_text_len=sanitized.original_len,
                sanitized_text_len=sanitized.sanitized_len,
                removed_stage_directions=sanitized.removed_stage_directions,
                removed_list_numbers=sanitized.removed_list_numbers,
                has_punctuation=any(ch in text for ch in ".!?…,:;"),
                console_message=f"[VOICE][TTS][qwen3_tts] profile={profile_id} text={text[:120]!r}",
            )

        start = time.monotonic()
        sample_rate = self._sample_rate
        try:
            parts: list[np.ndarray] = []
            for chunk, pause_sec in text_chunking.split_for_speech(text):
                wavs, sr = model.generate_custom_voice(
                    text=chunk,
                    speaker=speaker,
                    language=language,
                    instruct=instruct or None,
                )
                sample_rate = sr
                audio_np = text_chunking.to_numpy_audio(wavs[0] if isinstance(wavs, (list, tuple)) else wavs)
                parts.append(audio_np)
                if pause_sec > 0:
                    silence = np.zeros(int(sample_rate * pause_sec), dtype=audio_np.dtype)
                    parts.append(silence)

            if not parts:
                raise RuntimeError("empty text after speech preprocessing")

            audio_np = np.concatenate(parts)
        except Exception as exc:
            raise TTSUnavailableError(f"Ошибка синтеза речи Qwen3-TTS: {exc}") from exc
        elapsed_sec = time.monotonic() - start

        filename = f"{uuid.uuid4()}.wav"
        output_path = self._output_dir / filename
        sf.write(output_path, audio_np, sample_rate)
        duration_sec = len(audio_np) / sample_rate

        return {
            "audio_path": str(output_path),
            "audio_filename": filename,
            "duration_sec": round(duration_sec, 3),
            "voice": speaker,
            "sample_rate": sample_rate,
            "elapsed_sec": round(elapsed_sec, 3),
        }
