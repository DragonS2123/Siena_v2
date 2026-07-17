from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EmbodimentState(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    ABSENT = "absent"
    SPAWNING = "spawning"
    IDLE = "idle"
    FOLLOWING = "following"
    STAYING = "staying"
    RETURNING = "returning"
    CONVERSING = "conversing"
    SUSPENDED = "suspended"
    CLEANING_UP = "cleaning_up"
    ERROR = "error"


class EmbodimentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    npc_presence_enabled: bool = False
    npc_auto_spawn: bool = False
    npc_auto_follow: bool = True
    npc_spawn_delay_seconds: float = Field(default=3.0, ge=1.0, le=30.0)
    npc_follow_distance: float = Field(default=2.5, ge=1.5, le=8.0)
    npc_return_distance: float = Field(default=12.0, ge=5.0, le=40.0)
    npc_rescue_distance: float = Field(default=40.0, ge=15.0, le=100.0)
    npc_rescue_enabled: bool = False
    npc_suspend_during_combat: bool = True
    npc_suspend_in_vehicle: bool = True
    npc_in_game_subtitles_enabled: bool = True
    npc_voice_embodiment_enabled: bool = True


class EmbodiedTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous: EmbodimentState
    current: EmbodimentState
    reason: str = Field(max_length=80)
