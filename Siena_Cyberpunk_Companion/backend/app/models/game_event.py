from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.domain.priorities import EventPriority


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    priority: EventPriority
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="observer", min_length=1, max_length=64)
    deduplication_key: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
