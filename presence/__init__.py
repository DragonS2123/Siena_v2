"""Presence layer (0.2.1, Phase 1) — a lightweight, local, opt-in runtime
layer that tracks and exposes what Siena's backend is currently doing
(available/idle/listening/thinking/speaking/quiet/offline/error).

Not a chatbot feature and not an autonomous agent: this module only tracks
state and exposes it over a few small REST endpoints (see
api/server.py's /api/presence/* handlers). It never calls the LLM, never
injects assistant chat messages on its own, and holds no background
threads/timers — every derived field (idle detection, quiet-hours expiry) is
computed lazily when read, driven entirely by the frontend's existing 5s
poll (src/hooks/usePresence.ts), the same discipline as
RuntimeStatusProvider elsewhere in this codebase.
"""

from presence.presence_service import PresenceService, PresenceSettings
from presence.presence_state import PresenceState, VALID_STATES

__all__ = ["PresenceService", "PresenceSettings", "PresenceState", "VALID_STATES"]
