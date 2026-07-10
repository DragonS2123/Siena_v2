"""STT (speech-to-text) — faster-whisper. Только техническое превращение
голоса в текст.

Runtime/Voice Layer не решает, что ответить на распознанный текст — он идёт
дальше обычным путём в agent_loop, как если бы пользователь напечатал его сам
(ARCHITECTURE.md, философия проекта не меняется).

Модель НЕ загружается при импорте модуля — только лениво, при первом вызове
transcribe_file()/is_available().
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class STTUnavailableError(Exception):
    pass


class WhisperSTTProvider:
    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        download_root: Path,
        logger: _LoggerLike | None = None,
    ):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self._logger = logger
        self._model = None  # ленивая загрузка — см. докстринг модуля

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        return self._device

    @property
    def compute_type(self) -> str:
        return self._compute_type

    def is_available(self) -> bool:
        """Дешёвая проверка: установлен ли пакет faster-whisper. НЕ грузит модель
        (это заняло бы секунды/гигабайты VRAM только ради проверки статуса) —
        реальная загрузка происходит лениво на первом transcribe_file()."""
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTUnavailableError(
                f"faster-whisper не установлен: {exc}. См. README — установка voice-зависимостей."
            ) from exc

        device, compute_type = self._device, self._compute_type
        self._download_root.mkdir(parents=True, exist_ok=True)

        try:
            self._model = WhisperModel(
                self._model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(self._download_root),
            )
        except Exception as exc:
            if device == "cuda":
                # Технический fallback на CPU — не смысловое решение, а то же
                # самое "используем то, что физически доступно", что и в
                # других местах Runtime (см. DIAGNOSIS_CONTEXT_OVERFLOW.md).
                if self._logger:
                    self._logger.error(
                        "stt_unavailable",
                        console_message=(
                            f"[VOICE][STT] CUDA недоступна ({exc}) — переключаюсь на cpu/int8"
                        ),
                        model=self._model_name,
                        device=device,
                        error=str(exc),
                    )
                device, compute_type = "cpu", "int8"
                try:
                    self._model = WhisperModel(
                        self._model_name,
                        device=device,
                        compute_type=compute_type,
                        download_root=str(self._download_root),
                    )
                except Exception as exc2:
                    raise STTUnavailableError(
                        f"STT-модель {self._model_name} не загрузилась ни на cuda, ни на cpu: {exc2}"
                    ) from exc2
            else:
                raise STTUnavailableError(
                    f"Не удалось загрузить STT-модель {self._model_name} (device={device}): {exc}"
                ) from exc

        self._device = device
        self._compute_type = compute_type
        return self._model

    def _run_transcribe(self, model, path: str, language: str | None) -> dict[str, Any]:
        start = time.monotonic()
        segments_iter, info = model.transcribe(path, language=language)
        segments: list[dict[str, Any]] = []
        texts: list[str] = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
            texts.append(seg.text)
        elapsed_sec = time.monotonic() - start

        return {
            "text": "".join(texts).strip(),
            "language": info.language,
            "duration_sec": round(info.duration, 3),
            "segments": segments,
            "elapsed_sec": round(elapsed_sec, 3),
        }

    def transcribe_file(self, path: str, language: str | None = None) -> dict[str, Any]:
        model = self._ensure_model()

        try:
            return self._run_transcribe(model, path, language)
        except Exception as exc:
            if self._device != "cuda":
                raise STTUnavailableError(f"Ошибка распознавания речи: {exc}") from exc

            # CTranslate2 может успешно СКОНСТРУИРОВАТЬ модель на cuda (device
            # виден через NVML), но упасть только на первом реальном инференсе —
            # например, если в системе нет рантайм-библиотек CUDA Toolkit
            # (cublas64_12.dll и т.п.), которые отдельны от драйвера. Такой сбой
            # не ловится в _ensure_model() при конструировании — тот же
            # технический fallback на CPU нужен и здесь.
            if self._logger:
                self._logger.error(
                    "stt_unavailable",
                    console_message=(
                        f"[VOICE][STT] сбой CUDA при распознавании ({exc}) — переключаюсь на cpu/int8"
                    ),
                    model=self._model_name,
                    device=self._device,
                    error=str(exc),
                )
            self._model = None
            self._device, self._compute_type = "cpu", "int8"
            model = self._ensure_model()
            try:
                return self._run_transcribe(model, path, language)
            except Exception as exc2:
                raise STTUnavailableError(
                    f"Ошибка распознавания речи (после fallback на cpu): {exc2}"
                ) from exc2
