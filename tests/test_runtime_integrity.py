from __future__ import annotations

import asyncio
import json

import pytest

from core.errors import SienaInfraError
from core.model_router import resolve_model
from core.runtime_settings import RuntimeSettingsService, SettingsValidationError
from core.streaming_chat import seam_merge, structure_issues
from storage.settings_store import SettingsStore


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _chunk(content: str = "", *, reason: str = "stop", eval_count: int = 10) -> dict:
    return {
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": reason,
        "eval_count": eval_count,
        "prompt_eval_count": 2,
        "total_duration": 3,
    }


def test_runtime_settings_revision_persists_and_failed_write_does_not_apply(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    service = RuntimeSettingsService(SettingsStore(path))
    first = service.update({"chat_output_tokens": 3072, "temperature": 0.4})
    assert first.snapshot.revision == 1
    assert set(first.applied) == {"chat_output_tokens", "temperature"}
    restarted = RuntimeSettingsService(SettingsStore(path))
    assert restarted.current().revision == 1
    assert restarted.current().get("chat_output_tokens") == 3072
    before = restarted.current()
    monkeypatch.setattr(restarted._store, "replace", lambda _values: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        restarted.update({"chat_output_tokens": 4096})
    assert restarted.current() is before
    assert restarted.current().get("chat_output_tokens") == 3072


def test_runtime_settings_reject_invalid_values(tmp_path):
    service = RuntimeSettingsService(SettingsStore(tmp_path / "settings.json"))
    with pytest.raises(SettingsValidationError, match="context_size"):
        service.update({"context_size": 128})
    with pytest.raises(SettingsValidationError, match="unknown settings"):
        service.update({"decorative_only": True})


def test_model_router_precedence_and_deep_is_never_automatic():
    roles = {role: f"{role}:model" for role in ("chat", "deep", "coder", "reviewer", "memory", "ocr", "vision", "embedding")}
    coding = resolve_model(text="Создай Python-скрипт", attachments=[], explicit_mode="auto", request_override=None, conversation_override=None, roles=roles, code_auto_enabled=True, settings_revision=7)
    assert (coding.requested_role, coding.resolved_model, coding.selection_reason) == ("coder", "coder:model", "coding_intent")
    normal = resolve_model(text="Подумай подробно", attachments=[], explicit_mode="auto", request_override=None, conversation_override=None, roles=roles, code_auto_enabled=True, settings_revision=7)
    assert normal.requested_role == "chat"
    deep = resolve_model(text="hello", attachments=[], explicit_mode="deep", request_override=None, conversation_override=None, roles=roles, code_auto_enabled=True, settings_revision=7)
    assert (deep.requested_role, deep.resolved_model) == ("deep", "deep:model")
    override = resolve_model(text="Создай код", attachments=[], explicit_mode="auto", request_override=None, conversation_override="conversation:model", roles=roles, code_auto_enabled=True, settings_revision=7)
    assert override.resolved_model == "conversation:model" and override.selection_reason == "conversation_override"


def test_next_requests_use_live_chat_deep_and_coder_assignments(client, monkeypatch):
    runtime = client.app.state.runtime
    models = [{"name": name} for name in ("chat:new", "deep:new", "coder:new")]
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": models})
    for role, model in (("chat", "chat:new"), ("deep", "deep:new"), ("coder", "coder:new")):
        assert client.put(f"/api/models/roles/{role}", json={"model": model}).status_code == 200
    observed: list[str] = []

    def fake_run(_session, ollama_client, *_args, **_kwargs):
        observed.append(ollama_client._model)
        return "ok"

    monkeypatch.setattr("core.chat_service.run_agent_loop", fake_run)
    for message, mode in (("hello", "auto"), ("hello", "deep"), ("Создай Python-скрипт", "auto")):
        conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
        response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": message, "mode": mode})
        assert response.status_code == 200
    assert observed == ["chat:new", "deep:new", "coder:new"]


def test_reviewer_pipeline_uses_assigned_reviewer_and_persists_trace(client, monkeypatch):
    runtime = client.app.state.runtime
    catalog = {"available": True, "models": [{"name": "coder:new"}, {"name": "reviewer:new"}]}
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: catalog)
    assert client.put("/api/models/roles/coder", json={"model": "coder:new"}).status_code == 200
    assert client.put("/api/models/roles/reviewer", json={"model": "reviewer:new"}).status_code == 200

    async def fake_stream(self, _messages, tools=None, model=None):
        assert self._model == "coder:new"
        yield _chunk("```html\n<!doctype html><html><body>ok</body></html>\n```")

    def fake_chat(self, _messages, tools=None, model=None):
        assert self._model == "reviewer:new"
        return {"model": "reviewer:new", "message": {"content": "APPROVED"}, "done_reason": "stop", "eval_count": 4}

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    monkeypatch.setattr("core.ollama_client.OllamaClient.chat", fake_chat)
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    events = _events(client.post("/api/chat/stream", json={"conversation_id": conversation_id, "message": "Создай HTML-файл", "review_code": True}))
    assert any(event["type"] == "generation.review.completed" and event["resolved_model"] == "reviewer:new" for event in events)
    stored = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["metadata"]["review"]["resolved_model"] == "reviewer:new"
    traces = client.get("/api/trace/recent?limit=200").json()["events"]
    assert any(event.get("requested_role") == "reviewer" and event.get("resolved_model") == "reviewer:new" for event in traces)


def test_ocr_vision_memory_and_embedding_roles_have_live_consumers(client, monkeypatch):
    runtime = client.app.state.runtime
    names = ["ocr:new", "vision:new", "memory:new", "embed:new"]
    catalog = {"available": True, "models": [{"name": name} for name in names]}
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: catalog)
    for role, model in (("ocr", "ocr:new"), ("vision", "vision:new"), ("memory", "memory:new"), ("embedding", "embed:new")):
        assert client.put(f"/api/models/roles/{role}", json={"model": model}).status_code == 200
    assert client.get("/api/ocr/status").json()["model"] == "ocr:new"
    assert client.get("/api/vision/status").json()["model"] == "vision:new"

    monkeypatch.setattr("core.ollama_client.OllamaClient.chat", lambda self, _messages, **_kwargs: {"model": self._model, "message": {"content": "memory result"}, "done_reason": "stop", "eval_count": 3})
    delegated = runtime.registry.dispatch("delegate_model", {"role": "memory", "task": "summarize"})
    assert delegated.ok is True and delegated.content["model"] == "memory:new"

    class EmbedResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"embeddings": [[1.0, 0.0, 0.5]]}
    captured = {}
    monkeypatch.setattr("memory.embedding_service.requests.post", lambda _url, json, timeout: captured.update(json) or EmbedResponse())
    vector = runtime.long_memory._embedding_service.encode("private input")
    assert captured["model"] == "embed:new" and vector == [1.0, 0.0, 0.5]


def test_three_continuations_append_to_one_message(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.settings.update({"auto_continue_max_rounds": 8, "auto_continue_max_total_tokens": 32768})
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen2.5-coder:7b"}]})
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        reason = "length" if calls < 4 else "stop"
        yield _chunk((f"part-{calls}-" * 12) + "\n", reason=reason, eval_count=100)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    events = _events(client.post("/api/chat/stream", json={"conversation_id": conversation_id, "message": "Создай код", "mode": "code"}))
    started = next(event for event in events if event["type"] == "generation.started")
    completed = events[-1]
    assert calls == 4 and completed["continuation_count"] == 3
    assert completed["assistant_message_id"] == started["assistant_message_id"]
    assistants = [item for item in client.get(f"/api/conversations/{conversation_id}").json()["messages"] if item["role"] == "assistant"]
    assert len(assistants) == 1 and all(f"part-{index}" in assistants[0]["content"] for index in range(1, 5))


def test_transient_pre_token_error_retries_with_backoff(client, monkeypatch):
    runtime = client.app.state.runtime
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]})
    attempts = 0

    async def flaky(_self, _messages, tools=None, model=None):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise SienaInfraError("temporary connection reset")
        yield _chunk("recovered")

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", flaky)
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    events = _events(client.post("/api/chat/stream", json={"conversation_id": conversation_id, "message": "hello", "mode": "chat"}))
    assert attempts == 3 and events[-1]["status"] == "completed"
    traces = client.get("/api/trace/recent?limit=200").json()["events"]
    assert len([event for event in traces if event.get("event") == "model.request.retrying"]) == 2


def test_overlap_normalizes_crlf_and_trailing_spaces_but_keeps_similar_new_code():
    merged, removed = seam_merge("alpha\nline with spaces\n", "line with spaces  \r\nnext\r\n")
    assert removed == len("line with spaces  \r\n") and merged == "next\r\n"
    similar, removed = seam_merge("value = 10\n", "value = 100\n")
    assert removed == 0 and similar == "value = 100\n"


def test_structure_validation_catches_file_damage():
    assert "open_html_tag:style" in structure_issues("<!doctype html><html><style>body {")
    assert any(issue.startswith("open_delimiters") for issue in structure_issues("```js\nfunction x() {\n```"))
    assert "content_after_html" in structure_issues("<!doctype html><html><body></body></html>trailing")
    assert "duplicate_doctype" in structure_issues("<!doctype html><html></html><!doctype html>")
    assert structure_issues("```json\n{\"ok\": true}\n```") == []


def test_diagnostics_exposes_effective_revision_and_model_calls(client):
    response = client.post("/api/settings", json={"chat_output_tokens": 3072})
    assert response.status_code == 200
    diagnostics = client.get("/api/diagnostics").json()
    assert diagnostics["effective_settings"]["chat_output_tokens"] == 3072
    assert diagnostics["settings_revision"] == response.json()["settings_revision"]
    assert isinstance(diagnostics["recent_model_calls"], list)

def test_restart_auto_resume_keeps_same_assistant_message_id(client, monkeypatch):
    runtime = client.app.state.runtime
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    runtime.conversations.append_message(conversation_id, "user", "Создай код")
    pending = runtime.conversations.append_message(
        conversation_id,
        "assistant",
        "first segment\n",
        model="qwen2.5-coder:7b",
        metadata={
            "status": "continuing",
            "incomplete": True,
            "requested_role": "coder",
            "resolved_model": "qwen2.5-coder:7b",
            "continuation_count": 0,
            "segment_count": 1,
            "eval_count_total": 10,
        },
    )
    record = next(item for item in runtime.conversations.list_active_stream_messages() if item["id"] == pending["id"])
    assert runtime.conversations.recover_interrupted_messages() == 1
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen2.5-coder:7b"}]})

    async def finish(_self, _messages, tools=None, model=None):
        yield _chunk("second segment\n", reason="stop", eval_count=5)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", finish)
    asyncio.run(runtime.chat.resume_interrupted_message(record))
    messages = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
    assistants = [message for message in messages if message["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["id"] == pending["id"]
    assert assistants[0]["content"] == "first segment\nsecond segment\n"
    assert assistants[0]["metadata"]["status"] == "completed"