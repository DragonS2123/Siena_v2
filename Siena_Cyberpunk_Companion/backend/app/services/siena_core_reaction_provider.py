import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
from pydantic import ValidationError

from app.models.reaction import ReactionRequest, SienaCoreMetadata, SienaCoreRequest, SienaCoreResponse
from app.services.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.services.game_reaction_prompt import GameReactionPromptBuilder
from app.services.reaction_response_sanitizer import InvalidReactionResponse, ReactionResponseSanitizer

logger = logging.getLogger("siena_observer.siena_core")


class SienaCoreProviderError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False, category: str = "provider_failure") -> None:
        super().__init__(message)
        self.transient = transient
        self.category = category


@dataclass(slots=True)
class ProviderResult:
    text: str
    model: str | None
    latency_ms: float
    request_id: str
    metadata: dict = field(default_factory=dict)


class SienaCoreReactionProvider:
    name = "siena_core"

    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        api_token: str = "",
        connect_timeout_seconds: float = 2,
        request_timeout_seconds: float = 45,
        max_retries: int = 1,
        max_response_chars: int = 320,
        failure_threshold: int = 3,
        circuit_reset_seconds: float = 30,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] | None = None,
        player_name: str = "",
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.configuration_error = None if enabled and self.base_url else "SIENA_CORE_ENABLED=true and SIENA_CORE_BASE_URL are required"
        self.max_retries = max_retries
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.player_name = player_name.strip()
        self.prompt_builder = GameReactionPromptBuilder()
        self.sanitizer = ReactionResponseSanitizer(max_response_chars)
        self.circuit = CircuitBreaker(failure_threshold, circuit_reset_seconds, self.clock)
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        timeout = httpx.Timeout(request_timeout_seconds, connect=connect_timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)
        self._owns_client = client is None
        self.last_request_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = self.configuration_error
        self.reachable: bool | None = None
        self.latencies: list[float] = []

    async def generate_reaction(self, request: ReactionRequest, session_started_at: datetime | None = None) -> ProviderResult:
        if self.configuration_error:
            raise SienaCoreProviderError(self.configuration_error, category="configuration_error")
        try:
            self.circuit.allow_request()
        except CircuitOpenError as exc:
            raise SienaCoreProviderError(str(exc), category="circuit_open") from exc

        event = request.event
        prompt = self.prompt_builder.build(
            event,
            request.recent_events,
            request.language,
            session_started_at,
            request.recent_reactions,
            self.player_name,
            request.scene_snapshot,
            request.focus,
        )
        metadata = SienaCoreMetadata(
            game_session_id=event.session_id,
            event_id=event.event_id,
            request_id=request.request_id,
        )
        payload = SienaCoreRequest(prompt=prompt, request_id=request.request_id, language=request.language, metadata=metadata)
        self.last_request_at = self.clock()
        logger.info("siena_request_started request_id=%s event_type=%s", request.request_id, event.event_type)

        started = time.monotonic()
        last_error: SienaCoreProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self.base_url}/api/external/game-reaction",
                    json=payload.model_dump(mode="json"),
                )
                if response.status_code >= 500:
                    raise SienaCoreProviderError(f"Siena Core HTTP {response.status_code}", transient=True, category="http_5xx")
                if response.status_code >= 400:
                    category = "authentication_error" if response.status_code in {401, 403} else "configuration_error"
                    raise SienaCoreProviderError(f"Siena Core HTTP {response.status_code}", category=category)
                try:
                    parsed = SienaCoreResponse.model_validate(response.json())
                except (ValueError, ValidationError) as exc:
                    raise SienaCoreProviderError("Siena Core returned malformed response", category="malformed_response") from exc
                text = self.sanitizer.sanitize(parsed.text, parsed.reasoning)
                latency_ms = (time.monotonic() - started) * 1000
                self.circuit.record_success()
                self.reachable = True
                self.last_success_at = self.clock()
                self.last_error = None
                self.latencies = [*self.latencies[-19:], latency_ms]
                logger.info("siena_request_completed request_id=%s latency_ms=%.1f", request.request_id, latency_ms)
                return ProviderResult(text=text, model=parsed.model, latency_ms=latency_ms, request_id=request.request_id)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = SienaCoreProviderError(f"Siena Core transport failure: {type(exc).__name__}", transient=True, category="transport_error")
            except InvalidReactionResponse as exc:
                last_error = SienaCoreProviderError(str(exc), category="response_validation_failure")
            except SienaCoreProviderError as exc:
                last_error = exc

            if last_error.transient and attempt < self.max_retries:
                logger.warning("siena_request_retried request_id=%s attempt=%s reason=%s", request.request_id, attempt + 1, last_error.category)
                await self.sleep(0.1 * (attempt + 1))
                continue
            break

        self.circuit.record_failure()
        self.reachable = False
        self.last_failure_at = self.clock()
        self.last_error = str(last_error or "unknown Siena Core failure")
        logger.warning("siena_request_failed request_id=%s category=%s", request.request_id, (last_error.category if last_error else "unknown"))
        raise last_error or SienaCoreProviderError("unknown Siena Core failure")

    async def probe_health(self) -> bool:
        if self.configuration_error:
            return False
        try:
            response = await self._client.get(f"{self.base_url}/api/health")
            self.reachable = response.status_code == 200
        except httpx.HTTPError:
            self.reachable = False
        return bool(self.reachable)

    @property
    def average_latency_ms(self) -> float | None:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
