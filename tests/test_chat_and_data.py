from __future__ import annotations

import sqlite3

from core.data_migration import migrate_conversations


def test_conversation_crud_and_override(client, monkeypatch):
    created = client.post("/api/conversations", json={"title": "Test"}).json()
    conversation_id = created["conversation_id"]
    runtime = client.app.state.runtime
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "local:1"}]})
    updated = client.patch(f"/api/conversations/{conversation_id}", json={"model_override": "local:1"})
    assert updated.status_code == 200
    assert updated.json()["model_override"] == "local:1"
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 200


def test_chat_turn_persists_messages(client, monkeypatch):
    runtime = client.app.state.runtime
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]})
    monkeypatch.setattr("core.chat_service.run_agent_loop", lambda *_args, **_kwargs: "answer")
    response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "hello"})
    assert response.status_code == 200
    conversation = client.get(f"/api/conversations/{conversation_id}").json()
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    assert conversation["messages"][0]["metadata"]["status"] == "completed"


def test_memory_and_insights_use_temporary_stores(client):
    saved = client.post("/api/memory/long", json={"text": "remembered", "importance": "high"})
    assert saved.status_code == 200
    assert client.get("/api/memory/long").json()["entries"][0]["text"] == "remembered"
    assert client.get("/api/insights").status_code == 200


def test_conversation_migration_is_idempotent_and_backed_up(tmp_path):
    database = tmp_path / "conversations.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT)")
        connection.execute("CREATE TABLE remote_conversation_links (id TEXT)")
    assert migrate_conversations(database) is True
    assert database.with_suffix(".pre-core-cleanup.bak").exists()
    assert migrate_conversations(database) is False
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_conversation_links'"
        ).fetchone() is None

def test_large_length_limited_answer_round_trips_exactly(client, monkeypatch):
    from core.agent_loop import AgentResult

    runtime = client.app.state.runtime
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]})
    code = "```html\n" + "\n".join(f"<div>строка {index}</div>" for index in range(500))
    provider_result = AgentResult(
        content=code,
        done=True,
        done_reason="length",
        eval_count=4096,
        prompt_eval_count=321,
        total_duration=123456,
        configured_num_predict=4096,
    )
    monkeypatch.setattr("core.chat_service.run_agent_loop", lambda *_args, **_kwargs: provider_result)

    response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Создай HTML код"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == code
    assert payload["done_reason"] == "length"
    assert payload["incomplete"] is True
    conversation = client.get(f"/api/conversations/{conversation_id}").json()
    stored = conversation["messages"][-1]
    assert stored["content"] == code
    assert stored["metadata"]["eval_count"] == 4096
    assert stored["metadata"]["configured_num_predict"] == 4096
    assert stored["metadata"]["incomplete"] is True


def test_code_requests_use_persisted_separate_generation_budget(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.settings.save({
        "num_ctx": 32768,
        "num_predict": 1024,
        "code_num_predict": 4096,
        "request_timeout_seconds": 60,
        "code_request_timeout_seconds": 300,
        "max_context_messages": 45,
    })
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]})
    captured = {}

    def fake_run(_session, ollama_client, _registry, _logger, _iterations, max_messages):
        captured.update(num_ctx=ollama_client.num_ctx, num_predict=ollama_client.num_predict, max_messages=max_messages)
        return "ok"

    monkeypatch.setattr("core.chat_service.run_agent_loop", fake_run)
    response = client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Напиши HTML код страницы"})
    assert response.status_code == 200
    assert captured == {"num_ctx": 32768, "num_predict": 4096, "max_messages": 45}


def test_timeout_is_distinct_from_length_completion(monkeypatch):
    import pytest

    from core.agent_loop import AgentResult
    from core.errors import SienaTimeoutError
    from core.ollama_client import OllamaClient

    client = OllamaClient("http://127.0.0.1:11434", "local:test", 3, num_ctx=4096, num_predict=256)

    class TimedOutTransport:
        def chat(self, **_kwargs):
            raise TimeoutError("read timed out")

    client._client = TimedOutTransport()
    with pytest.raises(SienaTimeoutError):
        client.chat([{"role": "user", "content": "hello"}])

    limited = AgentResult(content="partial", done=True, done_reason="length", eval_count=256)
    assert limited.incomplete is True
    assert limited.timeout is False
