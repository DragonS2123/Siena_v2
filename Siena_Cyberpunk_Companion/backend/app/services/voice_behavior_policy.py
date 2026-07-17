import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Callable

from app.models.reaction import ReactionPriority, SienaReaction
from app.models.scene import ReactionFocus, SceneContext
from app.models.voice import VoiceOpportunity, VoicePriority, VoiceRequest
from app.services.speech_text_normalizer import SpeechTextError, SpeechTextNormalizer

logger = logging.getLogger("siena_observer.voice_policy")

PRIORITY_MAP = {
    ReactionPriority.LOW: VoicePriority.LOW,
    ReactionPriority.MEDIUM: VoicePriority.MEDIUM,
    ReactionPriority.HIGH: VoicePriority.HIGH,
    ReactionPriority.CRITICAL: VoicePriority.CRITICAL,
}


class VoiceBehaviorPolicy:
    def __init__(
        self,
        *,
        enabled: bool,
        muted: bool,
        min_interval_seconds: float,
        same_text_cooldown_seconds: float,
        scene_max_clips: int,
        critical_bypass: bool,
        max_text_chars: int,
        max_event_age_seconds: float,
        audio_ttl_seconds: float,
        language: str,
        speaker: str,
        require_tts_ready: bool,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.enabled = enabled
        self.muted = muted
        self.min_interval = timedelta(seconds=min_interval_seconds)
        self.same_text_cooldown = timedelta(seconds=same_text_cooldown_seconds)
        self.scene_max_clips = scene_max_clips
        self.critical_bypass = critical_bypass
        self.max_event_age = timedelta(seconds=max_event_age_seconds)
        self.audio_ttl = timedelta(seconds=audio_ttl_seconds)
        self.language = language
        self.speaker = speaker or None
        self.require_tts_ready = require_tts_ready
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.normalizer = SpeechTextNormalizer(max_text_chars)
        self._seen_reactions: set[str] = set()
        self._scene_counts: dict[str, int] = {}
        self._last_at: datetime | None = None
        self._last_critical_at: datetime | None = None
        self._recent_texts: deque[tuple[str, datetime]] = deque(maxlen=20)
        self.last_suppression_reason: str | None = None
        self.suppressed_count = 0

    def reset_session(self) -> None:
        self._seen_reactions.clear()
        self._scene_counts.clear()
        self._last_at = None
        self._last_critical_at = None
        self._recent_texts.clear()

    def evaluate(
        self,
        reaction: SienaReaction,
        scene: SceneContext | None,
        active_session: str | None,
        tts_ready: bool | None,
    ) -> VoiceOpportunity | None:
        self.last_suppression_reason = None
        now = self.clock()
        if not self.enabled:
            return self._suppress(reaction, "voice_disabled")
        if self.muted:
            return self._suppress(reaction, "muted")
        if not reaction.session_id or reaction.session_id != active_session:
            return self._suppress(reaction, "old_session")
        if reaction.metadata.get("delivery_hint") == "text_only":
            return self._suppress(reaction, "context_text_only")
        if now - reaction.created_at > self.max_event_age:
            return self._suppress(reaction, "stale_reaction")
        if reaction.reaction_id in self._seen_reactions:
            return self._suppress(reaction, "duplicate_reaction")
        if self.require_tts_ready and tts_ready is not True:
            return self._suppress(reaction, "tts_unavailable")
        if self.language not in {"ru", "en"}:
            return self._suppress(reaction, "unsupported_language")
        try:
            text = self.normalizer.normalize(reaction.text)
        except SpeechTextError:
            return self._suppress(reaction, "stale_reaction")

        priority = PRIORITY_MAP[reaction.priority]
        critical = priority == VoicePriority.CRITICAL or reaction.focus == ReactionFocus.DANGER_WARNING
        focus = reaction.focus
        eligible = focus in {
            ReactionFocus.SESSION_GREETING,
            ReactionFocus.DANGER_WARNING,
            ReactionFocus.RECOVERY_COMMENT,
            ReactionFocus.SCENE_RESOLUTION,
        }
        if focus == ReactionFocus.COMBAT_COMMENT and priority in {VoicePriority.HIGH, VoicePriority.CRITICAL}:
            eligible = True
        if focus == ReactionFocus.RAM_WARNING and priority in {VoicePriority.HIGH, VoicePriority.CRITICAL}:
            eligible = True
        if str(reaction.event_type) in {"health_critical", "player_health_critical"}:
            eligible = True
            critical = True
            priority = VoicePriority.CRITICAL
        if not eligible:
            return self._suppress(reaction, "player_not_ready")

        for previous, spoken_at in self._recent_texts:
            if now - spoken_at <= self.same_text_cooldown and self._similar(previous, text):
                return self._suppress(reaction, "duplicate_text")
        if self._last_at and now - self._last_at < self.min_interval and not (critical and self.critical_bypass):
            if self._last_critical_at and now - self._last_critical_at < self.min_interval:
                return self._suppress(reaction, "weak_after_critical")
            return self._suppress(reaction, "voice_budget_exhausted")

        scene_id = reaction.scene_id or (scene.scene_id if scene else None)
        scene_key = scene_id or reaction.session_id
        count = self._scene_counts.get(scene_key, 0)
        if count >= self.scene_max_clips and not (critical and self.critical_bypass):
            return self._suppress(reaction, "voice_budget_exhausted")

        request = VoiceRequest(
            reaction_id=reaction.reaction_id,
            event_id=reaction.event_id,
            session_id=reaction.session_id,
            scene_id=scene_id,
            scene_revision=reaction.scene_revision or (scene.revision if scene else None),
            focus=focus,
            priority=priority,
            text=text,
            provider=reaction.provider,
            speaker=self.speaker,
            language=self.language,
            created_at=now,
            expires_at=now + self.audio_ttl,
            metadata={
                "reaction_provider": reaction.provider,
                "event_id": reaction.event_id,
                "fallback_used": reaction.fallback_used,
                "text_preview": text[:100],
            },
        )
        self._seen_reactions.add(reaction.reaction_id)
        self._scene_counts[scene_key] = count + 1
        self._last_at = now
        if critical:
            self._last_critical_at = now
        self._recent_texts.append((text, now))
        logger.info(
            "voice_opportunity_created voice_request_id=%s reaction_id=%s priority=%s",
            request.voice_request_id, reaction.reaction_id, priority,
        )
        return VoiceOpportunity(request=request, reason="published_reaction", scene_snapshot=scene)

    @staticmethod
    def _similar(left: str, right: str) -> bool:
        a = left.casefold().strip()
        b = right.casefold().strip()
        return a == b or SequenceMatcher(None, a, b).ratio() >= 0.92

    def _suppress(self, reaction: SienaReaction, reason: str) -> None:
        self.last_suppression_reason = reason
        self.suppressed_count += 1
        logger.info("voice_opportunity_suppressed reaction_id=%s reason=%s", reaction.reaction_id, reason)
        return None
