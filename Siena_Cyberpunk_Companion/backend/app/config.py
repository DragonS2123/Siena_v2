from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIENA_CP_", extra="ignore", populate_by_name=True)

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
    siena_core_recent_reactions_limit: int = Field(default=5, ge=3, le=5)
    siena_core_player_name: str = Field(
        default="",
        max_length=80,
        validation_alias=AliasChoices("SIENA_CORE_PLAYER_NAME", "SIENA_CP_SIENA_CORE_PLAYER_NAME"),
    )
    siena_core_queue_size: int = Field(default=50, ge=2, le=1000)
    siena_core_language: Literal["ru", "en"] = "ru"
    scene_enabled: bool = True
    scene_event_history_limit: int = Field(default=20, ge=5, le=200)
    scene_history_limit: int = Field(default=50, ge=5, le=500)
    scene_idle_gap_seconds: float = Field(default=30.0, gt=0)
    scene_max_duration_seconds: float = Field(default=180.0, gt=0)
    scene_recent_event_window_seconds: float = Field(default=45.0, gt=0)
    scene_max_reactions: int = Field(default=3, ge=1, le=20)
    scene_min_reaction_interval_seconds: float = Field(default=15.0, ge=0)
    scene_critical_bypass: bool = True
    scene_resolution_reaction_enabled: bool = True
    scene_reaction_stale_grace_seconds: float = Field(default=3.0, ge=0)
    scene_recent_reactions_limit: int = Field(default=5, ge=1, le=5)
    contextual_companion_enabled: bool = True
    context_event_window_seconds: float = Field(default=60.0, gt=0, le=600)
    context_event_window_max_items: int = Field(default=20, ge=1, le=100)
    grouping_window_seconds: float = Field(default=3.0, ge=0.25, le=5)
    low_priority_queue_limit: int = Field(default=3, ge=1, le=10)
    recovery_voice_enabled: bool = False
    build_aware_reactions_enabled: bool = True
    voice_enabled: bool = False
    voice_muted: bool = False
    voice_min_interval_seconds: float = Field(default=8.0, ge=0)
    voice_same_text_cooldown_seconds: float = Field(default=120.0, ge=0)
    voice_scene_max_clips: int = Field(default=3, ge=1, le=20)
    voice_critical_bypass: bool = True
    voice_max_text_chars: int = Field(default=240, ge=20, le=500)
    voice_max_event_age_seconds: float = Field(default=30.0, gt=0)
    voice_queue_size: int = Field(default=20, ge=2, le=200)
    voice_clip_history_limit: int = Field(default=100, ge=5, le=1000)
    voice_audio_ttl_seconds: float = Field(default=300.0, gt=0)
    voice_post_play_gap_ms: int = Field(default=250, ge=0, le=5000)
    voice_language: Literal["ru", "en"] = "ru"
    voice_speaker: str = Field(default="", max_length=128)
    voice_volume: float = Field(default=0.85, ge=0, le=1)
    voice_require_tts_ready: bool = True
    voice_interrupt_mode: Literal["never", "critical_only"] = "critical_only"
    tts_base_url: str = ""
    tts_api_token: str = ""
    tts_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    tts_request_timeout_seconds: float = Field(default=60.0, gt=0)
    tts_max_retries: int = Field(default=1, ge=0, le=1)
    tts_max_audio_bytes: int = Field(default=15_000_000, ge=1024, le=100_000_000)
    tts_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    tts_circuit_reset_seconds: float = Field(default=30.0, gt=0)
    presence_enabled: bool = False
    presence_poll_interval_ms: int = Field(default=500, ge=100, le=60_000)
    presence_http_timeout_ms: int = Field(default=1000, ge=250, le=30_000)
    presence_generating_delay_ms: int = Field(default=400, ge=0, le=10_000)
    presence_normal_duration_ms: int = Field(default=8000, ge=500, le=120_000)
    presence_high_duration_ms: int = Field(default=10_000, ge=500, le=120_000)
    presence_critical_duration_ms: int = Field(default=14_000, ge=500, le=120_000)
    presence_fallback_duration_ms: int = Field(default=7000, ge=500, le=120_000)
    presence_fade_in_ms: int = Field(default=180, ge=0, le=10_000)
    presence_fade_out_ms: int = Field(default=450, ge=0, le=10_000)
    presence_max_text_chars: int = Field(default=320, ge=32, le=320)
    presence_error_backoff_ms: int = Field(default=2000, ge=250, le=60_000)
    presence_max_backoff_ms: int = Field(default=30_000, ge=1000, le=300_000)
    npc_controller_enabled: bool = False
    npc_command_poll_interval_ms: int = Field(default=400, ge=250, le=500)
    npc_command_queue_max: int = Field(default=16, ge=1, le=16)
    npc_spawn_distance: float = Field(default=2.0, ge=1.5, le=2.5)
    npc_vertical_offset: float = Field(default=0.1, ge=0.0, le=0.5)
    npc_command_expiry_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
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
