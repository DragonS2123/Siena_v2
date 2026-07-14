from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIENA_CP_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    event_buffer_size: int = Field(default=500, ge=20, le=10000)
    reaction_buffer_size: int = Field(default=200, ge=20, le=5000)
    websocket_queue_size: int = Field(default=256, ge=8, le=2048)
    telemetry_expected_rate_hz: float = Field(default=4.0, gt=0, le=1000)
    damage_window_seconds: float = Field(default=1.5, gt=0)
    enemy_debounce_seconds: float = Field(default=5.0, gt=0)
    same_event_cooldown_seconds: float = Field(default=60.0, ge=0)
    general_reaction_cooldown_seconds: float = Field(default=20.0, ge=0)
    health_low_threshold_percent: float = Field(default=25.0, gt=0, lt=100)
    health_low_recovery_percent: float = Field(default=35.0, gt=0, le=100)
    health_critical_threshold_percent: float = Field(default=10.0, gt=0, lt=100)
    heal_threshold_percent: float = Field(default=5.0, gt=0, le=100)
    idle_timeout_seconds: float = Field(default=60.0, gt=0)
    player_position_epsilon: float = Field(default=0.5, gt=0)
    session_disconnect_timeout_seconds: float = Field(default=10.0, gt=0)
    reactions_enabled: bool = True
    reaction_provider: Literal["template", "disabled", "siena_core"] = "template"
    siena_core_enabled: bool = False
    siena_core_base_url: str = ""
    siena_core_api_token: str = ""
    siena_core_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    siena_core_request_timeout_seconds: float = Field(default=45.0, gt=0)
    siena_core_max_retries: int = Field(default=1, ge=0, le=3)
    siena_core_fallback_provider: Literal["template", "disabled"] = "template"
    siena_core_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    siena_core_circuit_reset_seconds: float = Field(default=30.0, gt=0)
    siena_core_max_response_chars: int = Field(default=320, ge=32, le=2000)
    siena_core_max_event_age_seconds: float = Field(default=45.0, gt=0)
    siena_core_recent_events_limit: int = Field(default=6, ge=1, le=20)
    siena_core_queue_size: int = Field(default=50, ge=2, le=1000)
    siena_core_language: Literal["ru", "en"] = "ru"
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
