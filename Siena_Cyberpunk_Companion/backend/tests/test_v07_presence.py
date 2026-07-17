from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.priorities import EventPriority
from app.main import create_app
from app.models.game_event import EventType, GameEvent
from app.models.presence import InGamePresenceState
from app.models.reaction import ReactionGenerationStatus, ReactionPriority, SienaReaction
from app.models.scene import ReactionFocus, ScenePhase
from app.models.voice import VoiceGenerationStatus, VoicePriority, VoiceState
from app.services.event_bus import EventBus
from app.services.in_game_presence import InGamePresencePolicy, InGamePresenceProjection
from pathlib import Path


NOW = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance_ms(self, value: int) -> None:
        self.now += timedelta(milliseconds=value)


def policy(max_chars: int = 320) -> InGamePresencePolicy:
    return InGamePresencePolicy(
        generating_delay_ms=400,
        normal_duration_ms=8000,
        high_duration_ms=10_000,
        critical_duration_ms=14_000,
        fallback_duration_ms=7000,
        max_text_chars=max_chars,
    )


def projection(clock: FakeClock | None = None, enabled: bool = True) -> InGamePresenceProjection:
    fake = clock or FakeClock()
    return InGamePresenceProjection(enabled=enabled, policy=policy(), poll_interval_ms=500, clock=fake)


def event(kind: EventType, session: str = "session-a") -> GameEvent:
    return GameEvent(
        session_id=session,
        event_type=kind,
        priority=EventPriority.P2_MEDIUM,
        created_at=NOW,
        summary=kind.value,
        deduplication_key=f"{session}:{kind.value}",
    )


def lifecycle(state: str, session: str = "session-a", request: str = "request-a") -> ReactionGenerationStatus:
    return ReactionGenerationStatus(
        request_id=request,
        event_id="event-a",
        session_id=session,
        event_type=EventType.COMBAT_STARTED,
        state=state,
        scene_id="scene-a",
        scene_phase=ScenePhase.COMBAT,
        focus=ReactionFocus.COMBAT_COMMENT,
    )


def reaction(
    *,
    text: str = "Держись. Я рядом.",
    priority: ReactionPriority = ReactionPriority.MEDIUM,
    session: str = "session-a",
    fallback: bool = False,
    reaction_id: str = "reaction-a",
) -> SienaReaction:
    return SienaReaction(
        reaction_id=reaction_id,
        event_id="event-a",
        session_id=session,
        event_type=EventType.COMBAT_STARTED,
        text=text,
        created_at=NOW,
        priority=priority,
        provider="template" if fallback else "siena_core",
        fallback_used=fallback,
        scene_id="scene-a",
        scene_revision=1,
        scene_phase=ScenePhase.COMBAT,
        focus=ReactionFocus.COMBAT_COMMENT,
    )


def voice(state: VoiceState, session: str = "session-a", reaction_id: str = "reaction-a") -> VoiceGenerationStatus:
    return VoiceGenerationStatus(
        voice_request_id="voice-a",
        reaction_id=reaction_id,
        session_id=session,
        scene_id="scene-a",
        state=state,
        priority=VoicePriority.MEDIUM,
        created_at=NOW,
    )


def start(item: InGamePresenceProjection, session: str = "session-a") -> None:
    item.observe("game_event", event(EventType.SESSION_STARTED, session))


def test_published_reaction_creates_snapshot_and_revision_is_monotonic():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction())
    first = item.current()
    item.observe("siena_reaction", reaction())
    assert first.active and first.state == InGamePresenceState.REACTION
    assert first.revision == item.current().revision == 1


def test_suppressed_lifecycle_never_creates_active_snapshot():
    item = projection()
    start(item)
    item.observe("reaction_generation_status", lifecycle("suppressed"))
    assert item.current().active is False


def test_old_session_reaction_is_ignored():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction(session="old"))
    assert item.current().state == InGamePresenceState.HIDDEN


def test_generating_is_debounced_without_real_sleep():
    clock = FakeClock()
    item = projection(clock)
    start(item)
    item.observe("reaction_generation_status", lifecycle("generating"))
    clock.advance_ms(399)
    assert item.tick() is False
    clock.advance_ms(1)
    assert item.tick() is True
    assert item.current().text == "Сиена формулирует реакцию…"


def test_fast_completed_reaction_prevents_generating_flicker():
    clock = FakeClock()
    item = projection(clock)
    start(item)
    item.observe("reaction_generation_status", lifecycle("generating"))
    item.observe("siena_reaction", reaction())
    clock.advance_ms(500)
    item.tick()
    assert item.current().state == InGamePresenceState.REACTION


def test_completed_reaction_replaces_generating():
    clock = FakeClock()
    item = projection(clock)
    start(item)
    item.observe("reaction_generation_status", lifecycle("generating"))
    clock.advance_ms(400)
    item.tick()
    item.observe("siena_reaction", reaction(text="Готовая реплика"))
    assert item.current().text == "Готовая реплика"


def test_critical_replaces_low_and_low_cannot_replace_fresh_critical():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction(priority=ReactionPriority.LOW, reaction_id="low"))
    item.observe("siena_reaction", reaction(text="Критическая", priority=ReactionPriority.CRITICAL, reaction_id="critical"))
    critical_revision = item.current().revision
    item.observe("siena_reaction", reaction(text="Слабая", priority=ReactionPriority.LOW, reaction_id="late-low"))
    assert item.current().reaction_id == "critical"
    assert item.current().revision == critical_revision


def test_fallback_is_honestly_labeled_without_http_details():
    item = projection()
    start(item)
    value = reaction(text="Резервный текст", fallback=True)
    value.fallback_reason = "HTTP 500 internal exception"
    item.observe("siena_reaction", value)
    snapshot = item.current()
    rendered = snapshot.model_dump_json()
    assert snapshot.state == InGamePresenceState.FALLBACK
    assert snapshot.metadata.fallback_label == "Резервная реакция"
    assert "HTTP 500" not in rendered and "exception" not in rendered


def test_voice_indicator_tracks_synthesis_real_playback_and_completion():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction())
    item.observe("voice_generation_status", voice(VoiceState.SYNTHESIZING))
    assert item.current().metadata.voice_label == "Готовится голос…"
    item.observe("voice_generation_status", voice(VoiceState.READY))
    assert item.current().voice_state == "ready" and item.current().metadata.voice_label is None
    item.observe("voice_generation_status", voice(VoiceState.PLAYING))
    assert item.current().state == InGamePresenceState.VOICE_PLAYING
    assert item.current().metadata.voice_label == "Сиена говорит"
    item.observe("voice_generation_status", voice(VoiceState.COMPLETED))
    assert item.current().state == InGamePresenceState.REACTION
    assert item.current().metadata.voice_label is None


def test_old_session_voice_update_is_ignored_after_session_end():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction())
    item.observe("game_event", event(EventType.SESSION_ENDED))
    revision = item.current().revision
    item.observe("voice_generation_status", voice(VoiceState.PLAYING))
    assert item.current().revision == revision


def test_session_end_keeps_last_reaction_until_natural_expiry():
    clock = FakeClock()
    item = projection(clock)
    start(item)
    item.observe("siena_reaction", reaction())
    item.observe("game_event", event(EventType.SESSION_ENDED))
    assert item.current().active
    clock.advance_ms(8000)
    item.tick()
    assert item.current().state == InGamePresenceState.HIDDEN


def test_new_session_clears_old_snapshot():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction())
    start(item, "session-b")
    assert item.current().active is False


def test_text_is_bounded_control_characters_removed_and_unicode_preserved():
    item = InGamePresenceProjection(enabled=True, policy=policy(50), poll_interval_ms=500, clock=FakeClock())
    start(item)
    item.observe("siena_reaction", reaction(text="Здоровье\x00 8%. Осторожно: шанс выжить — 20%. Очень длинный хвост"))
    text = item.current().text
    assert "\x00" not in text and "%" in text and "—" in text
    assert len(text) <= 50


def test_only_three_lines_are_retained():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction(text="один\nдва\nтри\nчетыре"))
    assert item.current().text == "один\nдва\nтри"


def test_disabled_presence_preserves_hidden_v06_behavior():
    item = projection(enabled=False)
    start(item)
    item.observe("siena_reaction", reaction())
    assert item.current().revision == 0 and not item.current().active


def test_poll_metrics_and_consumer_disconnect_use_fake_clock():
    clock = FakeClock()
    item = projection(clock)
    start(item)
    snapshot, _ = item.record_poll(None)
    assert snapshot is not None and item.status().consumer_connected
    clock.advance_ms(2001)
    assert item.status().consumer_connected is False


def test_after_revision_returns_unchanged_without_incrementing_revision():
    item = projection()
    start(item)
    item.observe("siena_reaction", reaction())
    revision = item.current().revision
    snapshot, _ = item.record_poll(revision)
    assert snapshot is None and item.current().revision == revision and item.unchanged_count == 1


def test_event_bus_preserves_existing_message_and_adds_presence_status():
    item = projection()
    bus = EventBus()
    bus.subscribe(item.observe)

    async def run():
        queue = await bus.connect()
        await bus.publish("game_event", event(EventType.SESSION_STARTED))
        await bus.publish("siena_reaction", reaction())
        messages = [await queue.get(), await queue.get(), await queue.get()]
        return [message["type"] for message in messages]

    import asyncio

    assert asyncio.run(run()) == ["game_event", "siena_reaction", "in_game_presence_status"]


def test_rest_current_is_strict_and_after_revision_returns_204():
    app = create_app(Settings(presence_enabled=True))
    services = app.state.services
    start(services.presence)
    services.presence.observe("siena_reaction", reaction())
    with TestClient(app) as client:
        response = client.get("/api/v1/in-game-presence/current")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["state"] == "reaction" and "audio" not in response.text
        unchanged = client.get(f"/api/v1/in-game-presence/current?after_revision={snapshot['revision']}")
        assert unchanged.status_code == 204 and unchanged.content == b""


def test_status_endpoint_is_cached_and_does_not_probe_network():
    app = create_app(Settings(presence_enabled=True))
    with TestClient(app) as client:
        response = client.get("/api/v1/in-game-presence/status")
    assert response.status_code == 200
    assert response.json()["poll_count"] == 0


def test_all_six_presence_scenarios_are_installed():
    root = Path(__file__).parents[2] / "simulator" / "scenarios"
    names = {
        "v07_presence_normal", "v07_presence_critical", "v07_presence_fallback",
        "v07_presence_stale", "v07_presence_disconnect", "v07_presence_unicode",
    }
    assert names <= {path.stem for path in root.glob("v07_presence_*.json")}
