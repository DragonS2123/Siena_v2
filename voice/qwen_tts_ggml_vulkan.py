"""Qwen3-TTS provider backed by qwentts.cpp (GGML/Vulkan) — confirmed working
on AMD RX 7900 XTX. No torch, no CUDA: this talks to `tts-server.exe`
(external/qwentts.cpp/build/Release) over its OpenAI-compatible HTTP API
(POST /v1/audio/speech), the same "only the mouth, not the brain" contract as
every other TTS provider in this package (see voice/tts.py::SileroTTSProvider)
— Runtime doesn't decide WHAT to say, only how to voice text the model (or a
direct /api/voice/synthesize caller) already produced.

Server lifecycle: Runtime doesn't decide whether the user WANTS the server
running — config.QWEN_TTS_KEEP_SERVER_WARM (human's explicit choice) controls
whether it's started eagerly at backend boot; otherwise it's started lazily
on the first real synthesize_to_file() call, mirroring the lazy-load pattern
every other provider here already uses. POST /api/voice/tts/start / /stop
(api/server.py) are the explicit manual actions a human can take regardless
of that setting.
"""

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
from voice.tts import TTSUnavailableError, _LoggerLike

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
        logger: _LoggerLike | None = None,
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

    def stream_pcm(self, text: str, voice: str | None = None, language: str | None = None):
        """Experimental (Phase 2/3, HANDOFF_v2.md) — proxies raw PCM chunks
        from qwentts.cpp's tts-server.exe (response_format="pcm") as they
        arrive. A plain generator; completely separate from
        synthesize_to_file()/the stable WAV-per-request path above, which
        this method never calls or affects.

        `language` is accepted for API-shape completeness (the endpoint's
        request contract includes it) but is NOT forwarded anywhere: the
        raw tts-server.exe HTTP API (external/qwentts.cpp/src/tts-server.h)
        only accepts input/voice/response_format per request — the spoken
        language is fixed for the whole server process by the --lang flag
        given at startup (see ensure_server_running() above), not
        per-request. This method deliberately does NOT restart the server
        to honor a different `language` here — doing so would silently
        kill/replace a server other callers (or a human) may depend on
        being warm, which is out of scope for an experimental streaming
        probe.

        Note on Stop (Phase 3, HANDOFF_v2.md): a client aborting its fetch
        mid-stream cannot interrupt a blocking `response.iter_content()`
        read here from the outside — Starlette runs the generator built on
        top of this in a worker thread with no cancellation hook into a
        blocking call already in progress. An ASGI-level disconnect watcher
        was tried and confirmed (via live testing, not just in theory) to
        never detect the disconnect while this call is blocked. So Stop
        works by having the frontend immediately abort its own fetch/audio
        pipeline and report the disconnect itself — this generator (and the
        upstream tts-server request it's blocked on) simply keeps running
        until tts-server finishes that utterance on its own. See
        api/server.py::_stream_pcm_body for the full explanation.

        scripts/probe_qwen_tts_streaming.py showed response_format=pcm with
        stream=True surviving 36 real requests (including repeats of the
        exact short Russian phrase that crashed a prior manual probe) — but
        this is still qwen-only with NO Silero fallback by design: a
        fallback would silently swap to a non-streaming provider mid-request,
        which makes no sense for a streaming contract. Callers must treat
        any failure here as an honest error, never paper over it.

        Yields raw PCM bytes (s16le, 24 kHz, mono per tts-server.h). Raises
        TTSUnavailableError if the connection/initial response fails, or if
        the connection breaks partway through (after some bytes may already
        have been yielded — same "proxy, don't buffer" tradeoff as any other
        streamed passthrough).
        """
        self.ensure_server_running()

        speaker = voice or self._default_speaker
        sanitized = sanitize_text_for_tts_detailed(text, strip_all_numbers=False)
        text = sanitized.text

        try:
            response = requests.post(
                f"{self._server_url}/v1/audio/speech",
                json={"input": text, "voice": speaker, "response_format": "pcm"},
                timeout=self._timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise TTSUnavailableError(f"qwentts.cpp stream request failed: {exc}") from exc

        if response.status_code != 200:
            try:
                body_preview = response.text[:300]
            except Exception:
                body_preview = "<unreadable body>"
            response.close()
            raise TTSUnavailableError(
                f"qwentts.cpp stream request returned HTTP {response.status_code}: {body_preview}"
            )

        try:
            for chunk in response.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        except requests.RequestException as exc:
            raise TTSUnavailableError(f"qwentts.cpp stream broke mid-response: {exc}") from exc
        finally:
            response.close()

    @staticmethod
    def _wav_duration_and_rate(path: Path) -> tuple[float, int]:
        import wave

        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return (round(frames / rate, 3) if rate else 0.0), rate
