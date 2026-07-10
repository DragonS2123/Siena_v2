"""In-memory Presence runtime — one instance per backend process, held by
api/server.py (same lifetime discipline as trace_hub/session_store there).
Not persisted to disk on purpose: presence is a live "is Siena here right
now" indicator, not history, so it resets to a clean "available" state on
every backend restart, like _active_chat_model.

No background threads, no timers, no LLM calls. Idle detection, quiet-hours
windows, and quiet_until expiry are all computed lazily inside get_status()
(and every mutation method), driven entirely by the frontend's existing 5s
poll (src/hooks/usePresence.ts) — the same discipline as
core/system_metrics.py's cpu_ram_metrics()/vram_metrics().
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from presence.presence_state import PresenceState, VALID_TRANSIENT_ACTIVITIES

_DEFAULT_MESSAGES: dict[str, str] = {
    "available": "available",
    "idle": "idle",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
    "quiet": "quiet",
    "offline": "presence disabled",
    "error": "error",
}

# Deterministic, local, no-LLM message pool for the manual "Say something"
# action (POST /api/presence/say) — Phase 1 is conservative on purpose: no
# free-form generation, just a small curated set of short lines, varied by
# presence_style. Never injected into chat history by the backend; the
# frontend only ever shows these as a transient status line unless the user
# explicitly sends one to chat.
#
# These RU strings are the canonical `message` value; the frontend renders
# the localized line via i18n keys `presence.event.<type>.<style>.<variant>`
# instead (EN/RU parity), falling back to `message` if a key is missing —
# which is why every result also carries a `variant` index.
_SAY_SOMETHING_POOL: dict[str, list[str]] = {
    "calm": [
        "Я здесь, если понадоблюсь.",
        "Всё спокойно. Я рядом.",
        "Не тороплю — просто на связи.",
    ],
    "playful": [
        "Ку-ку! Я тут.",
        "Просто заглянула сказать привет.",
        "Всё ещё здесь, никуда не делась.",
    ],
    "minimal": [
        "Рядом.",
        "На связи.",
        "Здесь.",
    ],
}

# Welcome-back lines (Phase 2) — same deterministic, no-LLM discipline as
# the say pool above. Created only on a real idle -> available return (see
# maybe_create_welcome_back), shown only in the Presence Card as a UI event,
# never written into conversation history, never spoken via TTS.
_WELCOME_BACK_POOL: dict[str, list[str]] = {
    "calm": [
        "Ты вернулся. Я рядом.",
        "Я здесь, если хочешь продолжить.",
        "С возвращением. Можем продолжить спокойно.",
    ],
    "playful": [
        "О, ты вернулся! Я тут.",
        "С возвращением. Продолжаем?",
        "А вот и ты. Я никуда не уходила.",
    ],
    "minimal": [
        "Ты вернулся.",
        "С возвращением.",
        "Снова на связи.",
    ],
}


@dataclass(frozen=True)
class PresenceSettings:
    """One bundle of the live config.* values every Presence call needs —
    read fresh by api/server.py at call time (same "no restart needed"
    discipline as log_level elsewhere), passed as a single argument instead
    of five/six loose parameters."""

    enabled: bool
    idle_minutes: int
    quiet_hours_enabled: bool
    quiet_hours_start: str
    quiet_hours_end: str
    style: str
    max_messages_per_hour: int
    # Phase 2 fields — defaulted so Phase 1 call sites/tests that construct
    # this bundle without them keep working unchanged.
    show_welcome_back: bool = True
    min_seconds_between_ui_messages: int = 60


@dataclass
class PresenceTransition:
    """Returned by every mutation method so api/server.py can decide whether
    a meaningful change happened (and therefore whether to log/broadcast
    presence_state_changed) without presence_service depending on trace_hub
    or SienaLogger at all."""

    state: PresenceState
    previous_state: str
    new_state: str
    # Phase 2 — how long the user had been inactive before this activity
    # (only set by record_user_activity). The ping endpoint compares this
    # against idle_minutes to detect a "return" by real elapsed time rather
    # than by state labels, because during quiet mode/quiet hours the
    # visible state is "quiet", never "idle" — a label-based idle->available
    # check would silently miss returns that happen while quiet.
    idle_gap_seconds: float = 0.0

    @property
    def changed(self) -> bool:
        return self.previous_state != self.new_state


@dataclass
class PresenceStatusResult:
    state: PresenceState
    became_idle: bool
    returned_from_idle: bool


@dataclass
class SayResult:
    message: str | None
    throttled: bool
    style: str
    variant: int | None = None


@dataclass
class WelcomeBackResult:
    """Outcome of maybe_create_welcome_back() — the endpoint maps each
    non-created outcome to its own trace event (presence_behavior_skipped_quiet
    / presence_behavior_throttled) instead of presence_service knowing about
    tracing at all."""

    outcome: str  # "created" | "skipped_quiet" | "throttled" | "disabled" | "not_applicable"
    event: dict | None = None


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _parse_hhmm(value: str) -> int | None:
    """"HH:MM" -> minutes since midnight, or None if malformed. Malformed
    input is treated as "quiet hours disabled" rather than raised — the real
    format validation happens once, at Settings-save time
    (api/server.py::update_settings), so this is defense in depth only."""
    try:
        hours_str, minutes_str = value.split(":", 1)
        hours, minutes = int(hours_str), int(minutes_str)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


class PresenceService:
    def __init__(self) -> None:
        now = time.time()
        self._started_at = now
        self._last_user_activity_at = now
        self._last_assistant_activity_at: float | None = None
        self._last_presence_message_at: float | None = None
        self._is_quiet_mode = False
        self._quiet_until: float | None = None
        self._current_activity: str | None = None
        self._error_message: str | None = None
        self._was_idle = False
        # Set by an explicit /api/presence/wake while inside a quiet-hours
        # window, so Wake reliably wakes even though quiet hours are still
        # technically active. Auto-clears once the window ends naturally, so
        # the *next* quiet-hours window still applies quiet as configured.
        self._quiet_hours_dismissed = False
        # Rolling window of "say something" message timestamps, pruned to the
        # last hour — the only state needed for presence_max_messages_per_hour
        # throttling (no persistence, resets on restart, same as everything
        # else in this service).
        self._say_timestamps: list[float] = []
        # Phase 2 — latest UI-only presence event (welcome_back / say) and
        # the min-seconds throttle anchor for proactive UI messages. Both
        # in-memory only, reset on restart like everything else here.
        self._recent_event: dict | None = None
        self._last_ui_message_at: float | None = None
        self._welcome_back_count = 0

    # ---- internal -------------------------------------------------------

    def _in_quiet_hours(self, settings: PresenceSettings) -> bool:
        if not settings.quiet_hours_enabled:
            return False
        start = _parse_hhmm(settings.quiet_hours_start)
        end = _parse_hhmm(settings.quiet_hours_end)
        if start is None or end is None or start == end:
            return False
        now_minutes = time.localtime().tm_hour * 60 + time.localtime().tm_min
        if start < end:
            return start <= now_minutes < end
        return now_minutes >= start or now_minutes < end  # wraps past midnight

    def _effective_state(self, settings: PresenceSettings) -> str:
        now = time.time()
        if self._is_quiet_mode and self._quiet_until is not None and now >= self._quiet_until:
            self._is_quiet_mode = False
            self._quiet_until = None

        in_quiet_hours = self._in_quiet_hours(settings)
        if not in_quiet_hours:
            self._quiet_hours_dismissed = False

        if not settings.enabled:
            return "offline"
        if self._error_message:
            return "error"
        if self._is_quiet_mode or (in_quiet_hours and not self._quiet_hours_dismissed):
            return "quiet"
        if self._current_activity:
            return self._current_activity
        idle_seconds = now - self._last_user_activity_at
        if settings.idle_minutes > 0 and idle_seconds >= settings.idle_minutes * 60:
            return "idle"
        return "available"

    def _snapshot(self, state: str) -> PresenceState:
        message = self._error_message if state == "error" else _DEFAULT_MESSAGES[state]
        return PresenceState(
            state=state,
            message=message,
            last_user_activity_at=_iso(self._last_user_activity_at),
            last_assistant_activity_at=_iso(self._last_assistant_activity_at),
            last_presence_message_at=_iso(self._last_presence_message_at),
            is_quiet_mode=state == "quiet",
            quiet_until=_iso(self._quiet_until),
            uptime_seconds=round(time.time() - self._started_at),
            current_activity=self._current_activity,
            recent_event=self._recent_event,
        )

    # ---- mutation (called from existing chat/TTS/STT lifecycle points) --

    def record_user_activity(self, settings: PresenceSettings) -> PresenceTransition:
        """POST /api/presence/ping — meaningful user interaction in the UI."""
        previous_state = self._effective_state(settings)
        now = time.time()
        idle_gap = now - self._last_user_activity_at
        self._last_user_activity_at = now
        self._error_message = None
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state, idle_gap_seconds=idle_gap)

    def set_activity(self, activity: str, settings: PresenceSettings) -> PresenceTransition:
        """activity: one of thinking/listening/speaking. Called only from
        existing lifecycle points in api/server.py (chat/TTS/STT) — never by
        the model itself, never a tool."""
        if activity not in VALID_TRANSIENT_ACTIVITIES:
            raise ValueError(f"invalid transient activity: {activity!r}")
        previous_state = self._effective_state(settings)
        self._current_activity = activity
        self._error_message = None
        if activity in ("thinking", "speaking"):
            self._last_assistant_activity_at = time.time()
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state)

    def clear_activity(
        self, settings: PresenceSettings, only_if: tuple[str, ...] | None = None
    ) -> PresenceTransition:
        """Ends whatever transient activity was set via set_activity() —
        falls back to idle/available/quiet derivation. `only_if` restricts
        the clear to specific current activities: the frontend's
        POST /api/presence/activity "available" uses ("listening", "speaking")
        so a stale playback-finished signal can never wipe a backend-owned
        "thinking" set by an in-flight chat turn."""
        previous_state = self._effective_state(settings)
        if only_if is None or self._current_activity in only_if:
            self._current_activity = None
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state)

    def report_error(self, message: str, settings: PresenceSettings) -> PresenceTransition:
        previous_state = self._effective_state(settings)
        self._error_message = message
        self._current_activity = None
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state)

    def enable_quiet(self, settings: PresenceSettings, minutes: int | None = None) -> PresenceTransition:
        """POST /api/presence/quiet. `minutes` is optional — omitted means
        quiet indefinitely until an explicit /api/presence/wake."""
        previous_state = self._effective_state(settings)
        self._is_quiet_mode = True
        self._quiet_until = time.time() + minutes * 60 if minutes else None
        self._current_activity = None
        self._quiet_hours_dismissed = False
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state)

    def disable_quiet(self, settings: PresenceSettings) -> PresenceTransition:
        """POST /api/presence/wake — reliably wakes even during an active
        quiet-hours window (dismisses it until that window ends naturally)."""
        previous_state = self._effective_state(settings)
        self._is_quiet_mode = False
        self._quiet_until = None
        self._quiet_hours_dismissed = True
        new_state = self._effective_state(settings)
        return PresenceTransition(self._snapshot(new_state), previous_state, new_state)

    # ---- read -------------------------------------------------------------

    def get_status(self, settings: PresenceSettings) -> PresenceStatusResult:
        state = self._effective_state(settings)
        is_idle_now = state == "idle"
        became_idle = is_idle_now and not self._was_idle
        returned_from_idle = self._was_idle and not is_idle_now and state == "available"
        self._was_idle = is_idle_now
        return PresenceStatusResult(self._snapshot(state), became_idle, returned_from_idle)

    # ---- manual "Say something" (deterministic, no LLM) -------------------

    def say_something(self, settings: PresenceSettings) -> SayResult:
        now = time.time()
        self._say_timestamps = [ts for ts in self._say_timestamps if now - ts < 3600]
        if settings.max_messages_per_hour <= 0 or len(self._say_timestamps) >= settings.max_messages_per_hour:
            return SayResult(message=None, throttled=True, style=settings.style)

        pool = _SAY_SOMETHING_POOL.get(settings.style, _SAY_SOMETHING_POOL["calm"])
        variant = len(self._say_timestamps) % len(pool)
        message = pool[variant]
        self._say_timestamps.append(now)
        self._last_presence_message_at = now
        self._recent_event = {
            "type": "say",
            "style": settings.style if settings.style in _SAY_SOMETHING_POOL else "calm",
            "variant": variant,
            "message": message,
            "created_at": _iso(now),
        }
        return SayResult(message=message, throttled=False, style=settings.style, variant=variant)

    # ---- welcome back (Phase 2 — deterministic UI event, no LLM, no TTS) --

    def maybe_create_welcome_back(self, settings: PresenceSettings) -> WelcomeBackResult:
        """Called by the ping endpoint on a real idle -> available return.
        Produces a UI-only event for the Presence Card — never a chat
        message. Suppressed while quiet (manual quiet mode OR an active
        quiet-hours window — _effective_state covers both) and throttled by
        min_seconds_between_ui_messages so a flaky idle boundary can't spam
        the card."""
        if not settings.enabled:
            return WelcomeBackResult(outcome="not_applicable")
        if not settings.show_welcome_back:
            return WelcomeBackResult(outcome="disabled")
        if self._effective_state(settings) == "quiet":
            return WelcomeBackResult(outcome="skipped_quiet")
        now = time.time()
        if (
            self._last_ui_message_at is not None
            and now - self._last_ui_message_at < settings.min_seconds_between_ui_messages
        ):
            return WelcomeBackResult(outcome="throttled")

        style = settings.style if settings.style in _WELCOME_BACK_POOL else "calm"
        pool = _WELCOME_BACK_POOL[style]
        variant = self._welcome_back_count % len(pool)
        self._welcome_back_count += 1
        self._last_ui_message_at = now
        self._last_presence_message_at = now
        event = {
            "type": "welcome_back",
            "style": style,
            "variant": variant,
            "message": pool[variant],
            "created_at": _iso(now),
        }
        self._recent_event = event
        return WelcomeBackResult(outcome="created", event=event)

    # ---- recent event dismissal (Phase 2) ---------------------------------

    def dismiss_event(self) -> bool:
        """Returns True if there was an event to dismiss. Purely UI-side
        state — dismissing never touches conversation history."""
        had_event = self._recent_event is not None
        self._recent_event = None
        return had_event
