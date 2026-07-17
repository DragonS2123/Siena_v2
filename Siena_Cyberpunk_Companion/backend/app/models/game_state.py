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


class DeepGameStateCapabilities(StrictModel):
    player: bool | None = None
    stats: bool | None = None
    stat_pools: bool | None = None
    weapon: bool | None = None
    status_effects: bool | None = None
    cyberdeck_identity: bool | None = None
    cyberdeck_metadata: bool | None = None
    cyberdeck_programs: bool | None = None
    cyberdeck_capacity: bool | None = None


class DeepPlayerState(StrictModel):
    entity_available: bool | None = None
    session_available: bool | None = None
    is_pre_game: bool | None = None
    position: Position | None = None
    mounted_vehicle: bool | None = None


class DeepStatsState(StrictModel):
    level: float | None = None
    street_cred: float | None = None
    armor: float | None = None
    power_level: float | None = None
    health: float | None = None
    memory: float | None = None


class DeepStatPoolsState(StrictModel):
    current_health: float | None = Field(default=None, ge=0)
    maximum_health: float | None = Field(default=None, gt=0)
    current_memory: float | None = Field(default=None, ge=0)
    maximum_memory: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def current_values_not_above_maximum(self) -> "DeepStatPoolsState":
        if self.current_health is not None and self.maximum_health is not None and self.current_health > self.maximum_health:
            raise ValueError("current_health cannot exceed maximum_health")
        if self.current_memory is not None and self.maximum_memory is not None and self.current_memory > self.maximum_memory:
            raise ValueError("current_memory cannot exceed maximum_memory")
        return self


class DeepWeaponState(StrictModel):
    drawn: bool | None = None
    record_id: str | None = Field(default=None, max_length=256)
    source: Literal["active", "weapon_right", "none"] | None = None


class DeepStatusEffectsState(StrictModel):
    observed_count: int | None = Field(default=None, ge=0)
    truncated: bool | None = None
    limit: int | None = Field(default=None, ge=1, le=256)

    @model_validator(mode="after")
    def observed_count_within_limit(self) -> "DeepStatusEffectsState":
        if self.observed_count is not None and self.limit is not None and self.observed_count > self.limit:
            raise ValueError("observed_count cannot exceed limit")
        return self


class DeepCyberdeckProgram(StrictModel):
    slot_id: str = Field(pattern=r"^AttachmentSlots\.CyberdeckProgram[1-8]$", max_length=64)
    record_id: str = Field(pattern=r"^Items\.[A-Za-z0-9_.]+$", max_length=256)
    quality: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]+$", max_length=64)
    iconic: bool | None = None


class DeepCyberdeckCapacity(StrictModel):
    used: int | None = Field(default=None, ge=0, le=8)
    empty: int | None = Field(default=None, ge=0, le=8)
    total: int | None = Field(default=None, ge=0, le=8)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "DeepCyberdeckCapacity":
        if self.total is not None:
            if self.used is not None and self.used > self.total:
                raise ValueError("used cyberdeck slots cannot exceed total")
            if self.empty is not None and self.empty > self.total:
                raise ValueError("empty cyberdeck slots cannot exceed total")
            if self.used is not None and self.empty is not None and self.used + self.empty != self.total:
                raise ValueError("used plus empty cyberdeck slots must equal total")
        return self


class DeepCyberdeckState(StrictModel):
    record_id: str | None = Field(default=None, pattern=r"^Items\.[A-Za-z0-9_.]+$", max_length=256)
    quality: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]+$", max_length=64)
    iconic: bool | None = None
    tags: list[Literal["Cyberdeck", "Cyberware", "Iconic_OS_CW"]] | None = Field(default=None, max_length=3)
    program_capacity: DeepCyberdeckCapacity | None = None
    programs: list[DeepCyberdeckProgram] | None = Field(default=None, max_length=8)
    truncated: bool | None = None

    @model_validator(mode="after")
    def normalize_stable_collections(self) -> "DeepCyberdeckState":
        if self.tags is not None:
            self.tags = list(dict.fromkeys(self.tags))
        if self.programs is not None:
            unique = {(program.slot_id, program.record_id): program for program in self.programs}
            self.programs = sorted(unique.values(), key=lambda program: (int(program.slot_id[-1]), program.record_id))
        return self


class DeepGameState(StrictModel):
    capabilities: DeepGameStateCapabilities | None = None
    player: DeepPlayerState | None = None
    stats: DeepStatsState | None = None
    stat_pools: DeepStatPoolsState | None = None
    weapon: DeepWeaponState | None = None
    status_effects: DeepStatusEffectsState | None = None
    cyberdeck: DeepCyberdeckState | None = None


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
    deep_game_state: DeepGameState | None = None
