"""Local Qwen3-TTS provider backed by qwentts.cpp (GGML/Vulkan)."""

from __future__ import annotations

import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from voice.text_sanitize import sanitize_text_for_tts_detailed
from voice.errors import LoggerLike, TTSUnavailableError

_READY_POLL_INTERVAL_SEC = 0.5


class QwenTTSGgmlVulkanProvider:
    PROVIDER_NAME = "qwen3_tts_ggml_vulkan"

    def __init__(
        self,
        server_url: str,
        exe_path: Path,
        model_path: Path,
        codec_path: Path,
        default_language: str,
        default_speaker: str,
        timeout: int,
        output_dir: Path,
        auto_start: bool = True,
        startup_timeout_sec: int = 30,
        logger: LoggerLike | None = None,
    ):
        self._server_url = server_url.rstrip("/")
        parsed = urlparse(self._server_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 8080
        self._exe_path = exe_path
        self._model_path = model_path
        self._codec_path = codec_path
        self._default_language = default_language
        self._default_speaker = default_speaker
        self._timeout = timeout
        self._output_dir = output_dir
        self._auto_start = auto_start
        self._startup_timeout_sec = startup_timeout_sec
        self._logger = logger
        self._process: subprocess.Popen | None = None  # only set if WE started it

    @property
    def voice(self) -> str:
        return self._default_speaker

    @property
    def language(self) -> str:
        return self._default_language

    @property
    def device(self) -> str:
        return "vulkan"

    def is_available(self) -> bool:
        """Дешёвая проверка: сервер уже отвечает, ИЛИ у нас есть всё, чтобы
        его запустить (exe + обе GGUF-модели существуют на диске). Не
        запускает сервер и не грузит модель — см. докстрины остальных
        TTS-провайдеров за тем же принципом."""
        if self._is_server_reachable():
            return True
        return self._exe_path.exists() and self._model_path.exists() and self._codec_path.exists()

    def _is_server_reachable(self, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=timeout):
                return True
        except OSError:
            return False

    def is_server_managed_by_us(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def ensure_server_running(self) -> None:
        """Запускает tts-server.exe, если он ещё не отвечает. Если сервер уже
        поднят (кем угодно — нами раньше, или вручную человеком) — просто
        использует его, ничего не перезапускает."""
        if self._is_server_reachable():
            return

        if not self._auto_start:
            raise TTSUnavailableError(
                f"qwentts.cpp сервер не отвечает на {self._server_url}, "
                "а автозапуск отключён (QWEN_TTS_KEEP_SERVER_WARM/auto_start=false)."
            )

        if not self._exe_path.exists():
            raise TTSUnavailableError(f"tts-server.exe не найден: {self._exe_path}")
        if not self._model_path.exists():
            raise TTSUnavailableError(f"talker GGUF не найден: {self._model_path}")
        if not self._codec_path.exists():
            raise TTSUnavailableError(f"codec GGUF не найден: {self._codec_path}")

        if self._logger:
            self._logger.event(
                "tts_server_starting",
                provider=self.PROVIDER_NAME,
                host=self._host,
                port=self._port,
                console_message=f"[VOICE][TTS][qwen_ggml_vulkan] запускаю tts-server.exe ({self._host}:{self._port})",
            )

        try:
            self._process = subprocess.Popen(
                [
                    str(self._exe_path),
                    "--model", str(self._model_path),
                    "--codec", str(self._codec_path),
                    "--host", self._host,
                    "--port", str(self._port),
                    "--lang", self._default_language,
                ],
                cwd=str(self._exe_path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise TTSUnavailableError(f"Не удалось запустить tts-server.exe: {exc}") from exc

        start = time.monotonic()
        while time.monotonic() - start < self._startup_timeout_sec:
            if self._is_server_reachable():
                if self._logger:
                    elapsed = time.monotonic() - start
                    self._logger.event(
                        "tts_server_ready",
                        provider=self.PROVIDER_NAME,
                        elapsed_sec=round(elapsed, 3),
                        console_message=f"[VOICE][TTS][qwen_ggml_vulkan] сервер готов за {elapsed:.1f}с",
                    )
                return
            if self._process.poll() is not None:
                raise TTSUnavailableError(
                    f"tts-server.exe завершился раньше времени (код {self._process.returncode})"
                )
            time.sleep(_READY_POLL_INTERVAL_SEC)

        self.stop_server()
        raise TTSUnavailableError(f"tts-server.exe не ответил за {self._startup_timeout_sec}с")

    def stop_server(self) -> None:
        """Останавливает сервер, только если ЭТОТ провайдер его запускал —
        не трогает сервер, поднятый человеком вручную снаружи."""
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._logger:
            self._logger.event(
                "tts_server_stopped",
                provider=self.PROVIDER_NAME,
                console_message="[VOICE][TTS][qwen_ggml_vulkan] сервер остановлен",
            )
        self._process = None

    def synthesize_to_file(self, text: str, voice: str | None = None) -> dict[str, Any]:
        self.ensure_server_running()

        speaker = voice or self._default_speaker
        sanitized = sanitize_text_for_tts_detailed(text, strip_all_numbers=False)
        text = sanitized.text

        if self._logger:
            self._logger.event(
                "tts_text_received",
                provider=self.PROVIDER_NAME,
                speaker=speaker,
                text_preview=repr(text[:300]),
                original_text_len=sanitized.original_len,
                sanitized_text_len=sanitized.sanitized_len,
                console_message=f"[VOICE][TTS][qwen_ggml_vulkan] text={text[:120]!r}",
            )
            self._logger.event(
                "tts_request_started",
                provider=self.PROVIDER_NAME,
                speaker=speaker,
                console_message=f"[VOICE][TTS][qwen_ggml_vulkan] запрос синтеза (voice={speaker})",
            )

        start = time.monotonic()
        try:
            response = requests.post(
                f"{self._server_url}/v1/audio/speech",
                json={"input": text, "voice": speaker, "response_format": "wav"},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if self._logger:
                self._logger.error(
                    "tts_request_failed",
                    console_message=f"[VOICE][TTS][qwen_ggml_vulkan] ошибка запроса: {exc}",
                    provider=self.PROVIDER_NAME,
                    error=str(exc),
                )
            raise TTSUnavailableError(f"qwentts.cpp запрос синтеза не удался: {exc}") from exc
        elapsed_sec = time.monotonic() - start

        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4()}.wav"
        output_path = self._output_dir / filename
        output_path.write_bytes(response.content)

        duration_sec, sample_rate = self._wav_duration_and_rate(output_path)

        if self._logger:
            self._logger.event(
                "tts_request_completed",
                provider=self.PROVIDER_NAME,
                speaker=speaker,
                output_path=str(output_path),
                duration_sec=duration_sec,
                elapsed_sec=round(elapsed_sec, 3),
                console_message=(
                    f"[VOICE][TTS][qwen_ggml_vulkan] готово: dur={duration_sec:.2f}с, elapsed={elapsed_sec:.2f}с"
                ),
            )

        return {
            "audio_path": str(output_path),
            "audio_filename": filename,
            "duration_sec": duration_sec,
            "voice": speaker,
            "sample_rate": sample_rate,
            "elapsed_sec": round(elapsed_sec, 3),
        }

    @staticmethod
    def _wav_duration_and_rate(path: Path) -> tuple[float, int]:
        import wave

        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return (round(frames / rate, 3) if rate else 0.0), rate
