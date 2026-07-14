from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(StrictModel):
    x: float
    y: float
    z: float


class GameStatus(StrictModel):
    running: bool
    loaded: bool
    paused: bool

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "GameStatus":
        if self.loaded and not self.running:
            raise ValueError("loaded game must be running")
        if self.paused and not self.loaded:
            raise ValueError("paused game must be loaded")
        return self


class PlayerState(StrictModel):
    health: float = Field(ge=0)
    max_health: float = Field(gt=0)
    in_combat: bool
    in_vehicle: bool
    position: Position

    @model_validator(mode="after")
    def health_not_above_max(self) -> "PlayerState":
        if self.health > self.max_health:
            raise ValueError("health cannot exceed max_health")
        return self


class CompanionState(StrictModel):
    present: bool
    health: float = Field(ge=0)
    max_health: float = Field(gt=0)
    distance_to_player: float = Field(ge=0)
    current_intent: str = Field(min_length=1, max_length=64)
    moving: bool

    @model_validator(mode="after")
    def health_not_above_max(self) -> "CompanionState":
        if self.health > self.max_health:
            raise ValueError("health cannot exceed max_health")
        return self


class EnvironmentState(StrictModel):
    district: str = Field(min_length=1, max_length=128)
    visible_hostiles: int = Field(ge=0)
    highest_threat_id: str | None = Field(default=None, max_length=128)


class GameState(StrictModel):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$")
    session_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    captured_at: datetime
    source: Literal["simulator", "cet"] = "simulator"
    bridge_version: str | None = Field(default=None, max_length=32)
    game: GameStatus
    player: PlayerState
    companion: CompanionState
    environment: EnvironmentState
