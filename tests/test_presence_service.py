"""Unit tests for presence/presence_service.py — pure, importable module, no
FastAPI/TestClient involved (matches tests/test_resource_manager.py's
"test the importable module directly" convention). Endpoint-level coverage
(GET/POST /api/presence/*, settings persistence) lives in
tests/test_presence_endpoint.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from presence.presence_service import PresenceService, PresenceSettings  # noqa: E402


def _settings(**overrides) -> PresenceSettings:
    base = dict(
        enabled=True,
        idle_minutes=15,
        quiet_hours_enabled=False,
        quiet_hours_start="23:00",
        quiet_hours_end="08:00",
        style="calm",
        max_messages_per_hour=2,
    )
    base.update(overrides)
    return PresenceSettings(**base)


def test_default_status_is_available():
    service = PresenceService()
    result = service.get_status(_settings())
    assert result.state.state == "available"
    assert result.state.is_quiet_mode is False
    assert result.became_idle is False


def test_ping_updates_last_user_activity_timestamp():
    service = PresenceService()
    before = service.get_status(_settings()).state.last_user_activity_at
    transition = service.record_user_activity(_settings())
    after = transition.state.last_user_activity_at
    assert after is not None
    assert before is not None
    assert after >= before  # ISO strings, lexicographically comparable


def test_idle_detected_after_threshold(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    now = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: now[0])
    # Re-anchor last_user_activity_at to the fake clock's starting point.
    service._last_user_activity_at = now[0]

    settings = _settings(idle_minutes=1)
    assert service.get_status(settings).state.state == "available"

    now[0] += 61  # past the 1-minute idle threshold
    result = service.get_status(settings)
    assert result.state.state == "idle"
    assert result.became_idle is True


def test_returning_from_idle_is_detected(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    now = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: now[0])
    service._last_user_activity_at = now[0]

    settings = _settings(idle_minutes=1)
    now[0] += 61
    assert service.get_status(settings).state.state == "idle"

    now[0] += 1
    transition = service.record_user_activity(settings)
    assert transition.new_state == "available"
    assert transition.previous_state == "idle"

    result = service.get_status(settings)
    assert result.returned_from_idle is True


def test_quiet_mode_enable_and_disable():
    service = PresenceService()
    settings = _settings()

    transition = service.enable_quiet(settings)
    assert transition.new_state == "quiet"
    assert transition.state.is_quiet_mode is True

    status = service.get_status(settings)
    assert status.state.state == "quiet"

    wake_transition = service.disable_quiet(settings)
    assert wake_transition.new_state == "available"
    assert wake_transition.state.is_quiet_mode is False


def test_quiet_until_auto_expires(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    now = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: now[0])
    service._last_user_activity_at = now[0]

    settings = _settings()
    service.enable_quiet(settings, minutes=5)
    assert service.get_status(settings).state.state == "quiet"

    now[0] += 5 * 60 + 1
    result = service.get_status(settings)
    assert result.state.state == "available"
    assert result.state.is_quiet_mode is False


def test_disabled_presence_reports_offline():
    service = PresenceService()
    result = service.get_status(_settings(enabled=False))
    assert result.state.state == "offline"


def test_report_error_sets_error_state_and_clears_on_activity():
    service = PresenceService()
    settings = _settings()

    transition = service.report_error("boom", settings)
    assert transition.new_state == "error"
    assert service.get_status(settings).state.message == "boom"

    # Any subsequent user activity clears the error (see record_user_activity).
    cleared = service.record_user_activity(settings)
    assert cleared.new_state != "error"


def test_transient_activities_thinking_listening_speaking():
    service = PresenceService()
    settings = _settings()

    for activity in ("thinking", "listening", "speaking"):
        transition = service.set_activity(activity, settings)
        assert transition.new_state == activity
        assert service.get_status(settings).state.current_activity == activity
        cleared = service.clear_activity(settings)
        assert cleared.new_state == "available"


def test_set_activity_rejects_unknown_value():
    service = PresenceService()
    try:
        service.set_activity("dancing", _settings())
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_quiet_hours_window_wrapping_midnight(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    # 23:30 local time — inside a 23:00 -> 08:00 quiet-hours window.
    fake_struct = mod.time.struct_time((2026, 1, 1, 23, 30, 0, 0, 1, 0))
    monkeypatch.setattr(mod.time, "localtime", lambda *_: fake_struct)

    settings = _settings(quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="08:00")
    result = service.get_status(settings)
    assert result.state.state == "quiet"
    assert result.state.is_quiet_mode is True


def test_quiet_hours_disabled_by_default_does_not_apply(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    fake_struct = mod.time.struct_time((2026, 1, 1, 23, 30, 0, 0, 1, 0))
    monkeypatch.setattr(mod.time, "localtime", lambda *_: fake_struct)

    settings = _settings(quiet_hours_enabled=False, quiet_hours_start="23:00", quiet_hours_end="08:00")
    result = service.get_status(settings)
    assert result.state.state == "available"


def test_wake_dismisses_quiet_hours_for_current_window(monkeypatch):
    import presence.presence_service as mod

    service = PresenceService()
    fake_struct = mod.time.struct_time((2026, 1, 1, 23, 30, 0, 0, 1, 0))
    monkeypatch.setattr(mod.time, "localtime", lambda *_: fake_struct)

    settings = _settings(quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="08:00")
    assert service.get_status(settings).state.state == "quiet"

    service.disable_quiet(settings)
    assert service.get_status(settings).state.state == "available"


def test_say_something_returns_deterministic_message_and_updates_timestamp():
    service = PresenceService()
    settings = _settings(style="calm", max_messages_per_hour=2)

    result = service.say_something(settings)
    assert result.throttled is False
    assert isinstance(result.message, str) and result.message

    status = service.get_status(settings)
    assert status.state.last_presence_message_at is not None


def test_say_something_throttled_after_max_per_hour():
    service = PresenceService()
    settings = _settings(style="calm", max_messages_per_hour=2)

    first = service.say_something(settings)
    second = service.say_something(settings)
    third = service.say_something(settings)

    assert first.throttled is False
    assert second.throttled is False
    assert third.throttled is True
    assert third.message is None


def test_say_something_zero_max_per_hour_always_throttled():
    service = PresenceService()
    result = service.say_something(_settings(max_messages_per_hour=0))
    assert result.throttled is True
    assert result.message is None
