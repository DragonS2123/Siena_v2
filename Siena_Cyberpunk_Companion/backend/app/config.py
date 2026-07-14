from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIENA_CP_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    event_buffer_size: int = Field(default=1000, ge=20, le=10000)
    websocket_queue_size: int = Field(default=256, ge=8, le=2048)
    damage_window_seconds: float = Field(default=0.5, gt=0)
    enemy_debounce_seconds: float = Field(default=5.0, gt=0)
    companion_too_far_meters: float = Field(default=20.0, gt=0)
    companion_stuck_seconds: float = Field(default=5.0, gt=0)
    companion_movement_epsilon: float = Field(default=0.25, gt=0)
    telemetry_source: Literal["auto", "cet", "simulator"] = "auto"
    bridge_timeout_seconds: float = Field(default=5.0, gt=1, le=60)
    bridge_registry_size: int = Field(default=8, ge=1, le=32)
    protocol_version: str = "1.0"
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
