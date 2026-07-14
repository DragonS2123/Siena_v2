from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BridgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BridgeCapabilities(BridgeModel):
    player_health: bool = False
    player_position: bool = False
    combat_state: bool = False
    vehicle_state: bool = False
    pause_state: bool = False
    district: bool = False


class BridgeHello(BridgeModel):
    bridge_id: str = Field(min_length=1, max_length=128)
    bridge_version: str = Field(min_length=1, max_length=32)
    protocol_version: str = Field(min_length=1, max_length=32)
    transport: Literal["red_http_client"]
    game_version: str = Field(default="unknown", min_length=1, max_length=64)
    cet_version: str = Field(default="unknown", min_length=1, max_length=128)
    capabilities: BridgeCapabilities


class BridgeHeartbeat(BridgeModel):
    bridge_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    sequence: int | None = Field(default=None, ge=0)
    dropped_stale_states: int = Field(default=0, ge=0)
    telemetry_rate: float | None = Field(default=None, ge=0, le=1000)


class BridgeDisconnect(BridgeModel):
    bridge_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="bridge_shutdown", min_length=1, max_length=256)


class BridgeStatus(BridgeModel):
    connected: bool = False
    compatible: bool = False
    bridge_id: str | None = None
    bridge_version: str | None = None
    protocol_version: str | None = None
    game_version: str | None = None
    cet_version: str | None = None
    last_hello_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_telemetry_at: datetime | None = None
    last_sequence: int | None = None
    session_id: str | None = None
    capabilities: BridgeCapabilities = Field(default_factory=BridgeCapabilities)
    last_error: str | None = None
    latency_ms: float | None = None
    telemetry_rate: float = 0.0
    dropped_stale_states: int = 0
    source_conflict: bool = False
    active_source: Literal["cet", "simulator"] = "simulator"
    configured_source: Literal["auto", "cet", "simulator"] = "auto"
    registered_bridges: int = 0
