import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.priorities import EventPriority
from app.main import create_app
from app.models.game_event import EventSeverity, EventType, GameEvent
from app.models.reaction import ReactionPriority, ReactionRequest, SienaReaction
from app.services.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.services.event_bus import EventBus
from app.services.game_reaction_prompt import GameReactionPromptBuilder
from app.services.reaction_dispatch import ReactionDispatchService, ReactionPriorityQueue
from app.services.reaction_planner import DeferredSienaCoreProvider, ReactionPlanner
from app.services.reaction_response_sanitizer import InvalidReactionResponse, ReactionResponseSanitizer
from app.services.siena_core_reaction_provider import ProviderResult, SienaCoreProviderError, SienaCoreReactionProvider


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def event(event_type=EventType.HEALTH_LOW, priority=EventPriority.P1_HIGH, *, created_at=NOW, session="game-1", payload=None):
    severity = EventSeverity.CRITICAL if priority == EventPriority.P0_CRITICAL else EventSeverity.HIGH
    return GameEvent(
        session_id=session, event_type=event_type, priority=priority, severity=severity,
        created_at=created_at, source="simulator", sequence=2, summary="Player health is low",
        deduplication_key=f"{session}:{event_type}", payload=payload or {"health_percent": 20, "x": 999},
    )


def reaction_request(item=None):
    item = item or event()
    return ReactionRequest(request_id="request-1", event=item, recent_events=[item], queued_at=NOW)


def response(text="Береги себя.", model="qwen3.5:9b"):
    return {"text": text, "reasoning": None, "model": model, "request_id": "request-1"}


def provider(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://siena.test")
    return SienaCoreReactionProvider(enabled=True, base_url="http://siena.test", client=client, **kwargs)


def test_prompt_builder_sends_only_meaningful_event_context():
    prompt = GameReactionPromptBuilder().build(event(payload={"health_percent": 20, "x": 999, "raw_telemetry": {"everything": True}}), [], "ru", NOW)
    assert '"health_percent":20' in prompt
    assert '"x"' not in prompt and "raw_telemetry" not in prompt
    assert "Не вызывай инструменты" in prompt and "TTS" in prompt and "Не сохраняй событие в память" in prompt


def test_prompt_defines_persona_player_identity_and_optional_name():
    builder = GameReactionPromptBuilder()
    unnamed = builder.build(event(), [], "ru", NOW)
    assert "Ты — Сиена, игровой компаньон и наблюдатель" in unnamed
    assert "Игрок не является Сиеной" in unnamed
    assert "не начинай реплику со своего имени" in unnamed
    assert '"player_name"' not in unnamed

    named = builder.build(event(), [], "ru", NOW, player_name="Ви")
    assert '"player_name":"Ви"' in named
    assert "Имя игрока — Ви" in named


def test_unprefixed_player_name_environment_setting(monkeypatch):
    monkeypatch.setenv("SIENA_CORE_PLAYER_NAME", "Ви")
    assert Settings().siena_core_player_name == "Ви"


def test_prompt_recent_reactions_are_session_scoped():
    current = SienaReaction(
        event_id="one", session_id="game-1", event_type=EventType.HEALTH_LOW,
        text="Будь осторожнее.", priority=ReactionPriority.HIGH, provider="siena_core",
    )
    other = current.model_copy(update={"reaction_id": "other", "session_id": "game-2", "text": "Чужая сессия."})
    prompt = GameReactionPromptBuilder().build(event(), [], "ru", NOW, [other, current])
    assert '"event_type":"health_low","text":"Будь осторожнее."' in prompt
    assert "Чужая сессия" not in prompt


@pytest.mark.asyncio
async def test_siena_provider_creates_strict_stateless_request():
    captured = {}

    async def handler(request):
        captured.update(await request.aread() and request.json() if hasattr(request, "json") else {})
        return httpx.Response(200, json=response())

    # MockTransport Request has no json(); decode explicitly.
    async def capture(request):
        import json
        captured.update(json.loads((await request.aread()).decode()))
        return httpx.Response(200, json=response())

    core = provider(capture)
    result = await core.generate_reaction(reaction_request(), NOW)
    assert result.text == "Береги себя." and result.model == "qwen3.5:9b"
    assert captured["stateless"] is True
    assert captured["tools_enabled"] is False and captured["memory_write_enabled"] is False
    assert captured["tts_enabled"] is False and captured["attachments_enabled"] is False
    assert captured["metadata"]["channel"] == "game_observer"
    await core.close()


def test_sanitizer_removes_reasoning_markdown_and_prefix():
    sanitizer = ReactionResponseSanitizer(80)
    assert sanitizer.sanitize("<think>secret</think>```text\nОтвет: Держись, Ви.\n```") == "Держись, Ви."
    assert sanitizer.sanitize("Сиена: Здоровье критическое.") == "Здоровье критическое."
    assert sanitizer.sanitize("Reaction: Assistant: Спокойно.") == "Спокойно."
    assert sanitizer.sanitize("Сиена думает, что бой закончился.") == "Сиена думает, что бой закончился."


def test_sanitizer_rejects_empty_and_json_and_preserves_words():
    sanitizer = ReactionResponseSanitizer(20)
    with pytest.raises(InvalidReactionResponse):
        sanitizer.sanitize("<think>only reasoning</think>")
    with pytest.raises(InvalidReactionResponse):
        sanitizer.sanitize('{"text":"technical"}')
    value = sanitizer.sanitize("Очень длинная естественная игровая реплика без обрыва")
    assert len(value) <= 20 and not value.endswith("естеств")


@pytest.mark.asyncio
async def test_empty_or_malformed_response_is_provider_failure():
    for payload in (response(""), {"wrong": "shape"}):
        core = provider(lambda request, payload=payload: httpx.Response(200, json=payload), max_retries=0)
        with pytest.raises(SienaCoreProviderError):
            await core.generate_reaction(reaction_request(), NOW)
        await core.close()


@pytest.mark.asyncio
async def test_http_500_retries_but_http_4xx_does_not():
    attempts = {"500": 0, "401": 0}

    def five_hundred(request):
        attempts["500"] += 1
        return httpx.Response(500)

    async def no_sleep(seconds):
        return None

    core = provider(five_hundred, max_retries=1, sleep=no_sleep)
    with pytest.raises(SienaCoreProviderError):
        await core.generate_reaction(reaction_request(), NOW)
    assert attempts["500"] == 2
    await core.close()

    def unauthorized(request):
        attempts["401"] += 1
        return httpx.Response(401)

    core = provider(unauthorized, max_retries=1, sleep=no_sleep)
    with pytest.raises(SienaCoreProviderError) as exc:
        await core.generate_reaction(reaction_request(), NOW)
    assert attempts["401"] == 1 and exc.value.category == "authentication_error"
    await core.close()


@pytest.mark.asyncio
async def test_timeout_uses_fallback_failure_path():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    core = provider(timeout, max_retries=0)
    with pytest.raises(SienaCoreProviderError) as exc:
        await core.generate_reaction(reaction_request(), NOW)
    assert exc.value.transient and core.reachable is False
    await core.close()


def test_circuit_closed_open_half_open_and_recovery():
    current = [NOW]
    circuit = CircuitBreaker(2, 30, lambda: current[0])
    circuit.allow_request(); circuit.record_failure()
    circuit.allow_request(); circuit.record_failure()
    assert circuit.status().state == "open"
    with pytest.raises(CircuitOpenError):
        circuit.allow_request()
    current[0] += timedelta(seconds=30)
    circuit.allow_request()
    assert circuit.status().state == "half_open"
    circuit.record_success()
    assert circuit.status().state == "closed" and circuit.status().consecutive_failures == 0


def test_half_open_failure_reopens_circuit():
    current = [NOW]
    circuit = CircuitBreaker(1, 10, lambda: current[0])
    circuit.record_failure(); current[0] += timedelta(seconds=10)
    circuit.allow_request(); circuit.record_failure()
    assert circuit.status().state == "open"


@pytest.mark.asyncio
async def test_open_circuit_makes_no_http_request():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    core = provider(handler, max_retries=0, failure_threshold=1)
    with pytest.raises(SienaCoreProviderError):
        await core.generate_reaction(reaction_request(), NOW)
    with pytest.raises(SienaCoreProviderError) as exc:
        await core.generate_reaction(reaction_request(), NOW)
    assert exc.value.category == "circuit_open" and calls == 1
    await core.close()


@pytest.mark.asyncio
async def test_priority_queue_is_bounded_and_critical_evicts_low():
    queue = ReactionPriorityQueue(2)
    low1 = reaction_request(event(EventType.PLAYER_IDLE, EventPriority.P3_LOW))
    low2 = ReactionRequest(request_id="low-2", event=event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM), queued_at=NOW)
    critical = ReactionRequest(request_id="critical", event=event(EventType.HEALTH_CRITICAL, EventPriority.P0_CRITICAL), queued_at=NOW)
    assert await queue.put(low1) and await queue.put(low2) and await queue.put(critical)
    assert queue.qsize() == 2 and (await queue.get()).request_id == "critical"


@pytest.mark.asyncio
async def test_priority_queue_rejects_new_low_when_full_of_higher_priority():
    queue = ReactionPriorityQueue(2)
    await queue.put(ReactionRequest(request_id="critical", event=event(EventType.HEALTH_CRITICAL, EventPriority.P0_CRITICAL)))
    await queue.put(ReactionRequest(request_id="high", event=event()))
    assert not await queue.put(ReactionRequest(request_id="low", event=event(EventType.PLAYER_IDLE, EventPriority.P3_LOW)))


@pytest.mark.asyncio
async def test_priority_queue_evicts_stale_low_before_other_work():
    queue = ReactionPriorityQueue(2)
    stale = ReactionRequest(request_id="stale-low", event=event(EventType.PLAYER_IDLE, EventPriority.P3_LOW))
    current = ReactionRequest(request_id="current-low", event=event(EventType.PLAYER_MOVED_AFTER_IDLE, EventPriority.P3_LOW))
    incoming = ReactionRequest(request_id="incoming-low", event=event(EventType.PLAYER_IDLE, EventPriority.P3_LOW))
    await queue.put(stale)
    await queue.put(current)

    assert await queue.put(incoming, lambda request: request.request_id == "stale-low")
    remaining = {(await queue.get()).request_id, (await queue.get()).request_id}
    assert remaining == {"current-low", "incoming-low"}


def dispatch(core, *, fallback="template", clock=lambda: NOW):
    bus = EventBus()
    planner = ReactionPlanner(DeferredSienaCoreProvider(), general_cooldown_seconds=0, same_event_cooldown_seconds=0)
    service = ReactionDispatchService(
        configured_provider="siena_core", planner=planner, bus=bus, core_provider=core,
        fallback_provider=fallback, queue_capacity=4, recent_events_limit=6,
        recent_reactions_limit=5,
        max_event_age_seconds=45, language="ru", clock=clock,
    )
    return service, bus


@pytest.mark.asyncio
async def test_dispatch_is_nonblocking_while_provider_is_slow():
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set(); await release.wait()
        return httpx.Response(200, json=response())

    core = provider(handler)
    service, _ = dispatch(core)
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    await asyncio.wait_for(entered.wait(), 1)
    # Event handler returned while the only worker is still waiting on Siena.
    assert service._worker and not service._worker.done()
    release.set()
    await service.stop()
    assert service._worker is None


@pytest.mark.asyncio
async def test_worker_continues_after_unexpected_provider_exception():
    core = provider(lambda request: httpx.Response(200, json=response()))
    calls = 0

    async def generate(request, session_started_at=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected fake failure")
        return ProviderResult("Держись.", "fake-siena", 10, request.request_id)

    core.generate_reaction = generate
    service, bus = dispatch(core)
    queue = await bus.connect()
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    await service.handle_event(event(EventType.HEALTH_LOW, EventPriority.P1_HIGH, created_at=NOW + timedelta(seconds=1)))
    found = False
    for _ in range(10):
        message = await asyncio.wait_for(queue.get(), 1)
        if message["type"] == "siena_reaction":
            found = True
            break
    assert found and calls == 2 and service._worker and not service._worker.done()
    await service.stop()


@pytest.mark.asyncio
async def test_generation_lifecycle_publishes_queued_generating_completed():
    core = provider(lambda request: httpx.Response(200, json=response()))
    service, bus = dispatch(core)
    queue = await bus.connect()
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    states = []
    for _ in range(12):
        message = await asyncio.wait_for(queue.get(), 1)
        if message["type"] == "reaction_generation_status":
            assert message["payload"] == message["data"]
            states.append(message["data"]["state"])
            assert "exception" not in message["data"] and "prompt" not in message["data"]
            if message["data"]["state"] == "completed":
                break
    assert states == ["queued", "generating", "completed"]
    await service.stop()


@pytest.mark.asyncio
async def test_recent_published_reactions_are_bounded_and_reset_by_session():
    core = provider(lambda request: httpx.Response(200, json=response()))
    service, _ = dispatch(core)
    for index in range(7):
        service._remember_reaction(SienaReaction(
            event_id=f"event-{index}", session_id="old", event_type=EventType.HEALTH_LOW,
            text=f"Реплика {index}", priority=ReactionPriority.HIGH, provider="siena_core",
        ))
    assert [item.text for item in service._recent_reactions["old"]] == [f"Реплика {index}" for index in range(2, 7)]
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM, session="new"))
    assert list(service._recent_reactions) == ["new"] and list(service._recent_reactions["new"]) == []
    await core.close()


@pytest.mark.asyncio
async def test_provider_and_event_bus_preserve_exact_russian_unicode():
    exact = "Здоровье критическое. Найди укрытие."
    core = provider(lambda request: httpx.Response(200, json=response(exact)))
    result = await core.generate_reaction(reaction_request(), NOW)
    assert result.text == exact
    reaction = SienaReaction(
        event_id="utf8-event", session_id="game-1", event_type=EventType.HEALTH_CRITICAL,
        text=result.text, priority=ReactionPriority.CRITICAL, provider="siena_core",
    )
    bus = EventBus()
    queue = await bus.connect()
    await bus.publish("siena_reaction", reaction)
    assert bus.reactions(1)[0].text == exact
    assert (await queue.get())["payload"]["text"] == exact
    await core.close()


def test_rest_and_websocket_preserve_exact_template_unicode(state_factory):
    exact = "Здоровье критическое. Найди укрытие."
    settings = Settings(general_reaction_cooldown_seconds=0, same_event_cooldown_seconds=0)
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "status"
            client.post("/api/v1/telemetry/state", json=state_factory(1, player__health=9).model_dump(mode="json"))
            websocket_text = None
            for _ in range(20):
                message = socket.receive_json()
                if message["type"] == "siena_reaction" and message["payload"]["event_type"] == "health_critical":
                    websocket_text = message["payload"]["text"]
                    break
            assert websocket_text == exact
        rest = client.get("/api/v1/reactions", params={"limit": 20}).json()
        assert next(item["text"] for item in rest if item["event_type"] == "health_critical") == exact


@pytest.mark.asyncio
async def test_session_change_drops_queued_noncritical_requests():
    queue = ReactionPriorityQueue(4)
    await queue.put(ReactionRequest(request_id="old-low", event=event(session="old")))
    await queue.put(ReactionRequest(request_id="old-idle", event=event(EventType.PLAYER_IDLE, EventPriority.P3_LOW, session="old")))
    await queue.put(ReactionRequest(request_id="new", event=event(session="new")))
    dropped = await queue.keep_session("new")
    assert dropped == 2 and queue.qsize() == 1 and (await queue.get()).request_id == "new"


@pytest.mark.asyncio
async def test_session_change_logs_and_drops_old_critical_request():
    queue = ReactionPriorityQueue(2)
    await queue.put(ReactionRequest(request_id="old-critical", event=event(EventType.HEALTH_CRITICAL, EventPriority.P0_CRITICAL, session="old")))
    assert await queue.keep_session("new") == 1
    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_template_fallback_is_explicitly_marked():
    core = provider(lambda request: httpx.Response(500), max_retries=0)
    service, bus = dispatch(core)
    queue = await bus.connect()
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    reaction = None
    states = []
    for _ in range(10):
        message = await asyncio.wait_for(queue.get(), 1)
        if message["type"] == "siena_reaction":
            reaction = message["data"]
        if message["type"] == "reaction_generation_status":
            states.append(message["data"]["state"])
            if message["data"]["state"] == "fallback":
                break
    assert reaction and reaction["provider"] == "template_fallback" and reaction["fallback_used"] is True
    assert reaction["metadata"]["actual_provider"] == "template"
    assert states == ["queued", "generating", "fallback"]
    await service.stop()


@pytest.mark.asyncio
async def test_disabled_fallback_creates_no_reaction():
    core = provider(lambda request: httpx.Response(500), max_retries=0)
    service, bus = dispatch(core, fallback="disabled")
    queue = await bus.connect()
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    states = []
    for _ in range(10):
        message = await asyncio.wait_for(queue.get(), 1)
        if message["type"] == "reaction_generation_status":
            states.append(message["data"]["state"])
            if message["data"]["state"] == "failed":
                break
    assert bus.reactions() == []
    assert states == ["queued", "generating", "failed"]
    await service.stop()


@pytest.mark.asyncio
async def test_late_reaction_after_session_end_is_suppressed():
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set(); await release.wait()
        return httpx.Response(200, json=response())

    core = provider(handler)
    service, bus = dispatch(core)
    queue = await bus.connect()
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    await asyncio.wait_for(entered.wait(), 1)
    await service.handle_event(event(EventType.SESSION_ENDED, EventPriority.P2_MEDIUM, created_at=NOW + timedelta(seconds=1)))
    release.set()
    states = []
    for _ in range(8):
        message = await asyncio.wait_for(queue.get(), 1)
        if message["type"] == "reaction_generation_status":
            states.append(message["data"]["state"])
            if message["data"]["state"] == "suppressed":
                break
    assert bus.reactions() == [] and service.stale_suppressed_count == 1
    assert states[-1] == "suppressed"
    await service.stop()


@pytest.mark.asyncio
async def test_old_low_event_is_stale_before_http_call():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response())
    core = provider(handler)
    service, bus = dispatch(core, clock=lambda: NOW + timedelta(seconds=60))
    await service.start()
    await service.handle_event(event(EventType.SESSION_STARTED, EventPriority.P2_MEDIUM))
    await asyncio.sleep(0)
    assert calls == 0 and bus.reactions() == []
    await service.stop()


def test_provider_status_endpoint_is_valid_without_siena():
    settings = Settings(reaction_provider="siena_core", siena_core_enabled=False, siena_core_base_url="")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/reaction-provider/status")
        assert response.status_code == 200
        body = response.json()
        assert body["configured_provider"] == "siena_core"
        assert body["active_provider"] == "configuration_error" and body["worker_running"] is True


def test_websocket_publishes_provider_status_change(state_factory):
    settings = Settings(
        reaction_provider="siena_core", siena_core_enabled=False, siena_core_base_url="",
        general_reaction_cooldown_seconds=0, same_event_cooldown_seconds=0,
    )
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws") as socket:
            assert socket.receive_json()["type"] == "status"
            client.post("/api/v1/telemetry/state", json=state_factory(1).model_dump(mode="json"))
            found = False
            for _ in range(30):
                message = socket.receive_json()
                if message["type"] == "reaction_provider_status":
                    found = True
                    assert "payload" in message
                    break
            assert found
