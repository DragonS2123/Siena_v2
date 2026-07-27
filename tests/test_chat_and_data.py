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
