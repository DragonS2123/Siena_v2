"""Faster Qwen3-TTS provider — то же семейство моделей, что и voice/qwen_tts.py
(Qwen3-TTS CustomVoice), но через пакет `faster-qwen3-tts`, который держит
модель в памяти и использует ручной CUDA graph capture для инференса в
реальном времени. Проверено: RTF ~0.4-0.7 после прогрева против ~5-6 у
обычного `qwen-tts` на том же 0.6B чекпоинте (см. scripts/test_faster_qwen3_tts.py).

Тот же контракт: is_available()/synthesize_to_file() (см.
voice/tts.py::SileroTTSProvider), тот же принцип "только рот, не мозг" —
Runtime не решает, ЧТО сказать, только как озвучить уже готовый текст модели.

Почему прямой Python API, а не subprocess/CLI-обёртка вокруг
faster-qwen3-tts.exe: пакет экспортирует чистый, документированный класс
(FasterQwen3TTS.from_pretrained/.generate_custom_voice), и CUDA graph capture
(самая дорогая часть — секунды на "прогрев") происходит один раз внутри
from_pretrained и живёт, пока жив объект модели. Subprocess-обёртка запускала
бы новый процесс на каждый вызов синтеза — то есть заново прогревала бы CUDA
graph на КАЖДОЙ реплике Siena, теряя весь смысл этого provider'а. Прямой API +
ленивая загрузка (одна модель на процесс backend'а, как у Silero/Qwen3-TTS)
даёт прогрев один раз и быстрый инференс на каждый следующий вызов —
подтверждено измерением (первый вызов ~8с, второй — ~1с на той же модели).

model_repo/language/speaker/instruct берутся из активного voice profile
(voice/voice_profiles.py, storage/voice_profiles.json) — так же, как у
voice/qwen_tts.py::Qwen3TTSProvider. instruct — техническая инструкция ТОЛЬКО
для тембра голоса, не personality Siena; в модель уходит только уже готовый
финальный текст ответа + instruct.

Установка (отдельный venv, БЕЗ conda) — см. scripts/test_faster_qwen3_tts.py
и README.md ("Faster Qwen3-TTS"). Пакет: faster-qwen3-tts
(https://github.com/andimarafioti/faster-qwen3-tts), зависит от qwen-tts.

Модель НЕ загружается при импорте и не грузится при старте backend — только
лениво на первый реальный synthesize_to_file()/is_available() (is_available()
тоже не грузит модель — только проверяет, что пакет установлен), симметрично
Qwen3TTSProvider._ensure_model().
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from voice import text_chunking
from voice.qwen_tts import _KNOWN_SPEAKERS
from voice.text_sanitize import sanitize_text_for_tts_detailed
from voice.tts import TTSUnavailableError, _LoggerLike
from voice.voice_profiles import VoiceProfileStore

# Строковые обозначения dtype в конфиге (человекочитаемые) -> реальный
# torch.dtype. from_pretrained() пакета принимает и то, и другое, но делает
# getattr(torch, dtype) для строк — т.е. только точные имена атрибутов torch
# ("bfloat16", не "bf16"), поэтому конфиг использует привычные короткие имена,
# а сюда переводим сами.
_DTYPE_ALIASES = {
    "bf16": "bfloat16",
    "bfloat16": "bfloat16",
    "fp16": "float16",
    "float16": "float16",
    "fp32": "float32",
    "float32": "float32",
}


class FasterQwen3TTSProvider:
    PROVIDER_NAME = "faster_qwen3_tts"

    def __init__(
        self,
        model_repo: str,
        language: str,
        speaker: str,
        instruct: str,
        device: str,
        dtype: str,
        output_dir: Path,
        sample_rate: int,
        use_chunking: bool = False,
        strip_all_numbers: bool = False,
        voice_profile_store: VoiceProfileStore | None = None,
        logger: _LoggerLike | None = None,
    ):
        # Запасные дефолты — используются только если voice_profile_store не
        # передан или активный профиль недоступен/сломан.
        self._default_model_repo = model_repo
        self._default_language = language
        self._default_speaker = speaker
        self._default_instruct = instruct
        self._device = device
        self._dtype_name = dtype
        self._output_dir = output_dir
        self._sample_rate = sample_rate
        self._use_chunking = use_chunking
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
        """Дешёвая проверка: пакет faster-qwen3-tts и torch установлены, И
        CUDA реально доступна. Этот provider держит CUDA graph capture для
        RTF~0.4-0.7 (см. докстринг модуля) — на AMD/без CUDA он структурно не
        может работать, поэтому is_available() честно возвращает False здесь,
        а не притворяется доступным и не падает при первом реальном вызове.
        НЕ грузит модель (см. Qwen3TTSProvider.is_available())."""
        try:
            import torch
            from faster_qwen3_tts import FasterQwen3TTS  # noqa: F401
        except ImportError:
            return False
        try:
            return bool(torch.cuda.is_available())
        except Exception:
            return False

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
                            f"[VOICE][TTS][faster_qwen3_tts] не удалось получить active voice profile, "
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
            from faster_qwen3_tts import FasterQwen3TTS
        except ImportError as exc:
            raise TTSUnavailableError(
                f"faster-qwen3-tts не установлен: {exc}. См. README ('Faster Qwen3-TTS') "
                "или scripts/test_faster_qwen3_tts.py для установки в отдельный venv."
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
                        console_message=f"[VOICE][TTS][faster_qwen3_tts] CUDA недоступна ({exc}) — переключаюсь на cpu",
                        provider=self.PROVIDER_NAME,
                        device=device,
                        error=str(exc),
                    )
                device = "cpu"

        dtype_attr = _DTYPE_ALIASES.get(self._dtype_name.lower(), "bfloat16")
        dtype = getattr(torch, dtype_attr)

        start = time.monotonic()
        try:
            model = FasterQwen3TTS.from_pretrained(model_repo, device=device, dtype=dtype)
        except Exception as exc:
            raise TTSUnavailableError(f"Не удалось загрузить Faster Qwen3-TTS ({model_repo}): {exc}") from exc
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
                dtype=dtype_attr,
                elapsed_sec=round(elapsed_sec, 3),
                console_message=(
                    f"[VOICE][TTS][faster_qwen3_tts] модель загружена за {elapsed_sec:.1f}с "
                    f"(device={device}, dtype={dtype_attr}, model={model_repo})"
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
        # language по-прежнему берутся из активного профиля. Тот же набор
        # пресетных спикеров, что у обычного Qwen3-TTS (voice/qwen_tts.py) —
        # неизвестный override (например, имя Silero-голоса) игнорируется,
        # а не отправляется в движок.
        if voice:
            if voice.strip().lower() in _KNOWN_SPEAKERS:
                speaker = voice
            elif self._logger:
                self._logger.error(
                    "voice_override_ignored",
                    console_message=(
                        f"[VOICE][TTS][faster_qwen3_tts] voice={voice!r} — не Qwen3-TTS speaker, "
                        f"использую speaker активного профиля ({speaker!r})"
                    ),
                    requested_voice=voice,
                    active_speaker=speaker,
                )

        model = self._ensure_model(model_repo)
        speaker_arg = speaker.strip().lower()
        language_arg = language.strip().lower()

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
                console_message=f"[VOICE][TTS][faster_qwen3_tts] profile={profile_id} text={text[:120]!r}",
            )

        start = time.monotonic()
        sample_rate = self._sample_rate
        try:
            parts: list[np.ndarray] = []
            # MVP: по умолчанию НЕ режем текст на чанки — модель сама
            # справляется с просодией лучше, чем механическая нарезка Silero
            # (см. config.FASTER_QWEN_TTS_USE_CHUNKING). Чанкинг остаётся
            # опцией на случай, если на длинных ответах вылезет "пулемёт".
            chunks = text_chunking.split_for_speech(text) if self._use_chunking else [(text, 0.0)]
            for chunk, pause_sec in chunks:
                wavs, sr = model.generate_custom_voice(
                    text=chunk,
                    speaker=speaker_arg,
                    language=language_arg,
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
            raise TTSUnavailableError(f"Ошибка синтеза речи Faster Qwen3-TTS: {exc}") from exc
        elapsed_sec = time.monotonic() - start

        filename = f"{uuid.uuid4()}.wav"
        output_path = self._output_dir / filename
        sf.write(output_path, audio_np, sample_rate)
        duration_sec = len(audio_np) / sample_rate
        rtf = elapsed_sec / duration_sec if duration_sec > 0 else None

        if self._logger:
            self._logger.event(
                "tts_synthesis_result",
                provider=self.PROVIDER_NAME,
                active_profile_id=profile_id,
                output_path=str(output_path),
                duration_sec=round(duration_sec, 3),
                elapsed_sec=round(elapsed_sec, 3),
                rtf=round(rtf, 3) if rtf is not None else None,
                console_message=(
                    f"[VOICE][TTS][faster_qwen3_tts] готово: dur={duration_sec:.2f}с, "
                    f"elapsed={elapsed_sec:.2f}с, RTF={rtf:.2f}" if rtf is not None else
                    f"[VOICE][TTS][faster_qwen3_tts] готово: dur={duration_sec:.2f}с, elapsed={elapsed_sec:.2f}с"
                ),
            )

        return {
            "audio_path": str(output_path),
            "audio_filename": filename,
            "duration_sec": round(duration_sec, 3),
            "voice": speaker,
            "sample_rate": sample_rate,
            "elapsed_sec": round(elapsed_sec, 3),
        }
