"""Объединяет STT и TTS для api/server.py.

Не принимает смысловых решений — просто маршрутизирует к нужному провайдеру.
STT/TTS не являются tools модели (ARCHITECTURE.md) — это сервисы интерфейса
ввода/вывода, не появляются в registry.schemas().

fallback_tts — инженерная защита, не смысловое решение: если основной
TTS-провайдер (например, экспериментальный Qwen3-TTS) недоступен или падает
при синтезе, используется fallback (обычно Silero) вместо ошибки 503
пользователю. Какой провайдер основной, а какой fallback — решает человек
через config.TTS_PROVIDER, не Runtime."""

from __future__ import annotations

from typing import Any, Protocol

from voice.stt import WhisperSTTProvider
from voice.tts import TTSUnavailableError


class _TTSProviderLike(Protocol):
    PROVIDER_NAME: str

    def is_available(self) -> bool: ...
    def synthesize_to_file(self, text: str, voice: str | None = None) -> dict[str, Any]: ...


class VoiceService:
    def __init__(
        self,
        stt: WhisperSTTProvider,
        tts: _TTSProviderLike,
        fallback_tts: _TTSProviderLike | None = None,
        logger: Any | None = None,
    ):
        self.stt = stt
        self.tts = tts
        self.fallback_tts = fallback_tts
        self._logger = logger

    def transcribe(self, path: str, language: str | None = None) -> dict[str, Any]:
        return self.stt.transcribe_file(path, language=language)

    def synthesize(self, text: str, voice: str | None = None) -> dict[str, Any]:
        try:
            result = self.tts.synthesize_to_file(text, voice=voice)
            result["provider"] = self.tts.PROVIDER_NAME
            return result
        except TTSUnavailableError as exc:
            if self.fallback_tts is None:
                raise
            if self._logger:
                self._logger.error(
                    "tts_fallback",
                    console_message=(
                        f"[VOICE][TTS] {self.tts.PROVIDER_NAME} недоступен ({exc}), "
                        f"fallback на {self.fallback_tts.PROVIDER_NAME}"
                    ),
                    primary_provider=self.tts.PROVIDER_NAME,
                    fallback_provider=self.fallback_tts.PROVIDER_NAME,
                    error=str(exc),
                )
            result = self.fallback_tts.synthesize_to_file(text, voice=voice)
            result["provider"] = self.fallback_tts.PROVIDER_NAME
            return result

    def status(self) -> dict[str, Any]:
        return {
            "stt_available": self.stt.is_available(),
            "stt_model": self.stt.model_name,
            "stt_device": self.stt.device,
            "tts_available": self.tts.is_available() or (self.fallback_tts is not None and self.fallback_tts.is_available()),
            "tts_provider": self.tts.PROVIDER_NAME,
            "tts_fallback_provider": self.fallback_tts.PROVIDER_NAME if self.fallback_tts else None,
            "tts_language": self.tts.language,
            "tts_voice": self.tts.voice,
        }
