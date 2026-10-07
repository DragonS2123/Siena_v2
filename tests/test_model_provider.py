from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

import config
from core.errors import SienaInfraError, SienaTimeoutError
from core.message import ToolResult, tool_message
from core.model_provider import ModelProvider
from core.provider_factory import create_provider, ProviderCatalog
from core.providers.llama_cpp_provider import LlamaCppConfig, LlamaCppProvider
from core.providers.ollama_provider import OllamaProvider
from core.runtime_settings import RuntimeSettingsService, SettingsValidationError
from core.streaming_chat import with_stream_timeouts
from storage.settings_store import SettingsStore


MODEL = "gemma-4-26B-A4B-it"
SETTINGS = LlamaCppConfig("http://127.0.0.1:18084", MODEL, "gemma4-16k", 16384,
                         output_tokens=128)
MESSAGES = [{"role": "system", "content": "Отвечай по-русски."},
            {"role": "user", "content": "Привет"}]
TOOLS = [{"type": "function", "function": {
    "name": name, "description": name,
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}} for name in ("short_memory_search", "long_memory_search")]


def completion(content="Ответ", calls=None, reason="stop"):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"model": MODEL, "choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 5},
            "timings": {"prompt_ms": 4, "predicted_ms": 10}}


def call(name="short_memory_search", arguments='{"query":"Сиена"}', call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def chunk(delta, finish=None):
    return {"model": MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def sse(*events, done=True):
    value = ": keepalive\r\n\r\n" + "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n" for event in events)
    return (value + ("data: [DONE]\r\n\r\n" if done else "")).encode()


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, data, *, wait=False):
        self.data, self.wait, self.closed = data, wait, False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        # Exercise arbitrary byte boundaries, including UTF-8 and SSE delimiters.
        for index in range(0, len(self.data), 7):
            yield self.data[index:index + 7]
        if self.wait:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def provider(handler):
    transport = httpx.MockTransport(handler)
    return LlamaCppProvider(SETTINGS, transport=transport, async_transport=transport)


def collect(source):
    async def run():
        return [item async for item in source]
    return asyncio.run(run())


def test_protocol_and_normalized_chat_generate():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=completion())
    client = provider(handle)
    assert isinstance(client, ModelProvider)
    assert isinstance(OllamaProvider("http://legacy", "old", 10), ModelProvider)
    response = client.chat(MESSAGES)
    assert response["message"] == {"role": "assistant", "content": "Ответ"}
    assert response["done"] is True and response["done_reason"] == "stop"
    assert response["eval_count"] == 5 and response["prompt_eval_count"] == 17
    assert response["eval_duration"] == 10_000_000
    assert response["prompt_eval_duration"] == 4_000_000
    client.generate("Запрос", system="Система")
    body = json.loads(requests[-1].content)
    assert requests[-1].url == "http://127.0.0.1:18084/v1/chat/completions"
    assert body["messages"] == [{"role": "system", "content": "Система"}, {"role": "user", "content": "Запрос"}]
    assert body["model"] == MODEL and body["max_tokens"] == 128
    assert "reasoning_effort" not in body and "num_ctx" not in body
    assert body["reasoning_budget_tokens"] == 512


def test_explicit_reasoning_off_preserves_request_override():
    def handle(request):
        assert json.loads(request.content)["reasoning_effort"] == "none"
        return httpx.Response(200, json=completion())
    client = LlamaCppProvider(replace(SETTINGS, reasoning=False), transport=httpx.MockTransport(handle))
    client.chat(MESSAGES)


@pytest.mark.parametrize("budget", [0, 512, 1024, 2048])
def test_reasoning_budget_reaches_chat_and_stream(budget):
    def handle(request):
        body = json.loads(request.content)
        assert body["reasoning_budget_tokens"] == budget
        assert "reasoning_effort" not in body
        if body["stream"]:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=sse(chunk({"content": "Ответ"}, "stop")))
        return httpx.Response(200, json=completion())
    client = LlamaCppProvider(replace(SETTINGS, reasoning_budget_tokens=budget),
                             transport=httpx.MockTransport(handle), async_transport=httpx.MockTransport(handle))
    client.chat(MESSAGES)
    async def consume():
        return [c async for c in client.stream_chat(MESSAGES)]
    assert asyncio.run(consume())[-1]["done"]


@pytest.mark.parametrize("budget", [-1, True, 1.5, "512", None, 65537])
def test_unrestricted_or_invalid_reasoning_budget_rejected(budget):
    with pytest.raises(ValueError, match="reasoning budget"):
        replace(SETTINGS, reasoning_budget_tokens=budget)


def test_reasoning_budget_settings_persist_without_changing_sampling(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    settings = RuntimeSettingsService(store)
    before = settings.current()
    settings.update({"llama_cpp_reasoning_budget_tokens": 1024})
    restored = RuntimeSettingsService(store).current()
    assert create_provider(restored).config.reasoning_budget_tokens == 1024
    for key in ("temperature", "top_p", "top_k", "seed", "chat_output_tokens"):
        assert restored.get(key) == before.get(key)
    with pytest.raises(SettingsValidationError):
        settings.update({"llama_cpp_reasoning_budget_tokens": -1})


@pytest.mark.parametrize("response_format", [
    {"type": "json_object"},
    {"type": "json_schema", "json_schema": {"name": "answer", "strict": True,
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}},
])
def test_structured_json(response_format):
    def handle(request):
        assert json.loads(request.content)["response_format"] == response_format
        return httpx.Response(200, json=completion('{"ok":true}'))
    assert json.loads(provider(handle).chat(MESSAGES, response_format=response_format)["message"]["content"]) == {"ok": True}


@pytest.mark.parametrize("choice,names,wire_choice", [
    ("auto", ["short_memory_search", "long_memory_search"], "auto"),
    ({"type": "function", "function": {"name": "long_memory_search"}}, ["long_memory_search"], "required"),
    ("required", ["short_memory_search", "long_memory_search"], "required"),
    ("none", [], None),
])
def test_tool_choice_workarounds_and_argument_normalization(choice, names, wire_choice):
    tools_before = copy.deepcopy(TOOLS)
    def handle(request):
        body = json.loads(request.content)
        assert [t["function"]["name"] for t in body.get("tools", [])] == names
        assert body.get("tool_choice") == wire_choice
        if not names:
            assert "tools" not in body and "tool_choice" not in body
            return httpx.Response(200, json=completion())
        return httpx.Response(200, json=completion(None, [call(names[0])], "tool_calls"))
    response = provider(handle).chat(MESSAGES, TOOLS, tool_choice=choice)
    if names:
        assert response["message"]["tool_calls"][0]["function"]["arguments"] == {"query": "Сиена"}
    assert TOOLS == tools_before


def test_history_tool_result_ids_preserved_without_mutation():
    history = [*MESSAGES, {"role": "assistant", "content": "", "tool_calls": [call(arguments={"query": "Сиена"})]},
               tool_message("short_memory_search", ToolResult(True, ["found"]), tool_call_id="call-1")]
    before = copy.deepcopy(history)
    def handle(request):
        messages = json.loads(request.content)["messages"]
        assert isinstance(messages[-2]["tool_calls"][0]["function"]["arguments"], str)
        assert messages[-1]["tool_call_id"] == "call-1"
        assert messages[-1]["name"] == "short_memory_search"
        assert "tool_name" not in messages[-1]
        return httpx.Response(200, json=completion())
    provider(handle).chat(history, TOOLS)
    assert history == before


@pytest.mark.parametrize("choice,tools", [
    ("required", []), ("unexpected", TOOLS), ([], TOOLS),
    ({"type": "function", "function": {"name": "absent"}}, TOOLS),
    ({"type": "function", "function": []}, TOOLS), ("auto", {}),
])
def test_invalid_tool_configuration_never_sends_request(choice, tools):
    def handle(_):
        pytest.fail("invalid tool selection reached the server")
    with pytest.raises(ValueError):
        provider(handle).chat(MESSAGES, tools, tool_choice=choice)


def test_stream_text_thinking_usage_and_close():
    stream = ByteStream(sse(chunk({"role": "assistant"}), chunk({"reasoning_content": "Мысль"}),
                            chunk({"content": "При"}), chunk({"content": "вет"}), chunk({}, "stop"),
                            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}))
    def handle(request):
        body = json.loads(request.content)
        assert body["stream"] and body["stream_options"]["include_usage"]
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    chunks = collect(provider(handle).stream_chat(MESSAGES))
    assert "".join(c["message"].get("thinking", "") for c in chunks) == "Мысль"
    assert "".join(c["message"]["content"] for c in chunks) == "Привет"
    assert sum(c["done"] for c in chunks) == 1
    assert chunks[-1]["eval_count"] == 2 and chunks[-1]["prompt_eval_count"] == 10
    assert chunks[-1]["message"]["content"] == "" and stream.closed


def test_stream_assembles_multiple_tools_once_and_json_format():
    events = [chunk({"tool_calls": [{"index": 0, **call(arguments='{"query":')},
                                    {"index": 1, **call("long_memory_search", '{"query":', "call-2") }]}),
              chunk({"tool_calls": [{"index": 1, "function": {"arguments": '"Б"}'}},
                                    {"index": 0, "function": {"arguments": '"А"}'}}]}), chunk({}, "tool_calls")]
    stream = ByteStream(sse(*events))
    def handle(request):
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    chunks = collect(provider(handle).stream_chat(MESSAGES, TOOLS, response_format={"type": "json_object"}))
    assert len(chunks) == 1 and chunks[0]["done"]
    assert [c["function"]["arguments"] for c in chunks[0]["message"]["tool_calls"]] == [{"query": "А"}, {"query": "Б"}]
    assert stream.closed


@pytest.mark.parametrize("error,expected", [(httpx.ReadTimeout, SienaTimeoutError),
    (httpx.ConnectTimeout, SienaTimeoutError), (httpx.ConnectError, SienaInfraError)])
@pytest.mark.parametrize("stream", [False, True])
def test_transport_errors(error, expected, stream):
    def handle(request):
        raise error("private request details", request=request)
    with pytest.raises(expected) as raised:
        client = provider(handle)
        collect(client.stream_chat(MESSAGES)) if stream else client.chat(MESSAGES)
    assert "private request details" not in str(raised.value)


@pytest.mark.parametrize("status", [400, 503])
def test_http_errors(status):
    with pytest.raises(SienaInfraError, match=f"HTTP {status}"):
        provider(lambda _: httpx.Response(status, text="private")).chat(MESSAGES)


@pytest.mark.parametrize("invalid", [None, [], {}, {"choices": []},
    {"choices": [{"message": {"role": "user", "content": "x"}, "finish_reason": "stop"}]},
    {**completion(), "usage": []}, {**completion(), "usage": {"completion_tokens": -1}},
    {**completion(), "timings": {"predicted_ms": float("nan")}},
    {**completion(), "model": {}},
    completion(calls=[call(arguments="broken")], reason="tool_calls"),
    completion(content=["not text"]), completion(reason=[]),
])
def test_malformed_completion(invalid):
    with pytest.raises(SienaInfraError, match="malformed"):
        provider(lambda _: httpx.Response(200, json=invalid) if invalid is not None else httpx.Response(200, text="null")).chat(MESSAGES)


@pytest.mark.parametrize("data,content_type", [
    (b"broken", "application/json"), (b"data: broken\n\ndata: [DONE]\n\n", "text/event-stream"),
    (sse(chunk({"content": "partial"}), done=False), "text/event-stream"),
    (sse(chunk({"content": []}, "stop")), "text/event-stream"),
    (sse(chunk({}, None)), "text/event-stream"),
    (sse(chunk({"tool_calls": [{"index": 0, **call(arguments="broken")}]}, "tool_calls")), "text/event-stream"),
])
def test_malformed_or_truncated_sse_closes_connection(data, content_type):
    stream = ByteStream(data)
    with pytest.raises(SienaInfraError, match="malformed"):
        collect(provider(lambda _: httpx.Response(200, stream=stream, headers={"content-type": content_type})).stream_chat(MESSAGES))
    assert stream.closed


def test_cancellation_and_iterator_close_release_connection():
    async def run(cancel):
        stream = ByteStream(sse(chunk({"content": "partial"}), done=False), wait=True)
        client = provider(lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
        source = client.stream_chat(MESSAGES)
        assert (await anext(source))["message"]["content"] == "partial"
        if cancel:
            task = asyncio.create_task(anext(source))
            await stream.waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await source.aclose()
        assert stream.closed
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_first_token_timeout_closes_underlying_http_stream():
    async def run():
        stream = ByteStream(b"", wait=True)
        source = provider(lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})).stream_chat(MESSAGES)
        wrapped = with_stream_timeouts(source, first_token_timeout=0.01, idle_timeout=0.01,
                                       hard_deadline=asyncio.get_running_loop().time() + 1)
        with pytest.raises(SienaTimeoutError):
            await anext(wrapped)
        assert stream.closed
    asyncio.run(run())


def test_health_catalog_profiles_and_missing_model():
    context = 16384
    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"data": [{"id": "gguf-file", "aliases": [MODEL], "meta": {"n_ctx": context}}]})
    client = provider(handle)
    assert client.health()["available"]
    assert client.diagnostics()["server_context_size"] == 16384
    assert {m["name"] for m in client.catalog()["models"]} == {"gguf-file", MODEL}
    long = LlamaCppProvider(replace(SETTINGS, profile="gemma4-32k", context_size=32768), transport=httpx.MockTransport(handle))
    assert not long.health()["available"]
    context = 32768
    assert long.health()["available"]
    assert long.diagnostics()["managed"] is False
    missing = LlamaCppProvider(replace(SETTINGS, model="absent"), transport=httpx.MockTransport(handle))
    assert not missing.health()["available"]


def test_factory_defaults_config_profiles_and_legacy_migration(tmp_path):
    settings = RuntimeSettingsService(SettingsStore(tmp_path / "settings.json"))
    assert settings.current().get("inference_provider") == config.DEFAULT_INFERENCE_PROVIDER
    settings.update({"inference_provider": "llama_cpp", "llama_cpp_url": "http://localhost:18085",
                     "llama_cpp_model": "custom-alias"})
    first = create_provider(settings.current())
    settings.update({"llama_cpp_profile": "gemma4-32k"})
    second = create_provider(settings.current())
    assert (first.num_ctx, second.num_ctx) == (16384, 32768)
    assert first.config.model_path == second.config.model_path
    assert second.config.url == "http://localhost:18085" and second._model == "custom-alias"
    with pytest.raises(SettingsValidationError, match="context_size"):
        settings.update({"context_size": 65536})
    for patch in ({"inference_provider": []}, {"llama_cpp_profile": []}, {"llama_cpp_url": "http://bad:bad"}):
        with pytest.raises(SettingsValidationError):
            settings.update(patch)
    settings.update({"inference_provider": "ollama"})
    assert isinstance(create_provider(settings.current()), OllamaProvider)
    assert settings.current().get("context_size") == config.OLLAMA_NUM_CTX
    old_file = tmp_path / "legacy.json"
    old_file.write_text(json.dumps({"ollama_host": "http://old:11434", "context_size": 128000,
                                    "model_roles": config.DEFAULT_MODEL_ROLES}))
    legacy = RuntimeSettingsService(SettingsStore(old_file)).current()
    assert legacy.get("inference_provider") == "ollama" and legacy.get("context_size") == 128000
    assert legacy.get("model_roles")["chat"] == "qwen3.5:9b"


def test_legacy_adapter_forwards_original_chat(monkeypatch):
    observed = {}
    def chat(self, messages, tools=None, model=None):
        observed.update(model=self._model, messages=messages, tools=tools)
        return {"message": {"content": "legacy"}}
    monkeypatch.setattr("core.ollama_client.OllamaClient.chat", chat)
    client = OllamaProvider("http://legacy", "old", 10, num_ctx=32768, num_predict=99)
    assert client.chat(MESSAGES, TOOLS)["message"]["content"] == "legacy"
    assert observed == {"model": "old", "messages": MESSAGES, "tools": TOOLS}
    assert (client.num_ctx, client.num_predict) == (32768, 99)


def test_explicit_llama_settings_migration_keeps_native_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"inference_provider": "llama_cpp", "llama_cpp_model": "gemma-alias"}))
    store = SettingsStore(path)
    store.migrate()
    settings = RuntimeSettingsService(store)
    assert settings.current().get("model_roles")["chat"] == "gemma-alias"
    assert settings.current().get("context_size") == 16384
    settings.update({"inference_provider": "ollama", "context_size": 128000})
    settings.update({"inference_provider": "ollama"})
    settings.update({"llama_cpp_profile": "gemma4-32k"})
    assert settings.current().get("context_size") == 128000


def configure_llama(runtime):
    runtime.settings.update({"inference_provider": "llama_cpp", "llama_cpp_url": SETTINGS.url,
                             "llama_cpp_managed": False,
                             "llama_cpp_model": MODEL, "model_roles": config.inference_model_roles("llama_cpp", MODEL),
                             "auto_continue_enabled": False})


def test_siena_chat_agent_loop_uses_provider_and_tool_result(client, monkeypatch):
    runtime = client.app.state.runtime
    configure_llama(runtime)
    bodies = []
    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": MODEL, "meta": {"n_ctx": 16384}}]})
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=completion(None, [call(arguments='{"query":"test"}')], "tool_calls"))
        assert body["messages"][-1]["tool_call_id"] == "call-1"
        return httpx.Response(200, json=completion("Память проверена."))
    monkeypatch.setattr(LlamaCppProvider, "_client", lambda self, **kwargs: httpx.Client(base_url=SETTINGS.url, transport=httpx.MockTransport(handle)))
    conversation = client.post("/api/conversations", json={}).json()["conversation_id"]
    response = client.post("/api/chat", json={"conversation_id": conversation, "message": "Проверь память"})
    assert response.status_code == 200 and response.json()["answer"] == "Память проверена."
    assert len(bodies) == 2
    status = client.get("/api/runtime/status").json()
    assert status["inference"]["provider"] == "llama_cpp" and status["inference"]["available"]
    assert status["ollama"]["active"] is False and status["ollama"]["available"] is None


def test_siena_stream_disconnect_closes_provider_socket(client, monkeypatch):
    runtime = client.app.state.runtime
    configure_llama(runtime)
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": MODEL}]})
    stream = ByteStream(sse(chunk({"content": "Часть ответа"}), done=False), wait=True)
    monkeypatch.setattr(LlamaCppProvider, "_async_client", lambda self: httpx.AsyncClient(base_url=SETTINGS.url,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))))
    conversation = client.post("/api/conversations", json={}).json()["conversation_id"]
    async def run():
        source = runtime.chat.stream_turn(conversation, "Привет")
        assert (await anext(source))["type"] == "generation.started"
        assert (await anext(source))["type"] == "assistant.content.delta"
        await source.aclose()
    asyncio.run(run())
    assert stream.closed
    stored = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
    assert stored["content"] == "Часть ответа" and stored["metadata"]["status"] == "cancelled"


def test_siena_stream_tool_loop_receives_complete_arguments(client, monkeypatch):
    runtime = client.app.state.runtime
    configure_llama(runtime)
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": MODEL}]})
    bodies, streams = [], []
    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            data = sse(chunk({"tool_calls": [{"index": 0, **call(arguments='{"query":')}]}),
                       chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"test"}'}}]}),
                       chunk({}, "tool_calls"))
        else:
            assert body["messages"][-1]["tool_call_id"] == "call-1"
            data = sse(chunk({"content": "Проверено."}), chunk({}, "stop"))
        stream = ByteStream(data)
        streams.append(stream)
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(LlamaCppProvider, "_async_client", lambda self: httpx.AsyncClient(
        base_url=SETTINGS.url, transport=httpx.MockTransport(handle)))
    conversation = client.post("/api/conversations", json={}).json()["conversation_id"]
    events = collect(runtime.chat.stream_turn(conversation, "Проверь память"))
    assert events[-1]["type"] == "generation.completed"
    assert "".join(e["delta"] for e in events if e["type"] == "assistant.content.delta") == "Проверено."
    assert len(bodies) == 2 and all(s.closed for s in streams)


@pytest.mark.parametrize("metadata", [{"data": [{"id": MODEL, "aliases": "invalid"}]},
    {"data": [{"id": MODEL, "meta": {"n_ctx": "16384"}}]}, {"data": [{}]}, []])
def test_malformed_model_catalog_reports_unavailable(metadata):
    def handle(request):
        return httpx.Response(200, json={"status": "ok"} if request.url.path == "/health" else metadata)
    health = provider(handle).health()
    assert not health["available"] and "malformed" in health["error"]
