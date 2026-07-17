from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.reaction import ReactionPriority
from app.models.scene import ReactionFocus, ScenePhase


class InGamePresenceState(StrEnum):
    HIDDEN = "hidden"
    QUEUED = "queued"
    GENERATING = "generating"
    REACTION = "reaction"
    FALLBACK = "fallback"
    VOICE_SYNTHESIZING = "voice_synthesizing"
    VOICE_PLAYING = "voice_playing"
    UNAVAILABLE = "unavailable"


class InGamePresenceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallback_label: Literal["Резервная реакция"] | None = None
    voice_label: Literal["Готовится голос…", "Сиена говорит"] | None = None


class InGamePresenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    revision: int = Field(ge=0)
    state: InGamePresenceState
    session_id: str | None = Field(default=None, max_length=128)
    reaction_id: str | None = Field(default=None, max_length=128)
    scene_id: str | None = Field(default=None, max_length=128)
    priority: ReactionPriority | None = None
    phase: ScenePhase | None = None
    focus: ReactionFocus | None = None
    text: str = Field(default="", max_length=320)
    provider: str | None = Field(default=None, max_length=64)
    fallback_used: bool = False
    voice_state: Literal["idle", "synthesizing", "ready", "playing", "completed", "failed"] = "idle"
    created_at: datetime
    updated_at: datetime
    display_duration_ms: int = Field(ge=0, le=120_000)
    expires_at: datetime | None = None
    metadata: InGamePresenceMetadata = Field(default_factory=InGamePresenceMetadata)

    @field_validator("created_at", "updated_at", "expires_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("presence timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class InGamePresenceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    active: bool
    revision: int = Field(ge=0)
    state: InGamePresenceState
    consumer_connected: bool
    last_poll_at: datetime | None = None
    last_revision_sent: int | None = Field(default=None, ge=0)
    poll_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    last_error: str | None = Field(default=None, max_length=256)
    current_text_preview: str = Field(default="", max_length=160)
    overlay_supported: bool | None = None
    overlay_enabled: bool | None = None
    overlay_version: str | None = Field(default=None, max_length=32)
    font_cyrillic_ready: bool | None = None
    poll_interval_ms: int = Field(ge=100, le=60_000)

