from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.models.game_event import EventType, GameEvent
from app.models.reaction import SienaReaction
from app.models.voice import (
    VoiceClip,
    VoiceClipReady,
    VoiceGenerationStatus,
    VoiceOpportunity,
    VoicePlaybackEvent,
    VoicePriority,
    VoiceRequest,
    VoiceState,
    VoiceStatus,
)
from app.services.event_bus import EventBus
from app.services.scene_context import SceneContextBuilder
from app.services.siena_tts_client import SienaTtsClient, SienaTtsClientError
from app.services.voice_behavior_policy import VoiceBehaviorPolicy

logger = logging.getLogger("siena_observer.voice_worker")

VOICE_RANK = {
    VoicePriority.CRITICAL: 0,
    VoicePriority.HIGH: 1,
    VoicePriority.MEDIUM: 2,
    VoicePriority.LOW: 3,
    VoicePriority.INFO: 4,
}


class VoicePlaybackConflict(RuntimeError):
    pass


class VoicePriorityQueue:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._items: list[tuple[int, int, VoiceRequest]] = []
        self._sequence = 0
        self._condition = asyncio.Condition()

    def qsize(self) -> int:
        return len(self._items)

    async def put(self, request: VoiceRequest, is_stale: Callable[[VoiceRequest], bool]) -> tuple[bool, list[VoiceRequest]]:
        dropped: list[VoiceRequest] = []
        rank = VOICE_RANK[request.priority]
        async with self._condition:
            if len(self._items) >= self.capacity:
                stale_index = next((
                    index for index, (_, _, queued) in enumerate(self._items)
                    if queued.priority in {VoicePriority.INFO, VoicePriority.LOW} and is_stale(queued)
                ), None)
                if stale_index is not None:
                    dropped.append(self._items.pop(stale_index)[2])
                else:
                    worst = max(range(len(self._items)), key=lambda index: (self._items[index][0], -self._items[index][1]))
                    if rank < self._items[worst][0] or request.priority == VoicePriority.CRITICAL:
                        dropped.append(self._items.pop(worst)[2])
                    else:
                        return False, dropped
            self._sequence += 1
            self._items.append((rank, self._sequence, request))
            self._condition.notify()
            return True, dropped

    async def get(self) -> VoiceRequest:
        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._items))
            best = min(range(len(self._items)), key=lambda index: (self._items[index][0], self._items[index][1]))
            return self._items.pop(best)[2]

    async def drop_other_sessions(self, session_id: str | None) -> list[VoiceRequest]:
        async with self._condition:
            dropped = [item[2] for item in self._items if item[2].session_id != session_id]
            self._items = [item for item in self._items if item[2].session_id == session_id]
            return dropped

    async def drop_weaker_scene(self, incoming: VoiceRequest) -> list[VoiceRequest]:
        incoming_rank = VOICE_RANK[incoming.priority]
        async with self._condition:
            dropped = [
                item[2] for item in self._items
                if item[2].session_id == incoming.session_id
                and item[2].scene_id == incoming.scene_id
                and item[0] > incoming_rank
            ]
            dropped_ids = {item.voice_request_id for item in dropped}
            self._items = [item for item in self._items if item[2].voice_request_id not in dropped_ids]
            return dropped


@dataclass(slots=True)
class StoredVoiceClip:
    clip: VoiceClip
    audio: bytes


class VoiceClipStore:
    def __init__(self, limit: int, clock: Callable[[], datetime]) -> None:
        self.limit = limit
        self.clock = clock
        self._items: OrderedDict[str, StoredVoiceClip] = OrderedDict()

    def put(self, clip: VoiceClip, audio: bytes) -> None:
        self.cleanup()
        self._items[clip.voice_clip_id] = StoredVoiceClip(clip=clip, audio=audio)
        while len(self._items) > self.limit:
            removable = next((key for key, item in self._items.items() if item.clip.state != VoiceState.PLAYING), None)
            if removable is None:
                removable = next(iter(self._items))
            self._items.pop(removable, None)

    def get(self, clip_id: str) -> StoredVoiceClip | None:
        self.cleanup()
        return self._items.get(clip_id)

    def list(self, limit: int = 100, session_id: str | None = None) -> list[VoiceClip]:
        self.cleanup()
        values = reversed(self._items.values())
        return [item.clip.model_copy(deep=True) for item in values if not session_id or item.clip.session_id == session_id][:limit]

    def update(self, clip_id: str, state: VoiceState, error: str | None = None) -> VoiceClip | None:
        item = self._items.get(clip_id)
        if not item:
            return None
        item.clip.state = state
        item.clip.error_category = error
        return item.clip.model_copy(deep=True)

    def cleanup(self) -> None:
        now = self.clock()
        expired = [
            key for key, item in self._items.items()
            if item.clip.expires_at <= now and item.clip.state != VoiceState.PLAYING
        ]
        for key in expired:
            self._items.pop(key, None)

    def suppress_other_sessions(self, session_id: str | None) -> list[VoiceClip]:
        changed = []
        for item in self._items.values():
            if item.clip.session_id != session_id and item.clip.state in {VoiceState.READY, VoiceState.QUEUED}:
                item.clip.state = VoiceState.SUPPRESSED
                item.clip.error_category = "old_session"
                changed.append(item.clip.model_copy(deep=True))
        return changed

    def all_records(self) -> list[StoredVoiceClip]:
        self.cleanup()
        return list(self._items.values())


class VoiceDispatchService:
    def __init__(
        self,
        *,
        enabled: bool,
        muted: bool,
        policy: VoiceBehaviorPolicy,
        bus: EventBus,
        tts_client: SienaTtsClient,
        scene_builder: SceneContextBuilder,
        queue_capacity: int,
        clip_history_limit: int,
        audio_ttl_seconds: float,
        max_event_age_seconds: float,
        language: str,
        speaker: str,
        interrupt_mode: str = "critical_only",
        post_play_gap_ms: int = 250,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.enabled = enabled
        self.muted = muted
        self.policy = policy
        self.bus = bus
        self.tts_client = tts_client
        self.scene_builder = scene_builder
        self.queue = VoicePriorityQueue(queue_capacity)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.store = VoiceClipStore(clip_history_limit, self.clock)
        self.audio_ttl = timedelta(seconds=audio_ttl_seconds)
        self.max_event_age = timedelta(seconds=max_event_age_seconds)
        self.language = language
        self.speaker = speaker or None
        self.interrupt_mode = interrupt_mode
        self.post_play_gap_ms = post_play_gap_ms
        self._worker: asyncio.Task | None = None
        self._active_session: str | None = None
        self._current: VoiceRequest | None = None
        self._superseded: set[str] = set()
        self._playing_clip_id: str | None = None
        self._playing_tab_id: str | None = None
        self.suppressed_count = 0
        self.failed_count = 0
        self.on_playback_event = None

    async def start(self) -> None:
        if self.enabled and not self._worker:
            self._worker = asyncio.create_task(self._run(), name="siena-voice-worker")
            logger.info("voice_worker_started")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        await self.tts_client.close()
        logger.info("voice_worker_stopped")

    async def observe_event(self, event: GameEvent) -> None:
        if event.event_type == EventType.SESSION_STARTED:
            self._active_session = event.session_id
            self.policy.reset_session()
            for request in await self.queue.drop_other_sessions(event.session_id):
                await self._publish_status(request, VoiceState.CANCELLED, "old_session")
            if self._current and self._current.session_id != event.session_id:
                self._superseded.add(self._current.voice_request_id)
            for clip in self.store.suppress_other_sessions(event.session_id):
                await self._publish_clip_status(clip, VoiceState.SUPPRESSED, "old_session")
        elif event.event_type == EventType.SESSION_ENDED:
            if self._active_session == event.session_id:
                self._active_session = None
            for request in await self.queue.drop_other_sessions(None):
                await self._publish_status(request, VoiceState.CANCELLED, "old_session")
            if self._current and self._current.session_id == event.session_id:
                self._superseded.add(self._current.voice_request_id)
            for clip in self.store.suppress_other_sessions(None):
                await self._publish_clip_status(clip, VoiceState.SUPPRESSED, "old_session")
        await self.suppress_irrelevant_ready()

    async def handle_reaction(self, reaction: SienaReaction) -> VoiceRequest | None:
        scene = self.scene_builder.current()
        tts_ready = self.tts_client.reachable is True and self.tts_client.circuit.ready_for_attempt()
        opportunity = self.policy.evaluate(reaction, scene, self._active_session, tts_ready)
        if not opportunity:
            self.suppressed_count += 1
            return None
        request = opportunity.request
        if request.priority == VoicePriority.CRITICAL:
            for dropped in await self.queue.drop_weaker_scene(request):
                await self._publish_status(dropped, VoiceState.SUPPRESSED, "replaced_by_critical")
            if (
                self._current
                and self._current.session_id == request.session_id
                and self._current.scene_id == request.scene_id
                and VOICE_RANK[self._current.priority] > VOICE_RANK[request.priority]
            ):
                self._superseded.add(self._current.voice_request_id)
        accepted, dropped = await self.queue.put(request, self._is_stale)
        for removed in dropped:
            await self._publish_status(removed, VoiceState.SUPPRESSED, "queue_overflow")
        if not accepted:
            self.suppressed_count += 1
            await self._publish_status(request, VoiceState.SUPPRESSED, "queue_overflow")
            return None
        await self._publish_status(request, VoiceState.QUEUED)
        return request

    async def _run(self) -> None:
        await self.tts_client.probe_status()
        while True:
            try:
                try:
                    request = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                except TimeoutError:
                    if self.tts_client.reachable is not True:
                        await self.tts_client.probe_status()
                    self.store.cleanup()
                    continue
                if self._is_stale(request):
                    await self._suppress(request, "stale_reaction")
                    continue
                self._current = request
                await self._publish_status(request, VoiceState.SYNTHESIZING)
                try:
                    result = await self.tts_client.synthesize(request)
                except SienaTtsClientError as exc:
                    self.failed_count += 1
                    await self._publish_status(request, VoiceState.FAILED, exc.category)
                    continue
                if self._is_stale(request):
                    await self._suppress(request, "stale_reaction")
                    continue
                now = self.clock()
                clip = VoiceClip(
                    voice_request_id=request.voice_request_id,
                    reaction_id=request.reaction_id,
                    session_id=request.session_id,
                    scene_id=request.scene_id,
                    scene_revision=request.scene_revision,
                    focus=request.focus,
                    priority=request.priority,
                    state=VoiceState.READY,
                    sample_rate=result.sample_rate,
                    channels=result.channels,
                    duration_ms=result.duration_ms,
                    byte_length=len(result.content),
                    created_at=now,
                    expires_at=now + self.audio_ttl,
                    tts_provider=result.provider,
                    speaker=result.speaker,
                    language=request.language,
                    latency_ms=result.latency_ms,
                    metadata=request.metadata,
                )
                self.store.put(clip, result.content)
                await self._publish_status(request, VoiceState.READY)
                await self.bus.publish(
                    "voice_clip_ready",
                    VoiceClipReady(
                        voice_clip_id=clip.voice_clip_id,
                        voice_request_id=clip.voice_request_id,
                        reaction_id=clip.reaction_id,
                        session_id=clip.session_id,
                        scene_id=clip.scene_id,
                        scene_revision=clip.scene_revision,
                        focus=clip.focus,
                        priority=clip.priority,
                        audio_url=f"/api/v1/voice/audio/{clip.voice_clip_id}",
                        content_type=clip.content_type,
                        duration_ms=clip.duration_ms,
                        tts_provider=clip.tts_provider,
                        speaker=clip.speaker,
                        expires_at=clip.expires_at,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("voice_worker_recovered")
                self.failed_count += 1
            finally:
                self._current = None

    def _is_stale(self, request: VoiceRequest) -> bool:
        now = self.clock()
        if request.voice_request_id in self._superseded:
            return True
        if request.session_id != self._active_session:
            return True
        if now > request.expires_at or now - request.created_at > self.max_event_age:
            return True
        if request.scene_id:
            return not self.scene_builder.is_request_relevant(
                request.scene_id,
                request.scene_revision or 1,
                request.focus.value if request.focus else None,
            )
        return False

    async def _suppress(self, request: VoiceRequest, reason: str) -> None:
        self.suppressed_count += 1
        category = "old_session" if request.session_id != self._active_session else reason
        logger.info("voice_stale_suppressed voice_request_id=%s reason=%s", request.voice_request_id, category)
        await self._publish_status(request, VoiceState.SUPPRESSED, category)

    async def suppress_irrelevant_ready(self) -> None:
        for record in self.store.all_records():
            clip = record.clip
            if clip.state != VoiceState.READY:
                continue
            request = VoiceRequest(
                voice_request_id=clip.voice_request_id,
                reaction_id=clip.reaction_id,
                event_id=str(clip.metadata.get("event_id", clip.reaction_id)),
                session_id=clip.session_id,
                scene_id=clip.scene_id,
                scene_revision=clip.scene_revision,
                focus=clip.focus,
                priority=clip.priority,
                text=str(clip.metadata.get("text_preview", "voice clip")),
                provider=str(clip.metadata.get("reaction_provider", "unknown")),
                speaker=clip.speaker,
                language=clip.language,
                created_at=clip.created_at,
                expires_at=clip.expires_at,
            )
            if self._is_stale(request):
                self.store.update(clip.voice_clip_id, VoiceState.SUPPRESSED, "stale_reaction")
                await self._publish_clip_status(clip, VoiceState.SUPPRESSED, "stale_reaction")

    async def playback_event(self, event: VoicePlaybackEvent) -> VoiceClip:
        record = self.store.get(event.voice_clip_id)
        if not record:
            raise KeyError(event.voice_clip_id)
        clip = record.clip
        if clip.session_id != self._active_session or clip.expires_at <= self.clock():
            self.store.update(clip.voice_clip_id, VoiceState.SUPPRESSED, "old_session")
            raise VoicePlaybackConflict("voice clip is stale")
        states = {
            "playback_started": VoiceState.PLAYING,
            "playback_completed": VoiceState.COMPLETED,
            "playback_failed": VoiceState.FAILED,
            "playback_cancelled": VoiceState.CANCELLED,
        }
        state = states[event.event]
        if state == VoiceState.PLAYING:
            if self._playing_clip_id and (self._playing_clip_id != clip.voice_clip_id or self._playing_tab_id != event.tab_id):
                raise VoicePlaybackConflict("another tab or clip already owns playback")
            self._playing_clip_id = clip.voice_clip_id
            self._playing_tab_id = event.tab_id
        elif self._playing_clip_id == clip.voice_clip_id:
            self._playing_clip_id = None
            self._playing_tab_id = None
        updated = self.store.update(clip.voice_clip_id, state, event.reason)
        assert updated is not None
        await self._publish_clip_status(updated, state, event.reason)
        if self.on_playback_event is not None:
            await self.on_playback_event(updated, state)
        return updated

    def audio(self, clip_id: str) -> tuple[VoiceClip, bytes] | None:
        record = self.store.get(clip_id)
        if not record:
            return None
        if record.clip.session_id != self._active_session or record.clip.state in {
            VoiceState.SUPPRESSED, VoiceState.CANCELLED, VoiceState.FAILED,
        }:
            return None
        return record.clip.model_copy(deep=True), record.audio

    async def _publish_status(self, request: VoiceRequest, state: VoiceState, reason: str | None = None) -> None:
        await self.bus.publish(
            "voice_generation_status",
            VoiceGenerationStatus(
                voice_request_id=request.voice_request_id,
                reaction_id=request.reaction_id,
                session_id=request.session_id,
                scene_id=request.scene_id,
                state=state,
                priority=request.priority,
                reason=reason,
            ),
        )

    async def _publish_clip_status(self, clip: VoiceClip, state: VoiceState, reason: str | None = None) -> None:
        await self.bus.publish(
            "voice_generation_status",
            VoiceGenerationStatus(
                voice_request_id=clip.voice_request_id,
                reaction_id=clip.reaction_id,
                session_id=clip.session_id,
                scene_id=clip.scene_id,
                state=state,
                priority=clip.priority,
                reason=reason,
            ),
        )

    async def status(self) -> VoiceStatus:
        circuit = self.tts_client.circuit
        return VoiceStatus(
            enabled=self.enabled,
            muted=self.muted,
            configured=self.tts_client.configured,
            tts_base_url="configured" if self.tts_client.configured else "not_configured",
            tts_reachable=self.tts_client.reachable,
            tts_provider=self.tts_client.provider,
            speaker=self.speaker or self.tts_client.speaker,
            language=self.language,
            interrupt_mode=self.interrupt_mode,
            post_play_gap_ms=self.post_play_gap_ms,
            circuit_state=circuit.state,
            consecutive_failures=circuit.consecutive_failures,
            worker_running=bool(self._worker and not self._worker.done()),
            queue_size=self.queue.qsize(),
            queue_capacity=self.queue.capacity,
            clip_count=len(self.store.list(self.store.limit)),
            currently_synthesizing=self._current.voice_request_id if self._current else None,
            last_request_at=self.tts_client.last_request_at,
            last_success_at=self.tts_client.last_success_at,
            last_failure_at=self.tts_client.last_failure_at,
            last_error=self.tts_client.last_error,
            average_latency_ms=self.tts_client.average_latency_ms,
            suppressed_count=self.suppressed_count,
            failed_count=self.failed_count,
        )
