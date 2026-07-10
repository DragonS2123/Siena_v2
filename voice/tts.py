"""TTS (text-to-speech) — Silero TTS Russian. Только техническое превращение
текста в звук.

Runtime/Voice Layer не решает, ЧТО говорить — только как озвучить текст,
который уже сформировала модель (или прислал пользователь напрямую через
/api/voice/synthesize). Симметрично voice/stt.py.

Заменяет Kokoro: та не поддерживала русский язык вообще и на русском тексте
выдавала некорректную псевдоречь (кириллица читалась через английскую
фонетику) — см. DONEARCHITECTURE.md. Silero TTS Russian — нативная модель.

Модель НЕ загружается при импорте модуля и не грузится при старте backend —
только лениво, на первый реальный synthesize_to_file()/is_available()
(is_available() тоже не грузит модель — см. докстринг метода). Первая
генерация может быть медленной (torch.hub качает репозиторий + веса).

Provider-слой намеренно общий (TTSUnavailableError, тот же контракт
is_available()/synthesize_to_file(), что был у Kokoro) — чтобы позже можно
было добавить Piper/eSpeak как аварийный fallback-голос (не основной), не
переделывая интерфейс. Такой fallback пока не реализован.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from voice import text_chunking
from voice.text_sanitize import sanitize_text_for_tts_detailed


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class TTSUnavailableError(Exception):
    pass


# Ре-экспорт для обратной совместимости — раньше жил здесь, теперь общий для
# всех TTS-провайдеров в voice/text_chunking.py (см. voice/qwen_tts.py).
_LETTER_RE = text_chunking.LETTER_RE


class SileroTTSProvider:
    PROVIDER_NAME = "silero"

    def __init__(
        self,
        language: str,
        model_id: str,
        speaker: str,
        device: str,
        output_dir: Path,
        sample_rate: int,
        models_dir: Path,
        strip_all_numbers: bool = False,
        logger: _LoggerLike | None = None,
    ):
        self._language = language
        self._model_id = model_id
        self._speaker = speaker
        self._device = device
        self._output_dir = output_dir
        self._sample_rate = sample_rate
        self._models_dir = models_dir
        self._strip_all_numbers = strip_all_numbers
        self._logger = logger
        self._model = None  # ленивая загрузка — см. докстринг модуля

    @property
    def voice(self) -> str:
        return self._speaker

    @property
    def language(self) -> str:
        return self._language

    @property
    def device(self) -> str:
        return self._device

    def is_available(self) -> bool:
        """Дешёвая проверка: torch/omegaconf установлены. НЕ грузит модель
        (первая реальная загрузка идёт по сети через torch.hub и может занять
        десятки секунд) — см. WhisperSTTProvider.is_available() за тем же
        рассуждением."""
        try:
            import omegaconf  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        try:
            import torch
        except ImportError as exc:
            raise TTSUnavailableError(
                f"torch не установлен: {exc}. См. README — установка voice-зависимостей."
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
                        console_message=f"[VOICE][TTS] CUDA недоступна ({exc}) — переключаюсь на cpu",
                        device=device,
                        error=str(exc),
                    )
                device = "cpu"

        self._models_dir.mkdir(parents=True, exist_ok=True)
        torch.hub.set_dir(str(self._models_dir))

        start = time.monotonic()
        try:
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language=self._language,
                speaker=self._model_id,
                trust_repo=True,
            )
            model.to(device)
        except Exception as exc:
            raise TTSUnavailableError(
                f"Не удалось загрузить Silero TTS ({self._model_id}, language={self._language}): {exc}"
            ) from exc
        elapsed_sec = time.monotonic() - start

        self._model = model
        self._device = device
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if self._logger:
            self._logger.event(
                "tts_model_loaded",
                provider=self.PROVIDER_NAME,
                language=self._language,
                model_id=self._model_id,
                device=device,
                elapsed_sec=round(elapsed_sec, 3),
                console_message=(
                    f"[VOICE][TTS] Silero загружена за {elapsed_sec:.1f}с (device={device})"
                ),
            )
        return model

    # Тонкие делегаты к voice/text_chunking.py — оставлены как методы для
    # обратной совместимости (существующие тесты вызывают их через экземпляр
    # SileroTTSProvider); реальная логика теперь общая для всех провайдеров.
    def _normalize_for_speech(self, text: str) -> str:
        return text_chunking.normalize_for_speech(text)

    def _split_for_speech(self, text: str) -> list[tuple[str, float]]:
        return text_chunking.split_for_speech(text)

    def _to_numpy_audio(self, audio: Any) -> np.ndarray:
        return text_chunking.to_numpy_audio(audio)

    def synthesize_to_file(self, text: str, voice: str | None = None) -> dict[str, Any]:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise TTSUnavailableError(f"Не установлен soundfile: {exc}") from exc

        model = self._ensure_model()
        speaker = voice or self._speaker

        sanitized = sanitize_text_for_tts_detailed(text, strip_all_numbers=self._strip_all_numbers)
        text = sanitized.text

        if self._logger:
            self._logger.event(
                "tts_text_received",
                provider=self.PROVIDER_NAME,
                text_preview=repr(text[:300]),
                original_text_len=sanitized.original_len,
                sanitized_text_len=sanitized.sanitized_len,
                removed_stage_directions=sanitized.removed_stage_directions,
                removed_list_numbers=sanitized.removed_list_numbers,
                has_punctuation=any(ch in text for ch in ".!?…,:;"),
                console_message=f"[VOICE][TTS] text={text[:120]!r}",
            )

        start = time.monotonic()
        try:
            parts: list[np.ndarray] = []
            for chunk, pause_sec in self._split_for_speech(text):
                audio = model.apply_tts(text=chunk, speaker=speaker, sample_rate=self._sample_rate)
                audio_np = self._to_numpy_audio(audio)
                parts.append(audio_np)
                if pause_sec > 0:
                    silence = np.zeros(int(self._sample_rate * pause_sec), dtype=audio_np.dtype)
                    parts.append(silence)

            if not parts:
                raise RuntimeError("empty text after speech preprocessing")

            audio_np = np.concatenate(parts)
        except Exception as exc:
            raise TTSUnavailableError(f"Ошибка синтеза речи Silero: {exc}") from exc
        elapsed_sec = time.monotonic() - start

        filename = f"{uuid.uuid4()}.wav"
        output_path = self._output_dir / filename
        sf.write(output_path, audio_np, self._sample_rate)
        duration_sec = len(audio_np) / self._sample_rate

        return {
            "audio_path": str(output_path),
            "audio_filename": filename,
            "duration_sec": round(duration_sec, 3),
            "voice": speaker,
            "sample_rate": self._sample_rate,
            "elapsed_sec": round(elapsed_sec, 3),
        }
