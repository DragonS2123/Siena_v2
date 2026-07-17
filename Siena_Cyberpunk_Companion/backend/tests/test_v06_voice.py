import io
import json
import wave
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import ReactionPriority, SienaReaction
from app.models.scene import ReactionFocus, SceneContext, ScenePhase
from app.models.voice import VoiceClip, VoicePlaybackEvent, VoicePriority, VoiceRequest, VoiceState
from app.models.voice import VoiceClipReady
from app.services.event_bus import EventBus
from app.services.scene_context import SceneContextBuilder
from app.services.siena_tts_client import SienaTtsClient, SienaTtsClientError, SynthesizedAudio
from app.services.speech_text_normalizer import SpeechTextError, SpeechTextNormalizer
from app.services.tts_circuit_breaker import TtsCircuitBreaker, TtsCircuitOpenError
from app.services.voice_behavior_policy import VoiceBehaviorPolicy
from app.services.voice_dispatch import VoiceClipStore, VoiceDispatchService, VoicePriorityQueue

NOW = datetime(2026, 7, 15, 14, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self): self.now = NOW
    def __call__(self): return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


def wav_bytes(frames=240, rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def reaction(
    *, reaction_id="reaction-1", session="session-1", text="Здоровье критическое. Найди укрытие.",
    focus=ReactionFocus.DANGER_WARNING, priority=ReactionPriority.CRITICAL, created_at=NOW,
    scene_id=None, scene_revision=None, event_type=EventType.HEALTH_CRITICAL,
):
    return SienaReaction(
        reaction_id=reaction_id, event_id=f"event-{reaction_id}", session_id=session,
        event_type=event_type, text=text, created_at=created_at, priority=priority,
        provider="template_fallback", requested_provider="siena_core", fallback_used=True,
        scene_id=scene_id, scene_revision=scene_revision, scene_phase=ScenePhase.DANGER,
        focus=focus,
    )


def voice_request(**overrides):
    body = dict(
        voice_request_id="voice-1", reaction_id="reaction-1", event_id="event-1",
        session_id="session-1", priority=VoicePriority.CRITICAL,
        text="Здоровье критическое. Найди укрытие.", provider="template_fallback",
        language="ru", created_at=NOW, expires_at=NOW + timedelta(minutes=5),
    )
    body.update(overrides)
    return VoiceRequest(**body)


def policy(clock=None, **overrides):
    values = dict(
        enabled=True, muted=False, min_interval_seconds=8,
        same_text_cooldown_seconds=120, scene_max_clips=3, critical_bypass=True,
        max_text_chars=240, max_event_age_seconds=30, audio_ttl_seconds=300,
        language="ru", speaker="", require_tts_ready=True, clock=clock or FakeClock(),
    )
    values.update(overrides)
    return VoiceBehaviorPolicy(**values)


@pytest.mark.parametrize(("source", "expected"), [
    ("Здоровье *критическое*. Найди укрытие.", "Здоровье критическое. Найди укрытие."),
    ("**Осторожно**, здоровья почти не осталось.", "Осторожно, здоровья почти не осталось."),
    ("`Привет`", "Привет"),
    ("```text\nПривет\n```", "Привет"),
    ("Привет https://example.com/test", "Привет"),
    ("Тест [provider=core] готов", "Тест готов"),
    ("  Русский   текст — 42.  ", "Русский текст — 42."),
    ("Неисправимый РЎРёРµРЅР°", "Неисправимый РЎРёРµРЅР°"),
])
def test_speech_text_normalizer_preserves_words_unicode_and_numbers(source, expected):
    assert SpeechTextNormalizer().normalize(source) == expected


def test_speech_text_normalizer_limits_without_breaking_word():
    assert SpeechTextNormalizer(10).normalize("один длинноеслово потом") == "один"


@pytest.mark.parametrize("source", ["", "   ", "https://example.com", "[provider=core]"])
def test_speech_text_normalizer_rejects_empty(source):
    with pytest.raises(SpeechTextError): SpeechTextNormalizer().normalize(source)


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"enabled": False}, "voice_disabled"),
    ({"muted": True}, "muted"),
    ({"require_tts_ready": True, "tts_ready": False}, "tts_unavailable"),
])
def test_voice_policy_explicit_silence(kwargs, reason):
    tts_ready = kwargs.pop("tts_ready", True)
    p = policy(**kwargs)
    assert p.evaluate(reaction(), None, "session-1", tts_ready) is None
    assert p.last_suppression_reason == reason


def test_voice_policy_rejects_old_and_stale_session():
    clock = FakeClock(); p = policy(clock=clock)
    assert p.evaluate(reaction(session="old"), None, "new", True) is None
    assert p.last_suppression_reason == "old_session"
    assert p.evaluate(reaction(created_at=NOW-timedelta(seconds=31)), None, "session-1", True) is None
    assert p.last_suppression_reason == "stale_reaction"


@pytest.mark.parametrize(("focus", "priority"), [
    (ReactionFocus.SESSION_GREETING, ReactionPriority.MEDIUM),
    (ReactionFocus.DANGER_WARNING, ReactionPriority.CRITICAL),
    (ReactionFocus.RECOVERY_COMMENT, ReactionPriority.HIGH),
    (ReactionFocus.SCENE_RESOLUTION, ReactionPriority.MEDIUM),
    (ReactionFocus.COMBAT_COMMENT, ReactionPriority.HIGH),
])
def test_voice_policy_eligible_reactions(focus, priority):
    opportunity = policy(min_interval_seconds=0).evaluate(reaction(focus=focus, priority=priority), None, "session-1", True)
    assert opportunity is not None
    assert opportunity.request.focus == focus


@pytest.mark.parametrize("focus", [ReactionFocus.IDLE_COMMENT, ReactionFocus.EXPLORATION_COMMENT, ReactionFocus.VEHICLE_COMMENT])
def test_voice_policy_silences_weak_focus(focus):
    p = policy(min_interval_seconds=0)
    assert p.evaluate(reaction(focus=focus, priority=ReactionPriority.LOW, event_type=EventType.PLAYER_IDLE), None, "session-1", True) is None


def test_voice_policy_deduplicates_reaction_and_text():
    p = policy(min_interval_seconds=0)
    assert p.evaluate(reaction(), None, "session-1", True)
    assert p.evaluate(reaction(), None, "session-1", True) is None
    assert p.last_suppression_reason == "duplicate_reaction"
    assert p.evaluate(reaction(reaction_id="reaction-2"), None, "session-1", True) is None
    assert p.last_suppression_reason == "duplicate_text"


def test_voice_policy_scene_budget_and_critical_bypass():
    p = policy(scene_max_clips=1, min_interval_seconds=0, same_text_cooldown_seconds=0)
    first = reaction(reaction_id="one", focus=ReactionFocus.RECOVERY_COMMENT, priority=ReactionPriority.HIGH, text="Стало лучше.", event_type=EventType.PLAYER_HEALED)
    assert p.evaluate(first, None, "session-1", True)
    weak = reaction(reaction_id="two", focus=ReactionFocus.SCENE_RESOLUTION, priority=ReactionPriority.MEDIUM, text="Бой закончился.", event_type=EventType.COMBAT_ENDED)
    assert p.evaluate(weak, None, "session-1", True) is None
    critical = reaction(reaction_id="three", text="Срочно в укрытие.")
    assert p.evaluate(critical, None, "session-1", True) is not None


def make_client(handler, *, retries=1, max_bytes=15000000, threshold=3, clock=None):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SienaTtsClient(
        base_url="http://tts", api_token="secret", connect_timeout_seconds=1,
        request_timeout_seconds=2, max_retries=retries, max_audio_bytes=max_bytes,
        failure_threshold=threshold, circuit_reset_seconds=10, client=http, clock=clock,
    )


@pytest.mark.asyncio
async def test_tts_client_sends_utf8_and_accepts_valid_wav():
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, content=wav_bytes(), headers={"content-type":"audio/wav","x-siena-tts-provider":"fake","x-siena-tts-speaker":"serena"})
    client = make_client(handler)
    result = await client.synthesize(voice_request())
    assert captured["text"] == "Здоровье критическое. Найди укрытие."
    assert captured["speaker"] is None
    assert result.provider == "fake" and result.sample_rate == 24000 and result.channels == 1


@pytest.mark.parametrize(("response", "category"), [
    (httpx.Response(200, content=b"x", headers={"content-type":"text/plain"}), "unsupported_content_type"),
    (httpx.Response(200, content=b"", headers={"content-type":"audio/wav"}), "empty_audio"),
    (httpx.Response(200, content=b"not-wave", headers={"content-type":"audio/wav"}), "malformed_audio"),
    (httpx.Response(401), "authentication_error"),
    (httpx.Response(403), "authentication_error"),
])
@pytest.mark.asyncio
async def test_tts_client_controlled_failures(response, category):
    client = make_client(lambda request: response, retries=0)
    with pytest.raises(SienaTtsClientError) as caught: await client.synthesize(voice_request())
    assert caught.value.category == category


@pytest.mark.asyncio
async def test_tts_client_rejects_oversized_audio():
    client = make_client(lambda request: httpx.Response(200, content=wav_bytes(), headers={"content-type":"audio/wav"}), retries=0, max_bytes=50)
    with pytest.raises(SienaTtsClientError, match="exceeds"): await client.synthesize(voice_request())


@pytest.mark.parametrize(("mode", "expected_calls"), [("500",2),("timeout",2),("400",1)])
@pytest.mark.asyncio
async def test_tts_retry_rules(mode, expected_calls):
    calls = 0
    def handler(request):
        nonlocal calls; calls += 1
        if mode == "timeout": raise httpx.ReadTimeout("late", request=request)
        return httpx.Response(int(mode))
    client = make_client(handler, retries=1)
    with pytest.raises(SienaTtsClientError): await client.synthesize(voice_request())
    assert calls == expected_calls


def test_tts_circuit_open_half_open_close():
    clock = FakeClock(); circuit = TtsCircuitBreaker(2, 10, clock)
    circuit.allow_request(); circuit.record_failure(); circuit.allow_request(); circuit.record_failure()
    with pytest.raises(TtsCircuitOpenError): circuit.allow_request()
    clock.advance(10); circuit.allow_request(); assert circuit.state == "half_open"
    circuit.record_success(); assert circuit.state == "closed" and circuit.consecutive_failures == 0


def test_tts_half_open_failure_reopens():
    clock = FakeClock(); circuit = TtsCircuitBreaker(1, 1, clock)
    circuit.record_failure(); clock.advance(1); circuit.allow_request(); circuit.record_failure()
    assert circuit.state == "open"


@pytest.mark.asyncio
async def test_voice_queue_is_priority_ordered_and_bounded():
    queue = VoicePriorityQueue(2)
    low = voice_request(voice_request_id="low", priority=VoicePriority.LOW)
    medium = voice_request(voice_request_id="medium", priority=VoicePriority.MEDIUM)
    critical = voice_request(voice_request_id="critical", priority=VoicePriority.CRITICAL)
    assert (await queue.put(low, lambda _:False))[0]
    assert (await queue.put(medium, lambda _:False))[0]
    accepted, dropped = await queue.put(critical, lambda _:False)
    assert accepted and dropped[0].voice_request_id == "low"
    assert (await queue.get()).voice_request_id == "critical"


@pytest.mark.asyncio
async def test_critical_replaces_weak_same_scene_and_session_change_drops_old():
    queue = VoicePriorityQueue(5)
    low = voice_request(voice_request_id="low", scene_id="scene", priority=VoicePriority.LOW)
    await queue.put(low, lambda _:False)
    critical = voice_request(voice_request_id="critical", scene_id="scene", priority=VoicePriority.CRITICAL)
    assert [item.voice_request_id for item in await queue.drop_weaker_scene(critical)] == ["low"]
    await queue.put(voice_request(voice_request_id="old", session_id="old"), lambda _:False)
    assert [item.voice_request_id for item in await queue.drop_other_sessions("new")] == ["old"]


def clip(clip_id="clip-1", state=VoiceState.READY, session="session-1", expires=NOW+timedelta(minutes=5)):
    return VoiceClip(
        voice_clip_id=clip_id, voice_request_id=f"request-{clip_id}", reaction_id=f"reaction-{clip_id}",
        session_id=session, priority=VoicePriority.HIGH, state=state, sample_rate=24000,
        channels=1, duration_ms=10, byte_length=len(wav_bytes()), created_at=NOW,
        expires_at=expires, tts_provider="fake", language="ru",
    )


def test_clip_store_is_bounded_and_expires():
    clock = FakeClock(); store = VoiceClipStore(2, clock)
    store.put(clip("one"), wav_bytes()); store.put(clip("two"), wav_bytes()); store.put(clip("three"), wav_bytes())
    assert [item.voice_clip_id for item in store.list()] == ["three","two"]
    clock.advance(301); assert store.list() == []


def test_clip_store_suppresses_old_session_without_deleting_text_metadata():
    store = VoiceClipStore(5, FakeClock()); item = clip(session="old"); item.metadata={"text_preview":"Привет"}; store.put(item,wav_bytes())
    changed=store.suppress_other_sessions("new")
    assert changed[0].state==VoiceState.SUPPRESSED and changed[0].metadata["text_preview"]=="Привет"


class FakeTtsClient:
    def __init__(self):
        self.reachable=True; self.provider="fake"; self.speaker="serena"; self.language="ru"
        self.circuit=TtsCircuitBreaker(); self.last_request_at=self.last_success_at=self.last_failure_at=None
        self.last_error=None; self.average_latency_ms=1.0; self.configured=True; self.calls=[]
    async def probe_status(self): self.reachable=True; return True
    async def synthesize(self, request):
        self.calls.append(request)
        return SynthesizedAudio(wav_bytes(),"audio/wav",24000,1,10,"fake","serena","ru",1)
    async def close(self): pass


def session_event(event_type=EventType.SESSION_STARTED, session="session-1"):
    return GameEvent(session_id=session,event_type=event_type,priority="P2_MEDIUM",created_at=NOW,source="test",summary="session",deduplication_key=f"{event_type}:{session}")


@pytest.mark.asyncio
async def test_voice_worker_publishes_lifecycle_and_ready_without_blocking_text():
    bus=EventBus(); messages=await bus.connect(); fake=FakeTtsClient(); clock=FakeClock()
    service=VoiceDispatchService(enabled=True,muted=False,policy=policy(clock=clock,min_interval_seconds=0),bus=bus,tts_client=fake,scene_builder=SceneContextBuilder(),queue_capacity=5,clip_history_limit=5,audio_ttl_seconds=300,max_event_age_seconds=30,language="ru",speaker="",clock=clock)
    await service.observe_event(session_event()); await service.start()
    await service.handle_reaction(reaction())
    states=[]
    while "ready" not in states:
        message=await __import__('asyncio').wait_for(messages.get(),1)
        if message["type"]=="voice_generation_status": states.append(message["data"]["state"])
    assert states[:3]==["queued","synthesizing","ready"] and len(fake.calls)==1
    await service.stop()


def test_voice_models_are_strict():
    with pytest.raises(ValidationError): VoiceRequest(**{**voice_request().model_dump(),"unexpected":True})


def test_voice_rest_audio_history_status_and_playback():
    app=create_app(Settings(voice_enabled=False)); service=app.state.services.voice_dispatch
    service._active_session="session-1"; ready=clip(expires=service.clock()+timedelta(minutes=5)); service.store.put(ready,wav_bytes())
    with TestClient(app) as client:
        assert client.get("/api/v1/voice/status").status_code==200
        assert client.get("/api/v1/voice/clips").json()[0]["voice_clip_id"]=="clip-1"
        audio=client.get("/api/v1/voice/audio/clip-1"); assert audio.status_code==200 and audio.content.startswith(b"RIFF")
        assert client.get("/api/v1/voice/audio/../secret").status_code in {404,405}
        started=client.post("/api/v1/voice/playback-events",json={"voice_clip_id":"clip-1","event":"playback_started","tab_id":"tab-1","reason":None})
        assert started.status_code==200 and started.json()["state"]=="playing"
        completed=client.post("/api/v1/voice/playback-events",json={"voice_clip_id":"clip-1","event":"playback_completed","tab_id":"tab-1","reason":None})
        assert completed.json()["state"]=="completed"
        assert client.get("/api/v1/voice/audio/missing").status_code==404


@pytest.mark.asyncio
async def test_backend_rejects_second_tab_playback_for_same_clip():
    bus=EventBus(); fake=FakeTtsClient(); service=VoiceDispatchService(enabled=True,muted=False,policy=policy(),bus=bus,tts_client=fake,scene_builder=SceneContextBuilder(),queue_capacity=5,clip_history_limit=5,audio_ttl_seconds=300,max_event_age_seconds=30,language="ru",speaker="",clock=FakeClock())
    service._active_session="session-1"; service.store.put(clip(),wav_bytes())
    await service.playback_event(VoicePlaybackEvent(voice_clip_id="clip-1",event="playback_started",tab_id="leader"))
    from app.services.voice_dispatch import VoicePlaybackConflict
    with pytest.raises(VoicePlaybackConflict):
        await service.playback_event(VoicePlaybackEvent(voice_clip_id="clip-1",event="playback_started",tab_id="follower"))


@pytest.mark.asyncio
async def test_voice_websocket_payload_contains_url_but_never_audio_bytes():
    bus=EventBus(); queue=await bus.connect()
    payload=VoiceClipReady(voice_clip_id="clip",voice_request_id="request",reaction_id="reaction",session_id="session-1",priority=VoicePriority.HIGH,audio_url="/api/v1/voice/audio/clip",content_type="audio/wav",duration_ms=10,tts_provider="fake",expires_at=NOW+timedelta(minutes=1))
    await bus.publish("voice_clip_ready",payload); message=await queue.get()
    assert message["payload"]["audio_url"].endswith("/clip")
    assert "audio" not in message["payload"] and "bytes" not in message["payload"]


def test_voice_disabled_backend_is_headless_and_worker_free():
    with TestClient(create_app(Settings(voice_enabled=False))) as client:
        status=client.get("/api/v1/voice/status").json()
        assert status["enabled"] is False and status["worker_running"] is False
        assert client.get("/health").status_code==200


def test_quick_recovery_makes_ready_danger_voice_request_stale():
    builder=SceneContextBuilder(); clock=FakeClock(); fake=FakeTtsClient()
    start=session_event(); builder.apply(start)
    danger=GameEvent(session_id="session-1",event_type=EventType.HEALTH_CRITICAL,priority="P0_CRITICAL",created_at=NOW+timedelta(seconds=1),source="test",summary="danger",deduplication_key="danger",payload={"health_percent":5})
    snapshot=builder.apply(danger).scene
    service=VoiceDispatchService(enabled=True,muted=False,policy=policy(clock=clock),bus=EventBus(),tts_client=fake,scene_builder=builder,queue_capacity=5,clip_history_limit=5,audio_ttl_seconds=300,max_event_age_seconds=30,language="ru",speaker="",clock=clock)
    service._active_session="session-1"
    request=voice_request(scene_id=snapshot.scene_id,scene_revision=snapshot.revision,focus=ReactionFocus.DANGER_WARNING)
    healed=GameEvent(session_id="session-1",event_type=EventType.PLAYER_HEALED,priority="P1_HIGH",created_at=NOW+timedelta(seconds=2),source="test",summary="healed",deduplication_key="healed",payload={"health_percent":80,"healed_amount":75})
    builder.apply(healed)
    assert service._is_stale(request) is True
