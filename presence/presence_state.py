"""Shape of Presence's runtime state — plain data, no logic. See
presence_service.py for how these fields are actually computed/mutated.
"""

from __future__ import annotations

from dataclasses import dataclass

# The 8 states requested for Presence Layer Phase 1. "offline" means the
# presence layer itself is disabled (config.ENABLE_PRESENCE=False) — it is
# not a health-check/heartbeat state, there is no background monitoring here.
VALID_STATES = (
    "available",
    "idle",
    "listening",
    "thinking",
    "speaking",
    "quiet",
    "offline",
    "error",
)

# Transient activities set explicitly from existing chat/TTS/STT lifecycle
# hooks in api/server.py — never inferred, never set by the model itself.
VALID_TRANSIENT_ACTIVITIES = ("thinking", "listening", "speaking")


@dataclass(frozen=True)
class PresenceState:
    state: str
    message: str
    last_user_activity_at: str | None
    last_assistant_activity_at: str | None
    last_presence_message_at: str | None
    is_quiet_mode: bool
    quiet_until: str | None = None
    uptime_seconds: int | None = None
    current_activity: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "message": self.message,
            "last_user_activity_at": self.last_user_activity_at,
            "last_assistant_activity_at": self.last_assistant_activity_at,
            "last_presence_message_at": self.last_presence_message_at,
            "quiet_until": self.quiet_until,
            "is_quiet_mode": self.is_quiet_mode,
            "uptime_seconds": self.uptime_seconds,
            "current_activity": self.current_activity,
        }
