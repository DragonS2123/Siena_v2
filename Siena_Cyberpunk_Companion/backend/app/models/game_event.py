from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.priorities import EventPriority


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    PLAYER_DAMAGED = "player_damaged"
    HEALTH_LOW = "health_low"
    HEALTH_CRITICAL = "health_critical"
    PLAYER_HEALED = "player_healed"
    COMBAT_STARTED = "combat_started"
    COMBAT_ENDED = "combat_ended"
    VEHICLE_ENTERED = "vehicle_entered"
    VEHICLE_EXITED = "vehicle_exited"
    PLAYER_IDLE = "player_idle"
    PLAYER_MOVED_AFTER_IDLE = "player_moved_after_idle"
    GAME_STARTED = "game_started"
    GAME_STOPPED = "game_stopped"
    GAME_LOADED = "game_loaded"
    GAME_PAUSED = "game_paused"
    GAME_RESUMED = "game_resumed"
    PLAYER_HEALTH_BELOW_50 = "player_health_below_50"
    PLAYER_HEALTH_CRITICAL = "player_health_critical"
    PLAYER_RECOVERED = "player_recovered"
    PLAYER_ENTERED_VEHICLE = "player_entered_vehicle"
    PLAYER_EXITED_VEHICLE = "player_exited_vehicle"
    ENEMY_DETECTED = "enemy_detected"
    ENEMY_COUNT_CHANGED = "enemy_count_changed"
    DISTRICT_CHANGED = "district_changed"
    COMPANION_APPEARED = "companion_appeared"
    COMPANION_DISAPPEARED = "companion_disappeared"
    COMPANION_TOO_FAR = "companion_too_far"
    COMPANION_REGROUPED = "companion_regrouped"
    COMPANION_STUCK = "companion_stuck"
    COMPANION_HEALTH_CRITICAL = "companion_health_critical"
    COMPANION_INTENT_CHANGED = "companion_intent_changed"


class EventSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_FOR_PRIORITY = {
    EventPriority.P0_CRITICAL: EventSeverity.CRITICAL,
    EventPriority.P1_HIGH: EventSeverity.HIGH,
    EventPriority.P2_MEDIUM: EventSeverity.MEDIUM,
    EventPriority.P3_LOW: EventSeverity.LOW,
}


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str = Field(min_length=1, max_length=128)
    # Unknown v0.1/v0.2 event names remain valid for wire compatibility.
    event_type: EventType | str
    priority: EventPriority
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: EventSeverity | None = None
    source: str = Field(default="observer", min_length=1, max_length=64)
    sequence: int | None = Field(default=None, ge=0)
    summary: str = Field(default="Meaningful game state change", min_length=1, max_length=256)
    deduplication_key: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.severity is None:
            self.severity = SEVERITY_FOR_PRIORITY[self.priority]

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(timezone.utc)

    @property
    def occurred_at(self) -> datetime:
        return self.created_at

    @property
    def data(self) -> dict[str, Any]:
        return self.payload
