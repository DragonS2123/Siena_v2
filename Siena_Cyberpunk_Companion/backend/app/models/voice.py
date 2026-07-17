from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.scene import ReactionFocus, SceneContext


class VoiceState(StrEnum):
    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    PLAYING = "playing"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VoicePriority(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_request_id: str = Field(default_factory=lambda: str(uuid4()))
    reaction_id: str
    event_id: str
    session_id: str
    scene_id: str | None = None
    scene_revision: int | None = Field(default=None, ge=1)
    focus: ReactionFocus | None = None
    priority: VoicePriority
    text: str = Field(min_length=1, max_length=500)
    provider: str
    speaker: str | None = None
    language: Literal["ru", "en"] = "ru"
    created_at: datetime
    expires_at: datetime
    requested_format: Literal["wav"] = "wav"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "expires_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("voice timestamps must include timezone")
        return value.astimezone(timezone.utc)


class VoiceOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: VoiceRequest
    reason: str
    scene_snapshot: SceneContext | None = None


class VoiceClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_clip_id: str = Field(default_factory=lambda: str(uuid4()))
    voice_request_id: str
    reaction_id: str
    session_id: str
    scene_id: str | None = None
    scene_revision: int | None = Field(default=None, ge=1)
    focus: ReactionFocus | None = None
    priority: VoicePriority
    state: VoiceState
    audio_format: Literal["wav"] = "wav"
    content_type: Literal["audio/wav"] = "audio/wav"
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0, le=8)
    duration_ms: int = Field(ge=0)
    byte_length: int = Field(gt=0)
    created_at: datetime
    expires_at: datetime
    tts_provider: str
    speaker: str | None = None
    language: Literal["ru", "en"] = "ru"
    latency_ms: float | None = Field(default=None, ge=0)
    error_category: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceGenerationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_request_id: str
    reaction_id: str
    session_id: str
    scene_id: str | None = None
    state: VoiceState
    priority: VoicePriority
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VoiceClipReady(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_clip_id: str
    voice_request_id: str
    reaction_id: str
    session_id: str
    scene_id: str | None = None
    scene_revision: int | None = None
    focus: ReactionFocus | None = None
    priority: VoicePriority
    audio_url: str
    content_type: str
    duration_ms: int
    tts_provider: str
    speaker: str | None = None
    expires_at: datetime


class VoicePlaybackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_clip_id: str
    event: Literal["playback_started", "playback_completed", "playback_failed", "playback_cancelled"]
    tab_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=256)


class VoiceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    muted: bool
    configured: bool
    tts_base_url: Literal["configured", "not_configured"]
    tts_reachable: bool | None
    tts_provider: str | None
    speaker: str | None
    language: str
    interrupt_mode: Literal["never", "critical_only"]
    post_play_gap_ms: int = Field(ge=0)
    circuit_state: Literal["closed", "open", "half_open"]
    consecutive_failures: int = Field(ge=0)
    worker_running: bool
    queue_size: int = Field(ge=0)
    queue_capacity: int = Field(ge=1)
    clip_count: int = Field(ge=0)
    currently_synthesizing: str | None
    last_request_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    average_latency_ms: float | None = Field(default=None, ge=0)
    suppressed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
