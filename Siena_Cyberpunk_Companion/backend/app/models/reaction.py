from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.game_event import EventType
from app.models.scene import ReactionFocus, SceneContext, ScenePhase


class ReactionPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReactionGenerationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    event_type: EventType
    state: Literal["queued", "generating", "completed", "fallback", "suppressed", "failed"]
    scene_id: str | None = None
    scene_phase: ScenePhase | None = None
    focus: ReactionFocus | None = None
    reason: str | None = None


class SienaReaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reaction_id: str = Field(default_factory=lambda: str(uuid4()))
    event_id: str
    session_id: str | None = Field(default=None, max_length=128)
    event_type: EventType
    text: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priority: ReactionPriority
    provider: str = Field(min_length=1, max_length=64)
    requested_provider: str | None = Field(default=None, max_length=64)
    fallback_used: bool = False
    fallback_reason: str | None = Field(default=None, max_length=256)
    latency_ms: float | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    scene_id: str | None = Field(default=None, max_length=128)
    scene_revision: int | None = Field(default=None, ge=1)
    scene_phase: ScenePhase | None = None
    focus: ReactionFocus | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(timezone.utc)


class PlannerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    provider: str
    queued_events: int = Field(ge=0)
    event_count: int = Field(ge=0)
    reaction_count: int = Field(ge=0)
    last_event_at: datetime | None
    last_reaction_at: datetime | None
    cooldown_remaining_seconds: float = Field(ge=0)


class SienaCoreMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["cyberpunk_companion"] = "cyberpunk_companion"
    channel: Literal["game_observer"] = "game_observer"
    mode: Literal["read_only_reaction"] = "read_only_reaction"
    game: Literal["cyberpunk_2077"] = "cyberpunk_2077"
    game_session_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)


class SienaCoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=1, max_length=128)
    language: Literal["ru", "en"] = "ru"
    metadata: SienaCoreMetadata
    stateless: Literal[True] = True
    tools_enabled: Literal[False] = False
    memory_write_enabled: Literal[False] = False
    tts_enabled: Literal[False] = False
    attachments_enabled: Literal[False] = False


class SienaCoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    reasoning: str | None = None
    model: str | None = None
    request_id: str
    metadata: SienaCoreMetadata | None = None


class CircuitBreakerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["closed", "open", "half_open"]
    consecutive_failures: int = Field(ge=0)
    opened_at: datetime | None = None


class ReactionProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    configured_provider: str
    active_provider: str
    fallback_provider: str
    configuration_error: str | None = None
    siena_core_reachable: bool | None = None
    circuit_state: Literal["closed", "open", "half_open"]
    consecutive_failures: int = Field(ge=0)
    queue_size: int = Field(ge=0)
    queue_capacity: int = Field(ge=1)
    worker_running: bool
    last_request_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    average_latency_ms: float | None = Field(default=None, ge=0)
    fallback_count: int = Field(ge=0)
    stale_suppressed_count: int = Field(ge=0)


class ReactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    request_id: str
    event: Any
    recent_events: list[Any] = Field(default_factory=list)
    recent_reactions: list[Any] = Field(default_factory=list)
    scene_id: str | None = None
    scene_revision: int | None = Field(default=None, ge=1)
    scene_phase: ScenePhase | None = None
    focus: ReactionFocus | None = None
    scene_snapshot: SceneContext | None = None
    opportunity_expires_at: datetime | None = None
    related_event_types: list[str] = Field(default_factory=list, max_length=8)
    tactical_context: dict[str, Any] | None = None
    delivery_hint: Literal["text_only", "voice_and_text"] | None = None
    reaction_category: str | None = Field(default=None, max_length=64)
    language: Literal["ru", "en"] = "ru"
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
