from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NpcCommandName(StrEnum):
    SPAWN = "spawn"
    DESPAWN = "despawn"
    FOLLOW = "follow"
    STAY = "stay"
    COME_HERE = "come_here"
    LOOK_AT_PLAYER = "look_at_player"
    CLEAR_LOOK_AT = "clear_look_at"
    STATUS = "status"
    CONFIGURE = "configure"
    SUSPEND = "suspend"
    RESUME = "resume"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class NpcCommandOrigin(StrEnum):
    PLAYER = "player"
    SYSTEM = "system"
    SPEECH = "speech"


class PlayerNpcCommandName(StrEnum):
    SPAWN = "spawn"
    DESPAWN = "despawn"
    FOLLOW = "follow"
    STAY = "stay"
    COME_HERE = "come_here"
    LOOK_AT_PLAYER = "look_at_player"
    CLEAR_LOOK_AT = "clear_look_at"
    STATUS = "status"


class NpcCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: PlayerNpcCommandName


class NpcCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str
    command: NpcCommandName
    created_at: datetime
    expires_at: datetime
    expires_at_epoch_ms: int = Field(ge=0)
    origin: NpcCommandOrigin = NpcCommandOrigin.PLAYER
    payload: dict[str, Any] | None = None


class NpcStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    backend_connected: bool | None = None
    player_available: bool | None = None
    dynamic_entity_system_ready: bool | None = None
    entity_exists: bool | None = None
    managed: bool | None = None
    spawned: bool | None = None
    resolved: bool | None = None
    runtime_class: str | None = Field(default=None, max_length=64)
    entity_record: str = "Siena.SienaCompanion"
    appearance: str = "siena_default"
    current_appearance: str | None = Field(default=None, max_length=128)
    temporary_appearance: bool = False
    temporary_body_record: str = "Siena.SienaCompanion"
    lifecycle_state: str | None = Field(default=None, max_length=32)
    requested_mode: str | None = Field(default=None, max_length=32)
    effective_mode: str | None = Field(default=None, max_length=32)
    mode: str | None = Field(default=None, max_length=32)
    movement_active: bool | None = None
    look_at_active: bool | None = None
    last_command: NpcCommandName | None = None
    last_command_result: str | None = Field(default=None, max_length=64)
    last_command_completed_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=240)
    cleanup_reason: str | None = Field(default=None, max_length=64)
    stuck: bool | None = None
    rescue_state: str | None = Field(default=None, max_length=32)
    conversation_state: str | None = Field(default=None, max_length=32)
    active_utterance_id: str | None = Field(default=None, max_length=64)
    subtitle_visible: bool | None = None
    suspended_reason: str | None = Field(default=None, max_length=32)


class NpcCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: str = Field(pattern="^(success|unavailable|error|rejected|expired)$")
    error: str | None = Field(default=None, max_length=240)
    status: NpcStatus | None = None


class NpcCommandAck(BaseModel):
    command_id: str
    acknowledged: bool
