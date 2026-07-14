import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from app.domain.priorities import PRIORITY_RANK, EventPriority
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import ReactionPriority, ReactionProviderStatus, ReactionRequest, SienaReaction
from app.services.event_bus import EventBus
from app.services.reaction_planner import REACTION_PRIORITY, ReactionPlanner, TemplateReactionProvider
from app.services.siena_core_reaction_provider import SienaCoreProviderError, SienaCoreReactionProvider

logger = logging.getLogger("siena_observer.reaction_worker")


class ReactionPriorityQueue:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._items: list[tuple[int, int, ReactionRequest]] = []
        self._sequence = 0
        self._condition = asyncio.Condition()

    def qsize(self) -> int:
        return len(self._items)

    async def put(self, request: ReactionRequest, is_stale: Callable[[ReactionRequest], bool] | None = None) -> bool:
        rank = PRIORITY_RANK[request.event.priority]
        async with self._condition:
            if len(self._items) >= self.capacity:
                stale_low = next(
                    (
                        index
                        for index, item in enumerate(self._items)
                        if item[2].event.priority == EventPriority.P3_LOW
                        and is_stale is not None
                        and is_stale(item[2])
                    ),
                    None,
                )
                if stale_low is not None:
                    dropped = self._items.pop(stale_low)[2]
                    logger.warning(
                        "reaction_queue_overflow dropped_stale_event=%s incoming_event=%s",
                        dropped.event.event_type,
                        request.event.event_type,
                    )
                else:
                    worst_index = max(range(len(self._items)), key=lambda index: (self._items[index][0], -self._items[index][1]))
                    worst_rank = self._items[worst_index][0]
                    if rank < worst_rank or rank == 0:
                        dropped = self._items.pop(worst_index)[2]
                        logger.warning("reaction_queue_overflow dropped_event=%s incoming_event=%s", dropped.event.event_type, request.event.event_type)
                    else:
                        logger.warning("reaction_queue_overflow rejected_event=%s", request.event.event_type)
                        return False
            self._sequence += 1
            self._items.append((rank, self._sequence, request))
            self._condition.notify()
            return True

    async def get(self) -> ReactionRequest:
        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._items))
            best_index = min(range(len(self._items)), key=lambda index: (self._items[index][0], self._items[index][1]))
            return self._items.pop(best_index)[2]

    async def keep_session(self, session_id: str) -> int:
        async with self._condition:
            dropped = [item for item in self._items if item[2].event.session_id != session_id]
            self._items = [item for item in self._items if item[2].event.session_id == session_id]
            for _, _, request in dropped:
                logger.warning(
                    "reaction_queue_session_drop event=%s critical=%s",
                    request.event.event_type,
                    request.event.priority == EventPriority.P0_CRITICAL,
                )
            return len(dropped)


class ReactionDispatchService:
    def __init__(
        self,
        *,
        configured_provider: str,
        planner: ReactionPlanner,
        bus: EventBus,
        core_provider: SienaCoreReactionProvider,
        fallback_provider: str,
        queue_capacity: int,
        recent_events_limit: int,
        max_event_age_seconds: float,
        language: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.configured_provider = configured_provider
        self.planner = planner
        self.bus = bus
        self.core_provider = core_provider
        self.fallback_provider = fallback_provider
        self.queue = ReactionPriorityQueue(queue_capacity)
        self.recent_events_limit = recent_events_limit
        self.max_event_age_seconds = max_event_age_seconds
        self.language = language
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._worker: asyncio.Task | None = None
        self._active_session: str | None = None
        self._ended_sessions: set[str] = set()
        self._recent: dict[str, deque[GameEvent]] = {}
        self._session_started_at: dict[str, datetime] = {}
        self._latest_critical_at: dict[str, datetime] = {}
        self.fallback_count = 0
        self.stale_suppressed_count = 0
        self._active_provider = "configuration_error" if configured_provider == "siena_core" and core_provider.configuration_error else configured_provider
        self._last_status_fingerprint: tuple | None = None

    async def start(self) -> None:
        if self.configured_provider == "siena_core" and not self._worker:
            self._worker = asyncio.create_task(self._run(), name="siena-reaction-worker")
            logger.info("reaction_worker_started")
            await self._publish_status_if_changed()

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
            logger.info("reaction_worker_stopped")
        await self.core_provider.close()

    async def handle_event(self, event: GameEvent) -> SienaReaction | None:
        if event.event_type == EventType.SESSION_STARTED:
            self._active_session = event.session_id
            self._ended_sessions.discard(event.session_id)
            self._session_started_at[event.session_id] = event.created_at
            await self.queue.keep_session(event.session_id)
        elif event.event_type == EventType.SESSION_ENDED:
            self._ended_sessions.add(event.session_id)
            if self._active_session == event.session_id:
                self._active_session = None
        self._recent.setdefault(event.session_id, deque(maxlen=self.recent_events_limit)).append(event)
        if event.priority == EventPriority.P0_CRITICAL:
            self._latest_critical_at[event.session_id] = event.created_at

        if self.configured_provider != "siena_core":
            return await self.planner.consider(event)
        if not await self.planner.reserve(event):
            return None
        request = ReactionRequest(
            request_id=str(uuid4()), event=event,
            recent_events=list(self._recent[event.session_id]), language=self.language, queued_at=self.clock(),
        )
        accepted = await self.queue.put(request, self._is_stale)
        if accepted:
            logger.info("siena_request_queued request_id=%s event_type=%s queue_size=%s", request.request_id, event.event_type, self.queue.qsize())
        await self._publish_status_if_changed()
        return None

    async def _run(self) -> None:
        while True:
            try:
                request = await self.queue.get()
                if self._is_stale(request):
                    self._suppress_stale(request)
                    continue
                try:
                    result = await self.core_provider.generate_reaction(request, self._session_started_at.get(request.event.session_id))
                    if self._is_stale(request):
                        self._suppress_stale(request)
                        continue
                    reaction = self._reaction_from_core(request, result)
                    self._active_provider = "siena_core"
                except SienaCoreProviderError as exc:
                    if self._is_stale(request):
                        self._suppress_stale(request)
                        reaction = None
                    else:
                        reaction = self._fallback(request, exc)
                if reaction:
                    await self.bus.publish("siena_reaction", reaction)
                    await self.planner.record_reaction()
                await self._publish_status_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("reaction worker recovered after error: %s", type(exc).__name__)
                await self._publish_status_if_changed()

    def _is_stale(self, request: ReactionRequest) -> bool:
        event = request.event
        if event.session_id in self._ended_sessions or self._active_session != event.session_id:
            return True
        if (self.clock() - event.created_at).total_seconds() > self.max_event_age_seconds:
            return True
        newer_critical = self._latest_critical_at.get(event.session_id)
        return bool(event.priority != EventPriority.P0_CRITICAL and newer_critical and newer_critical > event.created_at)

    def _suppress_stale(self, request: ReactionRequest) -> None:
        self.stale_suppressed_count += 1
        logger.info("reaction_stale_suppressed request_id=%s event_type=%s", request.request_id, request.event.event_type)

    def _reaction_from_core(self, request, result) -> SienaReaction:
        event = request.event
        return SienaReaction(
            event_id=event.event_id, session_id=event.session_id, event_type=event.event_type,
            text=result.text, priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM], provider="siena_core",
            requested_provider="siena_core", latency_ms=result.latency_ms, model=result.model,
            request_id=request.request_id, metadata={"actual_provider": "siena_core"},
        )

    def _fallback(self, request: ReactionRequest, error: SienaCoreProviderError) -> SienaReaction | None:
        self._active_provider = "template_fallback" if self.fallback_provider == "template" else "disabled"
        if self.fallback_provider != "template":
            return None
        text = TemplateReactionProvider().text_for(request.event)
        if not text:
            return None
        self.fallback_count += 1
        circuit = self.core_provider.circuit.status().state
        logger.warning("siena_fallback_used request_id=%s reason=%s circuit_state=%s", request.request_id, error.category, circuit)
        event = request.event
        return SienaReaction(
            event_id=event.event_id, session_id=event.session_id, event_type=event.event_type,
            text=text, priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM], provider="template_fallback",
            requested_provider="siena_core", fallback_used=True, fallback_reason=error.category,
            request_id=request.request_id,
            metadata={"requested_provider": "siena_core", "actual_provider": "template", "fallback_reason": error.category, "circuit_state": circuit},
        )

    async def status(self) -> ReactionProviderStatus:
        circuit = self.core_provider.circuit.status()
        return ReactionProviderStatus(
            enabled=self.planner.enabled,
            configured_provider=self.configured_provider,
            active_provider=self._active_provider,
            fallback_provider=self.fallback_provider,
            configuration_error=self.core_provider.configuration_error if self.configured_provider == "siena_core" else None,
            siena_core_reachable=self.core_provider.reachable,
            circuit_state=circuit.state,
            consecutive_failures=circuit.consecutive_failures,
            queue_size=self.queue.qsize(), queue_capacity=self.queue.capacity,
            worker_running=bool(self._worker and not self._worker.done()),
            last_request_at=self.core_provider.last_request_at, last_success_at=self.core_provider.last_success_at,
            last_failure_at=self.core_provider.last_failure_at, last_error=self.core_provider.last_error,
            average_latency_ms=self.core_provider.average_latency_ms,
            fallback_count=self.fallback_count, stale_suppressed_count=self.stale_suppressed_count,
        )

    async def _publish_status_if_changed(self) -> None:
        status = await self.status()
        fingerprint = (
            status.active_provider, status.siena_core_reachable, status.circuit_state,
            status.configuration_error, status.last_error, status.worker_running,
        )
        if fingerprint != self._last_status_fingerprint:
            self._last_status_fingerprint = fingerprint
            await self.bus.publish("reaction_provider_status", status)
