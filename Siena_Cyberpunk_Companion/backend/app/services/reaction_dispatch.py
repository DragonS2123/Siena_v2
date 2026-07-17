import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from app.domain.priorities import PRIORITY_RANK, EventPriority
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import (
    ReactionGenerationStatus,
    ReactionPriority,
    ReactionProviderStatus,
    ReactionRequest,
    SienaReaction,
)
from app.models.scene import ReactionOpportunity
from app.services.scene_context import SceneContextBuilder
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

    async def put(
        self,
        request: ReactionRequest,
        is_stale: Callable[[ReactionRequest], bool] | None = None,
        *,
        return_dropped: bool = False,
    ) -> bool | tuple[bool, ReactionRequest | None]:
        rank = PRIORITY_RANK[request.event.priority]
        dropped_request: ReactionRequest | None = None
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
                    dropped_request = dropped
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
                        dropped_request = dropped
                        logger.warning("reaction_queue_overflow dropped_event=%s incoming_event=%s", dropped.event.event_type, request.event.event_type)
                    else:
                        logger.warning("reaction_queue_overflow rejected_event=%s", request.event.event_type)
                        return (False, None) if return_dropped else False
            self._sequence += 1
            self._items.append((rank, self._sequence, request))
            self._condition.notify()
            return (True, dropped_request) if return_dropped else True

    async def get(self) -> ReactionRequest:
        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._items))
            best_index = min(range(len(self._items)), key=lambda index: (self._items[index][0], self._items[index][1]))
            return self._items.pop(best_index)[2]

    async def keep_session(self, session_id: str) -> int:
        return len(await self.drop_other_sessions(session_id))

    async def drop_other_sessions(self, session_id: str) -> list[ReactionRequest]:
        async with self._condition:
            dropped = [item for item in self._items if item[2].event.session_id != session_id]
            self._items = [item for item in self._items if item[2].event.session_id == session_id]
            for _, _, request in dropped:
                logger.warning(
                    "reaction_queue_session_drop event=%s critical=%s",
                    request.event.event_type,
                    request.event.priority == EventPriority.P0_CRITICAL,
                )
            return [request for _, _, request in dropped]

    async def drop_weaker_scene_requests(self, incoming: ReactionRequest) -> list[ReactionRequest]:
        incoming_rank = PRIORITY_RANK[incoming.event.priority]
        async with self._condition:
            dropped = [
                item
                for item in self._items
                if item[2].event.session_id == incoming.event.session_id
                and item[2].scene_id == incoming.scene_id
                and item[0] > incoming_rank
            ]
            dropped_ids = {item[2].request_id for item in dropped}
            self._items = [item for item in self._items if item[2].request_id not in dropped_ids]
            return [request for _, _, request in dropped]


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
        recent_reactions_limit: int,
        max_event_age_seconds: float,
        language: str,
        scene_builder: SceneContextBuilder | None = None,
        scene_stale_grace_seconds: float = 3.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.configured_provider = configured_provider
        self.planner = planner
        self.bus = bus
        self.core_provider = core_provider
        self.fallback_provider = fallback_provider
        self.queue = ReactionPriorityQueue(queue_capacity)
        self.recent_events_limit = recent_events_limit
        self.recent_reactions_limit = recent_reactions_limit
        self.max_event_age_seconds = max_event_age_seconds
        self.language = language
        self.scene_builder = scene_builder
        self.scene_stale_grace_seconds = scene_stale_grace_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._worker: asyncio.Task | None = None
        self._active_session: str | None = None
        self._ended_sessions: set[str] = set()
        self._recent: dict[str, deque[GameEvent]] = {}
        self._recent_reactions: dict[str, deque[SienaReaction]] = {}
        self._session_started_at: dict[str, datetime] = {}
        self._latest_critical_at: dict[str, datetime] = {}
        self.fallback_count = 0
        self.stale_suppressed_count = 0
        self._active_provider = "configuration_error" if configured_provider == "siena_core" and core_provider.configuration_error else configured_provider
        self._last_status_fingerprint: tuple | None = None
        self.on_reaction_published: Callable[[SienaReaction], Awaitable[None]] | None = None

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
        await self.observe_event(event)
        if self.configured_provider != "siena_core":
            return await self.planner.consider(event)
        if not await self.planner.reserve(event):
            return None
        request = self._request_for(event)
        await self._enqueue(request)
        return None

    async def handle_opportunity(self, opportunity: ReactionOpportunity, *, observe_event: bool = True) -> SienaReaction | None:
        event = opportunity.trigger_event
        if observe_event:
            await self.observe_event(event)
        if not await self.planner.reserve_opportunity(event):
            return None
        if self.configured_provider != "siena_core":
            text = TemplateReactionProvider().text_for_opportunity(opportunity)
            reaction = SienaReaction(
                event_id=event.event_id,
                session_id=event.session_id,
                event_type=event.event_type,
                text=text,
                priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM],
                provider=self.configured_provider,
                requested_provider=self.configured_provider,
                scene_id=opportunity.scene_id,
                scene_revision=opportunity.scene_revision,
                scene_phase=opportunity.scene_snapshot.phase,
                focus=opportunity.focus,
                metadata={"delivery_hint": opportunity.delivery_hint, "reaction_category": opportunity.reaction_category, "related_event_types": opportunity.related_event_types},
            )
            self._remember_reaction(reaction)
            if self.scene_builder:
                self.scene_builder.record_reaction(opportunity.scene_id)
            await self.planner.record_reaction()
            return reaction
        request = self._request_for(
            event,
            scene_id=opportunity.scene_id,
            scene_revision=opportunity.scene_revision,
            scene_phase=opportunity.scene_snapshot.phase,
            focus=opportunity.focus,
            scene_snapshot=opportunity.scene_snapshot,
            opportunity_expires_at=opportunity.expires_at,
            related_event_types=opportunity.related_event_types,
            tactical_context=opportunity.tactical_context,
            delivery_hint=opportunity.delivery_hint,
            reaction_category=opportunity.reaction_category,
        )
        if event.priority == EventPriority.P0_CRITICAL:
            for dropped in await self.queue.drop_weaker_scene_requests(request):
                logger.info(
                    "reaction_request_replaced request_id=%s replacement=%s scene_id=%s",
                    dropped.request_id,
                    request.request_id,
                    request.scene_id,
                )
                await self._publish_lifecycle(
                    dropped,
                    "suppressed",
                    reason="replaced_by_higher_priority_scene_update",
                )
        await self._enqueue(request)
        return None

    async def observe_event(self, event: GameEvent) -> None:
        if event.event_type == EventType.SESSION_STARTED:
            self._active_session = event.session_id
            self._ended_sessions.discard(event.session_id)
            self._session_started_at[event.session_id] = event.created_at
            self._recent_reactions = {
                event.session_id: deque(maxlen=self.recent_reactions_limit),
            }
            dropped = await self.queue.drop_other_sessions(event.session_id)
            for request in dropped:
                await self._publish_lifecycle(request, "suppressed", reason="session_changed")
        elif event.event_type == EventType.SESSION_ENDED:
            self._ended_sessions.add(event.session_id)
            if self._active_session == event.session_id:
                self._active_session = None
        self._recent.setdefault(event.session_id, deque(maxlen=self.recent_events_limit)).append(event)
        if event.priority == EventPriority.P0_CRITICAL:
            self._latest_critical_at[event.session_id] = event.created_at

    def _request_for(self, event: GameEvent, **scene_fields) -> ReactionRequest:
        return ReactionRequest(
            request_id=str(uuid4()),
            event=event,
            recent_events=list(self._recent[event.session_id]),
            recent_reactions=list(self._recent_reactions.get(event.session_id, ())),
            language=self.language,
            queued_at=self.clock(),
            **scene_fields,
        )

    async def _enqueue(self, request: ReactionRequest) -> None:
        accepted, dropped = await self.queue.put(request, self._is_stale, return_dropped=True)
        if dropped:
            await self._publish_lifecycle(dropped, "suppressed", reason="queue_overflow")
        if accepted:
            logger.info("siena_request_queued request_id=%s event_type=%s queue_size=%s", request.request_id, request.event.event_type, self.queue.qsize())
            await self._publish_lifecycle(request, "queued")
        await self._publish_status_if_changed()

    async def _run(self) -> None:
        while True:
            try:
                request = await self.queue.get()
                if self._is_stale(request):
                    await self._suppress_stale(request)
                    continue
                await self._publish_lifecycle(request, "generating")
                try:
                    result = await self.core_provider.generate_reaction(request, self._session_started_at.get(request.event.session_id))
                    if self._is_stale(request):
                        await self._suppress_stale(request)
                        continue
                    reaction = self._reaction_from_core(request, result)
                    self._active_provider = "siena_core"
                except SienaCoreProviderError as exc:
                    if self._is_stale(request):
                        await self._suppress_stale(request)
                        reaction = None
                    else:
                        reaction = self._fallback(request, exc)
                if reaction:
                    await self.bus.publish("siena_reaction", reaction)
                    if self.on_reaction_published:
                        await self.on_reaction_published(reaction)
                    self._remember_reaction(reaction)
                    if self.scene_builder and reaction.scene_id:
                        self.scene_builder.record_reaction(reaction.scene_id)
                    await self.planner.record_reaction()
                    await self._publish_lifecycle(request, "fallback" if reaction.fallback_used else "completed")
                elif not self._is_stale(request):
                    await self._publish_lifecycle(request, "failed")
                await self._publish_status_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("reaction worker recovered after error: %s", type(exc).__name__)
                if "request" in locals():
                    await self._publish_lifecycle(request, "failed")
                await self._publish_status_if_changed()

    def _is_stale(self, request: ReactionRequest) -> bool:
        event = request.event
        if event.session_id in self._ended_sessions or self._active_session != event.session_id:
            return True
        age_limit = self.max_event_age_seconds
        if event.priority == EventPriority.P0_CRITICAL:
            age_limit += self.scene_stale_grace_seconds
        if request.opportunity_expires_at:
            opportunity_age = (request.opportunity_expires_at - event.created_at).total_seconds()
            age_limit = min(age_limit, opportunity_age + (self.scene_stale_grace_seconds if event.priority == EventPriority.P0_CRITICAL else 0))
        if (self.clock() - event.created_at).total_seconds() > age_limit:
            return True
        newer_critical = self._latest_critical_at.get(event.session_id)
        if event.priority != EventPriority.P0_CRITICAL and newer_critical and newer_critical > event.created_at:
            return True
        if request.scene_id and self.scene_builder:
            return not self.scene_builder.is_request_relevant(
                request.scene_id,
                request.scene_revision or 1,
                request.focus.value if request.focus else None,
            )
        return False

    async def _suppress_stale(self, request: ReactionRequest) -> None:
        self.stale_suppressed_count += 1
        logger.info("reaction_stale_suppressed request_id=%s event_type=%s", request.request_id, request.event.event_type)
        await self._publish_lifecycle(request, "suppressed", reason="stale_scene_or_session")

    def _remember_reaction(self, reaction: SienaReaction) -> None:
        if not reaction.session_id:
            return
        self._recent_reactions.setdefault(
            reaction.session_id,
            deque(maxlen=self.recent_reactions_limit),
        ).append(reaction)

    async def _publish_lifecycle(self, request: ReactionRequest, state: str, reason: str | None = None) -> None:
        await self.bus.publish(
            "reaction_generation_status",
            ReactionGenerationStatus(
                request_id=request.request_id,
                event_id=request.event.event_id,
                session_id=request.event.session_id,
                event_type=request.event.event_type,
                state=state,
                scene_id=request.scene_id,
                scene_phase=request.scene_phase,
                focus=request.focus,
                reason=reason,
            ),
        )

    def _reaction_from_core(self, request, result) -> SienaReaction:
        event = request.event
        return SienaReaction(
            event_id=event.event_id, session_id=event.session_id, event_type=event.event_type,
            text=result.text, priority=REACTION_PRIORITY[event.severity or EventSeverity.MEDIUM], provider="siena_core",
            requested_provider="siena_core", latency_ms=result.latency_ms, model=result.model,
            request_id=request.request_id, metadata={"actual_provider": "siena_core", "delivery_hint": request.delivery_hint, "reaction_category": request.reaction_category, "related_event_types": request.related_event_types},
            scene_id=request.scene_id, scene_revision=request.scene_revision,
            scene_phase=request.scene_phase, focus=request.focus,
        )

    def _fallback(self, request: ReactionRequest, error: SienaCoreProviderError) -> SienaReaction | None:
        self._active_provider = "template_fallback" if self.fallback_provider == "template" else "disabled"
        if self.fallback_provider != "template":
            return None
        text = (
            TemplateReactionProvider().text_for_focus(request.focus, request.tactical_context)
            if request.focus
            else TemplateReactionProvider().text_for(request.event)
        )
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
            scene_id=request.scene_id, scene_revision=request.scene_revision,
            scene_phase=request.scene_phase, focus=request.focus,
            metadata={"requested_provider": "siena_core", "actual_provider": "template", "fallback_reason": error.category, "circuit_state": circuit, "delivery_hint": request.delivery_hint, "reaction_category": request.reaction_category, "related_event_types": request.related_event_types},
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
