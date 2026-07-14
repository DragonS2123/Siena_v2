from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.priorities import EventPriority


class CommandIntent(StrEnum):
    CONTINUE = "continue"
    FOLLOW = "follow"
    HOLD = "hold"
    REGROUP = "regroup"
    PROTECT = "protect"
    RETREAT = "retreat"
    STOP = "stop"


class CompanionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(default_factory=lambda: str(uuid4()))
    intent: CommandIntent
    priority: EventPriority
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_for_seconds: float = Field(default=10.0, gt=0, le=3600)
    target_id: str | None = Field(default=None, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="manual", min_length=1, max_length=64)

    def is_valid(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return (current - self.created_at).total_seconds() < self.valid_for_seconds
