from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.game_event import EventType


class ReactionPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SienaReaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reaction_id: str = Field(default_factory=lambda: str(uuid4()))
    event_id: str
    event_type: EventType
    text: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priority: ReactionPriority
    provider: str = Field(min_length=1, max_length=64)

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
