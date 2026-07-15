from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.priorities import EventPriority
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventSeverity, GameEvent


class ScenePhase(StrEnum):
    SESSION_START = "session_start"
    EXPLORATION = "exploration"
    COMBAT = "combat"
    DANGER = "danger"
    RECOVERY = "recovery"
    VEHICLE = "vehicle"
    IDLE = "idle"
    TRANSITION = "transition"
    SESSION_END = "session_end"
    UNKNOWN = "unknown"


class HealthTrend(StrEnum):
    STABLE = "stable"
    FALLING = "falling"
    RAPIDLY_FALLING = "rapidly_falling"
    RECOVERING = "recovering"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class ReactionFocus(StrEnum):
    SESSION_GREETING = "session_greeting"
    DANGER_WARNING = "danger_warning"
    COMBAT_COMMENT = "combat_comment"
    RECOVERY_COMMENT = "recovery_comment"
    EXPLORATION_COMMENT = "exploration_comment"
    VEHICLE_COMMENT = "vehicle_comment"
    IDLE_COMMENT = "idle_comment"
    SCENE_RESOLUTION = "scene_resolution"


class SceneEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    semantic_type: str
    created_at: datetime
    severity: EventSeverity
    summary: str
    sequence: int | None = None


class NormalizedSceneEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: GameEvent
    semantic_type: str
    legacy_alias: bool = False
    diagnostic_only: bool = False
    duplicate_semantic_event: bool = False


class SceneContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    source: str
    phase: ScenePhase
    previous_phase: ScenePhase | None = None
    started_at: datetime
    updated_at: datetime
    last_meaningful_event_at: datetime
    severity: EventSeverity = EventSeverity.INFO
    peak_severity: EventSeverity = EventSeverity.INFO
    event_count: int = Field(default=0, ge=0)
    event_ids: list[str] = Field(default_factory=list)
    recent_events: list[SceneEventSummary] = Field(default_factory=list)
    primary_event_type: str | None = None
    health_current: float | None = None
    health_max: float | None = None
    health_percent: float | None = None
    health_trend: HealthTrend = HealthTrend.UNKNOWN
    total_damage: float = Field(default=0, ge=0)
    total_healing: float = Field(default=0, ge=0)
    damage_hits: int = Field(default=0, ge=0)
    combat_state: bool | None = None
    vehicle_state: bool | None = None
    idle_seconds: float | None = Field(default=None, ge=0)
    capabilities: BridgeCapabilities = Field(default_factory=BridgeCapabilities)
    notable_facts: list[str] = Field(default_factory=list)
    summary: str
    revision: int = Field(default=1, ge=1)
    reaction_count: int = Field(default=0, ge=0)
    closed_at: datetime | None = None

    @field_validator("started_at", "updated_at", "last_meaningful_event_at", "closed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("scene timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class SceneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: SceneContext | None
    normalized_event: NormalizedSceneEvent
    significant: bool = False
    started: bool = False
    closed: bool = False


class ReactionOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    opportunity_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    scene_id: str
    scene_revision: int = Field(ge=1)
    trigger_event_id: str
    trigger_event_type: str
    focus: ReactionFocus
    priority: EventPriority
    reason: str
    created_at: datetime
    expires_at: datetime
    scene_snapshot: SceneContext
    trigger_event: GameEvent
    replaces_request_id: str | None = None
    suppresses_event_ids: list[str] = Field(default_factory=list)

    @field_validator("created_at", "expires_at")
    @classmethod
    def opportunity_timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("opportunity timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class SceneCurrentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    scene: SceneContext | None = None


class SceneSuppression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
