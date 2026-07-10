"""Settings Pass 3 — soft chat-prompt language preference
(config.PREFERRED_RESPONSE_LANGUAGE, api/server.py::chat). Verifies the note
is injected only when the setting is not "auto", and that the default
("auto") injects nothing at all, preserving today's behavior exactly.

Same TestClient + fake_agent_loop pattern as tests/test_nucleares_context.py
(not imported from there — this file is self-contained and never touches
Nucleares/game bridge code).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from storage.conversation_store import ConversationStore  # noqa: E402


def _install_temp_store(monkeypatch, tmp_path: Path) -> ConversationStore:
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    monkeypatch.setattr(server, "conversation_store", store)
    monkeypatch.setattr(server, "session_store", server.SessionStore(server.config.SYSTEM_PROMPT, store))
    return store


def _patch_fast_chat(monkeypatch, captured: dict):
    async def no_ocr(*args, **kwargs):
        return "", []

    async def no_vision(*args, **kwargs):
        return "", []

    def fake_agent_loop(*args, **kwargs):
        captured["content"] = kwargs["session"].messages[-1]["content"]
        return "ok"

    monkeypatch.setattr(server, "_run_image_ocr", no_ocr)
    monkeypatch.setattr(server, "_run_image_vision", no_vision)
    monkeypatch.setattr(server, "build_registry", lambda logger: ({}, None, None, None))
    monkeypatch.setattr(server, "run_agent_loop", fake_agent_loop)


def test_default_auto_injects_no_language_note(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)
    monkeypatch.setattr(server.config, "PREFERRED_RESPONSE_LANGUAGE", "auto")
    conversation_id = server.session_store.new_conversation("lang auto")

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "Привет!", "attachments": []},
    )

    assert response.status_code == 200
    assert captured["content"] == "Привет!"
    for note in server._LANGUAGE_PREFERENCE_NOTES.values():
        assert note not in captured["content"]


def test_ru_preference_injects_soft_note(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)
    monkeypatch.setattr(server.config, "PREFERRED_RESPONSE_LANGUAGE", "ru")
    conversation_id = server.session_store.new_conversation("lang ru")

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "Hello!", "attachments": []},
    )

    assert response.status_code == 200
    assert server._LANGUAGE_PREFERENCE_NOTES["ru"] in captured["content"]
    assert "Hello!" in captured["content"]


def test_en_preference_injects_soft_note(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)
    monkeypatch.setattr(server.config, "PREFERRED_RESPONSE_LANGUAGE", "en")
    conversation_id = server.session_store.new_conversation("lang en")

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "Привет!", "attachments": []},
    )

    assert response.status_code == 200
    assert server._LANGUAGE_PREFERENCE_NOTES["en"] in captured["content"]
