import io
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from app.models.voice import VoiceRequest
from app.services.tts_circuit_breaker import TtsCircuitBreaker, TtsCircuitOpenError


class SienaTtsClientError(RuntimeError):
    def __init__(self, category: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass(slots=True)
class SynthesizedAudio:
    content: bytes
    content_type: str
    sample_rate: int
    channels: int
    duration_ms: int
    provider: str
    speaker: str | None
    language: str
    latency_ms: float


class SienaTtsClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        connect_timeout_seconds: float,
        request_timeout_seconds: float,
        max_retries: int,
        max_audio_bytes: int,
        failure_threshold: int,
        circuit_reset_seconds: float,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.max_retries = max_retries
        self.max_audio_bytes = max_audio_bytes
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.circuit = TtsCircuitBreaker(failure_threshold, circuit_reset_seconds, self.clock)
        timeout = httpx.Timeout(request_timeout_seconds, connect=connect_timeout_seconds)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.reachable: bool | None = None
        self.provider: str | None = None
        self.speaker: str | None = None
        self.language: str | None = None
        self.last_request_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None
        self._latencies: list[float] = []

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def average_latency_ms(self) -> float | None:
        return sum(self._latencies) / len(self._latencies) if self._latencies else None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}

    async def probe_status(self) -> bool:
        if not self.configured:
            self.reachable = False
            self.last_error = "TTS base URL is not configured"
            return False
        try:
            response = await self.client.get(f"{self.base_url}/api/voice/status", headers=self._headers())
            response.raise_for_status()
            data = response.json()
            self.reachable = bool(data.get("tts_available"))
            self.provider = data.get("tts_provider")
            self.speaker = data.get("tts_voice")
            self.language = data.get("tts_language")
            self.last_error = None if self.reachable else "TTS provider is unavailable"
            return self.reachable
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            self.reachable = False
            self.last_error = type(exc).__name__
            return False

    async def synthesize(self, request: VoiceRequest) -> SynthesizedAudio:
        if not self.configured:
            raise SienaTtsClientError("configuration_error", "TTS base URL is not configured")
        try:
            self.circuit.allow_request()
        except TtsCircuitOpenError as exc:
            raise SienaTtsClientError("circuit_open", str(exc)) from exc

        self.last_request_at = self.clock()
        started = time.perf_counter()
        last_error: SienaTtsClientError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self.client.stream(
                    "POST",
                    f"{self.base_url}/api/external/speech",
                    headers=self._headers(),
                    json={
                        "request_id": request.voice_request_id,
                        "text": request.text,
                        "speaker": request.speaker,
                        "language": request.language,
                        "audio_format": request.requested_format,
                    },
                ) as response:
                    if response.status_code in {401, 403}:
                        raise SienaTtsClientError("authentication_error", f"TTS returned HTTP {response.status_code}")
                    if 400 <= response.status_code < 500:
                        raise SienaTtsClientError("http_4xx", f"TTS returned HTTP {response.status_code}")
                    if response.status_code >= 500:
                        raise SienaTtsClientError("http_5xx", f"TTS returned HTTP {response.status_code}", retryable=True)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
                        raise SienaTtsClientError("unsupported_content_type", f"unsupported TTS content type: {content_type}", retryable=True)
                    audio = bytearray()
                    async for chunk in response.aiter_bytes():
                        audio.extend(chunk)
                        if len(audio) > self.max_audio_bytes:
                            raise SienaTtsClientError("response_size_overflow", "TTS audio exceeds configured limit", retryable=True)
                    content = bytes(audio)
                    if not content:
                        raise SienaTtsClientError("empty_audio", "TTS returned empty audio", retryable=True)
                sample_rate, channels, duration_ms = self._validate_wav(content)
                latency_ms = (time.perf_counter() - started) * 1000
                provider = response.headers.get("x-siena-tts-provider", self.provider or "unknown")
                speaker = response.headers.get("x-siena-tts-speaker") or request.speaker or self.speaker
                language = response.headers.get("x-siena-tts-language", request.language)
                self.circuit.record_success()
                self.reachable = True
                self.provider = provider
                self.speaker = speaker
                self.language = language
                self.last_success_at = self.clock()
                self.last_error = None
                self._latencies = [*self._latencies[-19:], latency_ms]
                return SynthesizedAudio(
                    content=content, content_type="audio/wav", sample_rate=sample_rate,
                    channels=channels, duration_ms=duration_ms, provider=provider,
                    speaker=speaker, language=language, latency_ms=latency_ms,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = SienaTtsClientError("timeout" if isinstance(exc, httpx.TimeoutException) else "connection_error", type(exc).__name__, retryable=True)
            except SienaTtsClientError as exc:
                last_error = exc
            if not last_error.retryable or attempt >= self.max_retries:
                break

        assert last_error is not None
        self.reachable = False
        self.last_failure_at = self.clock()
        self.last_error = last_error.category
        if last_error.category not in {"authentication_error", "http_4xx", "configuration_error"}:
            self.circuit.record_failure()
        else:
            self.circuit.record_configuration_error()
        raise last_error

    @staticmethod
    def _validate_wav(content: bytes) -> tuple[int, int, int]:
        if len(content) < 44 or not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
            raise SienaTtsClientError("malformed_audio", "TTS returned malformed WAV", retryable=True)
        try:
            with wave.open(io.BytesIO(content), "rb") as wav:
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                frames = wav.getnframes()
        except (wave.Error, EOFError) as exc:
            raise SienaTtsClientError("malformed_audio", "TTS returned malformed WAV", retryable=True) from exc
        if sample_rate <= 0 or channels <= 0 or frames <= 0:
            raise SienaTtsClientError("malformed_audio", "WAV contains no playable frames", retryable=True)
        return sample_rate, channels, round(frames / sample_rate * 1000)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
