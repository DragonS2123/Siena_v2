import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.models.bridge import BridgeStatus
from app.models.game_event import EventType, GameEvent
from app.models.presence import (
    InGamePresenceMetadata,
    InGamePresenceSnapshot,
    InGamePresenceState,
    InGamePresenceStatus,
)
from app.models.reaction import ReactionGenerationStatus, ReactionPriority, SienaReaction
from app.models.scene import ReactionFocus, SceneContext, ScenePhase
from app.models.voice import VoiceGenerationStatus, VoiceState


PRIORITY_RANK = {
    ReactionPriority.LOW: 0,
    ReactionPriority.MEDIUM: 1,
    ReactionPriority.HIGH: 2,
    ReactionPriority.CRITICAL: 3,
}


class InGamePresencePolicy:
    def __init__(
        self,
        *,
        generating_delay_ms: int,
        normal_duration_ms: int,
        high_duration_ms: int,
        critical_duration_ms: int,
        fallback_duration_ms: int,
        max_text_chars: int,
    ) -> None:
        self.generating_delay_ms = generating_delay_ms
        self.normal_duration_ms = normal_duration_ms
        self.high_duration_ms = high_duration_ms
        self.critical_duration_ms = critical_duration_ms
        self.fallback_duration_ms = fallback_duration_ms
        self.max_text_chars = max_text_chars

    def sanitize_text(self, value: str) -> str:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value or "")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()[:3]]
        text = "\n".join(line for line in lines if line).strip()
        if len(text) <= self.max_text_chars:
            return text
        clipped = text[: self.max_text_chars + 1]
        boundary = max(clipped.rfind(" "), clipped.rfind("\n"))
        if boundary >= max(1, self.max_text_chars // 2):
            clipped = clipped[:boundary]
        return clipped[: self.max_text_chars].rstrip()

    def duration_for(self, priority: ReactionPriority, fallback: bool) -> int:
        if fallback:
            return self.fallback_duration_ms
        if priority == ReactionPriority.CRITICAL:
            return self.critical_duration_ms
        if priority == ReactionPriority.HIGH:
            return self.high_duration_ms
        return self.normal_duration_ms

    def may_replace(self, current: InGamePresenceSnapshot, incoming: ReactionPriority, now: datetime) -> bool:
        if not current.active or not current.priority or not current.expires_at or current.expires_at <= now:
            return True
        if current.priority == ReactionPriority.CRITICAL and incoming != ReactionPriority.CRITICAL:
            return False
        return PRIORITY_RANK[incoming] >= PRIORITY_RANK[current.priority]


class InGamePresenceProjection:
    def __init__(
        self,
        *,
        enabled: bool,
        policy: InGamePresencePolicy,
        poll_interval_ms: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.enabled = enabled
        self.policy = policy
        self.poll_interval_ms = poll_interval_ms
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        now = self.clock()
        self._snapshot = self._hidden(0, now)
        self._active_session: str | None = None
        self._ended_sessions: set[str] = set()
        self._pending: ReactionGenerationStatus | None = None
        self._generating_due: datetime | None = None
        self._revision = 0
        self.last_poll_at: datetime | None = None
        self.last_revision_sent: int | None = None
        self.poll_count = 0
        self.unchanged_count = 0
        self.last_error: str | None = None
        self.overlay_supported: bool | None = None
        self.overlay_enabled: bool | None = None
        self.overlay_version: str | None = None
        self.font_cyrillic_ready: bool | None = None
        self._last_status_signature: tuple | None = None

    def _hidden(self, revision: int, now: datetime) -> InGamePresenceSnapshot:
        return InGamePresenceSnapshot(
            active=False,
            revision=revision,
            state=InGamePresenceState.HIDDEN,
            created_at=now,
            updated_at=now,
            display_duration_ms=0,
        )

    def current(self) -> InGamePresenceSnapshot:
        self.tick()
        return self._snapshot.model_copy(deep=True)

    def _set(self, candidate: InGamePresenceSnapshot) -> bool:
        comparable = candidate.model_dump(exclude={"revision", "created_at", "updated_at", "expires_at"})
        current = self._snapshot.model_dump(exclude={"revision", "created_at", "updated_at", "expires_at"})
        if comparable == current:
            return False
        self._revision += 1
        candidate.revision = self._revision
        self._snapshot = candidate
        return True

    def _hide(self, now: datetime) -> bool:
        if not self._snapshot.active and self._snapshot.state == InGamePresenceState.HIDDEN:
            return False
        return self._set(self._hidden(self._revision + 1, now))

    def observe(self, message_type: str, data: object) -> list[tuple[str, InGamePresenceStatus]]:
        if not self.enabled:
            return []
        now = self.clock()
        changed = False
        if message_type == "game_event" and isinstance(data, GameEvent):
            changed = self._observe_event(data, now)
        elif message_type == "reaction_generation_status" and isinstance(data, ReactionGenerationStatus):
            changed = self._observe_reaction_status(data, now)
        elif message_type == "siena_reaction" and isinstance(data, SienaReaction):
            changed = self._observe_reaction(data, now)
        elif message_type == "voice_generation_status" and isinstance(data, VoiceGenerationStatus):
            changed = self._observe_voice(data, now)
        elif message_type == "scene_context_updated" and isinstance(data, SceneContext):
            changed = self._observe_scene(data, now)
        elif message_type == "bridge_status" and isinstance(data, BridgeStatus):
            changed = self._observe_bridge(data)
        if changed:
            status = self.status()
            self._last_status_signature = self._status_signature(status)
            return [("in_game_presence_status", status)]
        return []

    def _observe_event(self, event: GameEvent, now: datetime) -> bool:
        if event.event_type == EventType.SESSION_STARTED:
            if event.session_id == self._active_session:
                return False
            self._active_session = event.session_id
            self._ended_sessions.discard(event.session_id)
            self._pending = None
            self._generating_due = None
            return self._hide(now)
        if event.event_type == EventType.SESSION_ENDED and event.session_id == self._active_session:
            self._ended_sessions.add(event.session_id)
            self._active_session = None
            self._pending = None
            self._generating_due = None
            if self._snapshot.state not in {
                InGamePresenceState.REACTION,
                InGamePresenceState.FALLBACK,
                InGamePresenceState.VOICE_SYNTHESIZING,
                InGamePresenceState.VOICE_PLAYING,
            }:
                return self._hide(now)
        return False

    def _observe_reaction_status(self, status: ReactionGenerationStatus, now: datetime) -> bool:
        if status.session_id != self._active_session or status.session_id in self._ended_sessions:
            return False
        if status.state == "queued":
            self._pending = status
            self._generating_due = None
            return False
        if status.state == "generating":
            self._pending = status
            self._generating_due = now + timedelta(milliseconds=self.policy.generating_delay_ms)
            return False
        if status.state in {"suppressed", "failed"}:
            if self._pending and self._pending.request_id == status.request_id:
                self._pending = None
                self._generating_due = None
                if self._snapshot.state == InGamePresenceState.GENERATING:
                    return self._hide(now)
        return False

    def _priority_for_pending(self, status: ReactionGenerationStatus) -> ReactionPriority:
        if status.focus == ReactionFocus.DANGER_WARNING:
            return ReactionPriority.CRITICAL
        if status.focus in {ReactionFocus.COMBAT_COMMENT, ReactionFocus.RECOVERY_COMMENT}:
            return ReactionPriority.HIGH
        return ReactionPriority.MEDIUM

    def tick(self) -> bool:
        if not self.enabled:
            return False
        now = self.clock()
        changed = False
        if self._snapshot.active and self._snapshot.expires_at and self._snapshot.expires_at <= now:
            changed = self._hide(now)
        elif self._pending and self._generating_due and now >= self._generating_due:
            priority = self._priority_for_pending(self._pending)
            if not self.policy.may_replace(self._snapshot, priority, now):
                self._generating_due = None
            else:
                snapshot = InGamePresenceSnapshot(
                    active=True,
                    revision=self._revision,
                    state=InGamePresenceState.GENERATING,
                    session_id=self._pending.session_id,
                    scene_id=self._pending.scene_id,
                    priority=priority,
                    phase=self._pending.scene_phase,
                    focus=self._pending.focus,
                    text="Сиена формулирует реакцию…",
                    created_at=now,
                    updated_at=now,
                    display_duration_ms=self.policy.normal_duration_ms,
                    expires_at=now + timedelta(milliseconds=self.policy.normal_duration_ms),
                )
                self._generating_due = None
                changed = self._set(snapshot)
        status = self.status(now)
        signature = self._status_signature(status)
        status_changed = self._last_status_signature is not None and signature != self._last_status_signature
        if changed or status_changed:
            self._last_status_signature = signature
        return changed or status_changed

    def _observe_reaction(self, reaction: SienaReaction, now: datetime) -> bool:
        if not reaction.session_id or reaction.session_id != self._active_session or reaction.session_id in self._ended_sessions:
            return False
        text = self.policy.sanitize_text(reaction.text)
        replaces_own_generation = (
            self._snapshot.state == InGamePresenceState.GENERATING
            and self._snapshot.session_id == reaction.session_id
            and (not reaction.scene_id or self._snapshot.scene_id == reaction.scene_id)
        )
        if not text or (not replaces_own_generation and not self.policy.may_replace(self._snapshot, reaction.priority, now)):
            return False
        duration = self.policy.duration_for(reaction.priority, reaction.fallback_used)
        self._pending = None
        self._generating_due = None
        snapshot = InGamePresenceSnapshot(
            active=True,
            revision=self._revision,
            state=InGamePresenceState.FALLBACK if reaction.fallback_used else InGamePresenceState.REACTION,
            session_id=reaction.session_id,
            reaction_id=reaction.reaction_id,
            scene_id=reaction.scene_id,
            priority=reaction.priority,
            phase=reaction.scene_phase,
            focus=reaction.focus,
            text=text,
            provider=reaction.provider,
            fallback_used=reaction.fallback_used,
            created_at=now,
            updated_at=now,
            display_duration_ms=duration,
            expires_at=now + timedelta(milliseconds=duration),
            metadata=InGamePresenceMetadata(fallback_label="Резервная реакция" if reaction.fallback_used else None),
        )
        return self._set(snapshot)

    def _observe_voice(self, voice: VoiceGenerationStatus, now: datetime) -> bool:
        current = self._snapshot
        if (
            not current.active
            or voice.session_id in self._ended_sessions
            or voice.session_id != current.session_id
            or voice.reaction_id != current.reaction_id
        ):
            return False
        state = voice.state
        candidate = current.model_copy(deep=True)
        candidate.updated_at = now
        if state == VoiceState.SYNTHESIZING:
            candidate.state = InGamePresenceState.VOICE_SYNTHESIZING
            candidate.voice_state = "synthesizing"
            candidate.metadata.voice_label = "Готовится голос…"
        elif state == VoiceState.PLAYING:
            candidate.state = InGamePresenceState.VOICE_PLAYING
            candidate.voice_state = "playing"
            candidate.metadata.voice_label = "Сиена говорит"
        elif state == VoiceState.READY:
            candidate.voice_state = "ready"
            candidate.metadata.voice_label = None
            candidate.state = InGamePresenceState.FALLBACK if candidate.fallback_used else InGamePresenceState.REACTION
        elif state in {VoiceState.COMPLETED, VoiceState.CANCELLED}:
            candidate.voice_state = "completed"
            candidate.metadata.voice_label = None
            candidate.state = InGamePresenceState.FALLBACK if candidate.fallback_used else InGamePresenceState.REACTION
        elif state in {VoiceState.FAILED, VoiceState.SUPPRESSED}:
            candidate.voice_state = "failed"
            candidate.metadata.voice_label = None
            candidate.state = InGamePresenceState.FALLBACK if candidate.fallback_used else InGamePresenceState.REACTION
        else:
            return False
        return self._set(candidate)

    def _observe_scene(self, scene: SceneContext, now: datetime) -> bool:
        if scene.session_id != self._active_session:
            return False
        if self._pending and self._pending.scene_id and self._pending.scene_id != scene.scene_id:
            self._pending = None
            self._generating_due = None
            if self._snapshot.state == InGamePresenceState.GENERATING:
                return self._hide(now)
        return False

    def _observe_bridge(self, status: BridgeStatus) -> bool:
        values = status.capabilities
        before = (self.overlay_supported, self.overlay_enabled, self.overlay_version, self.font_cyrillic_ready)
        self.overlay_supported = values.presence_overlay_supported
        self.overlay_enabled = values.presence_overlay_enabled
        self.overlay_version = values.presence_overlay_version
        self.font_cyrillic_ready = values.presence_font_cyrillic_ready
        return before != (self.overlay_supported, self.overlay_enabled, self.overlay_version, self.font_cyrillic_ready)

    def record_poll(self, after_revision: int | None) -> tuple[InGamePresenceSnapshot | None, InGamePresenceStatus | None]:
        self.tick()
        now = self.clock()
        was_connected = self.consumer_connected(now)
        self.last_poll_at = now
        self.poll_count += 1
        changed = after_revision is None or self._snapshot.revision > after_revision
        if changed:
            self.last_revision_sent = self._snapshot.revision
        else:
            self.unchanged_count += 1
        status = self.status(now)
        signature_changed = self._status_signature(status) != self._last_status_signature or not was_connected
        self._last_status_signature = self._status_signature(status)
        return (self.current() if changed else None), (status if signature_changed else None)

    def consumer_connected(self, now: datetime | None = None) -> bool:
        current = now or self.clock()
        timeout_ms = max(2_000, self.poll_interval_ms * 4)
        return bool(self.enabled and self.last_poll_at and current - self.last_poll_at <= timedelta(milliseconds=timeout_ms))

    def status(self, now: datetime | None = None) -> InGamePresenceStatus:
        current = now or self.clock()
        return InGamePresenceStatus(
            enabled=self.enabled,
            active=self._snapshot.active,
            revision=self._snapshot.revision,
            state=self._snapshot.state,
            consumer_connected=self.consumer_connected(current),
            last_poll_at=self.last_poll_at,
            last_revision_sent=self.last_revision_sent,
            poll_count=self.poll_count,
            unchanged_count=self.unchanged_count,
            last_error=self.last_error,
            current_text_preview=self._snapshot.text[:160],
            overlay_supported=self.overlay_supported,
            overlay_enabled=self.overlay_enabled,
            overlay_version=self.overlay_version,
            font_cyrillic_ready=self.font_cyrillic_ready,
            poll_interval_ms=self.poll_interval_ms,
        )

    @staticmethod
    def _status_signature(status: InGamePresenceStatus) -> tuple:
        return (
            status.enabled,
            status.active,
            status.revision,
            status.state,
            status.consumer_connected,
            status.last_revision_sent,
            status.last_error,
            status.overlay_supported,
            status.overlay_enabled,
            status.overlay_version,
            status.font_cyrillic_ready,
        )
