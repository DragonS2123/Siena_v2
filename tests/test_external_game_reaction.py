from fastapi.testclient import TestClient

import config
from api import server


def payload(**overrides):
    body = {
        "prompt": "Коротко отреагируй на низкое здоровье.",
        "request_id": "request-1",
        "language": "ru",
        "metadata": {
            "source": "cyberpunk_companion",
            "channel": "game_observer",
            "mode": "read_only_reaction",
            "game": "cyberpunk_2077",
            "game_session_id": "session-1",
            "event_id": "event-1",
            "request_id": "request-1",
        },
        "stateless": True,
        "tools_enabled": False,
        "memory_write_enabled": False,
        "tts_enabled": False,
        "attachments_enabled": False,
    }
    body.update(overrides)
    return body


def test_external_game_reaction_is_feature_flagged(monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_GAME_REACTIONS_ENABLED", False)
    with TestClient(server.app) as client:
        assert client.post("/api/external/game-reaction", json=payload()).status_code == 404


def test_external_game_reaction_is_stateless_and_tool_free(monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_GAME_REACTIONS_ENABLED", True)
    captured = {}

    def fake_chat(messages, tools=None, model=None):
        captured.update(messages=messages, tools=tools, model=model)
        return {"model": "qwen3.5:9b", "message": {"content": "Найди укрытие.", "thinking": "internal"}}

    monkeypatch.setattr(server.ollama_client, "chat", fake_chat)
    before = len(server.conversation_store.list_conversations())
    with TestClient(server.app) as client:
        response = client.post("/api/external/game-reaction", json=payload())
    assert response.status_code == 200
    assert response.json()["text"] == "Найди укрытие."
    assert response.json()["reasoning"] == "internal"
    assert captured["tools"] is None and captured["model"] is None
    assert "Do not call tools" in captured["messages"][0]["content"]
    assert len(server.conversation_store.list_conversations()) == before


def test_external_game_reaction_rejects_enabled_side_effects(monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_GAME_REACTIONS_ENABLED", True)
    with TestClient(server.app) as client:
        response = client.post("/api/external/game-reaction", json=payload(tools_enabled=True))
    assert response.status_code in {400, 422}


def test_external_game_reaction_preserves_exact_russian_unicode(monkeypatch):
    exact = "Здоровье критическое. Найди укрытие."
    monkeypatch.setattr(config, "EXTERNAL_GAME_REACTIONS_ENABLED", True)
    monkeypatch.setattr(
        server.ollama_client,
        "chat",
        lambda messages, tools=None, model=None: {
            "model": "qwen3.5:9b",
            "message": {"content": exact},
        },
    )
    with TestClient(server.app) as client:
        response = client.post("/api/external/game-reaction", json=payload())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["text"] == exact
