from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from storage.conversation_store import ConversationStore  # noqa: E402


def _install_temp_attachment_store(monkeypatch, tmp_path: Path) -> ConversationStore:
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    monkeypatch.setattr(server, "conversation_store", store)
    monkeypatch.setattr(server, "session_store", server.SessionStore(server.config.SYSTEM_PROMPT, store))
    monkeypatch.setattr(server.config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(server.config, "ATTACHMENTS_STORAGE_ROOT", tmp_path / "storage" / "attachments")
    return store


def test_text_attachment_is_persisted_linked_and_reloaded(monkeypatch, tmp_path):
    store = _install_temp_attachment_store(monkeypatch, tmp_path)
    conversation_id = store.create_conversation("attachments")
    message = store.append_message(conversation_id, "user", "read this")

    source = tmp_path / "original.txt"
    source.write_text("hello from source", encoding="utf-8")
    attachment = server.ChatAttachment(name=str(source), type="text", mime="text/plain", content=source.read_text(encoding="utf-8"))

    persisted = server._persist_uploaded_attachments(conversation_id, message["id"], [attachment])
    source.unlink()

    assert len(persisted) == 1
    stored_path = tmp_path / persisted[0]["stored_relative_path"]
    assert stored_path.read_text(encoding="utf-8") == "hello from source"

    reloaded = store.get_conversation(conversation_id)
    attachments = reloaded["messages"][0]["attachments"]
    assert attachments[0]["id"] == persisted[0]["id"]
    assert attachments[0]["kind"] == "text"
    assert attachments[0]["source"] == "uploaded"
    assert attachments[0]["original_name"] == "original.txt"
    assert attachments[0]["url"] == f"/api/attachments/{persisted[0]['id']}/content"


def test_image_attachment_content_endpoint_returns_bytes_and_type(monkeypatch, tmp_path):
    store = _install_temp_attachment_store(monkeypatch, tmp_path)
    conversation_id = store.create_conversation("image")
    message = store.append_message(conversation_id, "user", "what is this?")
    image_bytes = b"\x89PNG\r\n\x1a\nfake"
    data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    attachment = server.ChatAttachment(name="screen.png", type="image", mime="image/png", data_url=data_url)

    persisted = server._persist_uploaded_attachments(conversation_id, message["id"], [attachment])[0]
    response = TestClient(server.app).get(f"/api/attachments/{persisted['id']}/content")

    assert response.status_code == 200
    assert response.content == image_bytes
    assert response.headers["content-type"].startswith("image/png")


def test_missing_and_invalid_attachment_requests_return_404(monkeypatch, tmp_path):
    _install_temp_attachment_store(monkeypatch, tmp_path)
    client = TestClient(server.app)

    assert client.get("/api/attachments/not-a-uuid/content").status_code == 404
    assert client.get("/api/attachments/00000000-0000-0000-0000-000000000000/content").status_code == 404


def test_attachment_path_must_resolve_inside_storage_root(monkeypatch, tmp_path):
    store = _install_temp_attachment_store(monkeypatch, tmp_path)
    conversation_id = store.create_conversation("path guard")
    message = store.append_message(conversation_id, "user", "bad path")
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    attachment_id = "11111111-1111-1111-1111-111111111111"
    store.add_attachment(
        {
            "id": attachment_id,
            "conversation_id": conversation_id,
            "message_id": message["id"],
            "kind": "text",
            "source": "uploaded",
            "original_name": "outside.txt",
            "stored_filename": "outside.txt",
            "stored_relative_path": "outside.txt",
            "mime_type": "text/plain",
            "size_bytes": outside.stat().st_size,
            "created_at": server._now_iso(),
            "sha256": None,
            "metadata": {},
        }
    )

    response = TestClient(server.app).get(f"/api/attachments/{attachment_id}/content")
    assert response.status_code == 404


def test_chat_failure_keeps_user_message_attachment_and_failed_status(monkeypatch, tmp_path):
    store = _install_temp_attachment_store(monkeypatch, tmp_path)
    conversation_id = server.session_store.new_conversation("failed chat")

    async def no_ocr(*args, **kwargs):
        return "", []

    async def no_vision(*args, **kwargs):
        return "", []

    def fail_agent(*args, **kwargs):
        raise server.SienaInfraError("simulated model failure")

    monkeypatch.setattr(server, "_run_image_ocr", no_ocr)
    monkeypatch.setattr(server, "_run_image_vision", no_vision)
    monkeypatch.setattr(server, "run_agent_loop", fail_agent)
    monkeypatch.setattr(server, "build_registry", lambda logger: ({}, None, None, None))

    response = TestClient(server.app).post(
        "/api/chat",
        json={
            "conversation_id": conversation_id,
            "message": "read this",
            "attachments": [{"name": "note.txt", "type": "text", "mime": "text/plain", "content": "hello"}],
        },
    )

    assert response.status_code == 503
    conversation = store.get_conversation(conversation_id)
    assert len(conversation["messages"]) == 1
    message = conversation["messages"][0]
    assert message["role"] == "user"
    assert message["metadata"]["status"] == "failed"
    assert "simulated model failure" in message["metadata"]["error"]
    assert message["attachments"][0]["original_name"] == "note.txt"


def test_chat_response_is_saved_to_requested_conversation(monkeypatch, tmp_path):
    store = _install_temp_attachment_store(monkeypatch, tmp_path)
    chat_a = server.session_store.new_conversation("chat a")
    chat_b = server.session_store.new_conversation("chat b")

    async def no_ocr(*args, **kwargs):
        return "", []

    async def no_vision(*args, **kwargs):
        return "", []

    monkeypatch.setattr(server, "_run_image_ocr", no_ocr)
    monkeypatch.setattr(server, "_run_image_vision", no_vision)
    monkeypatch.setattr(server, "run_agent_loop", lambda *args, **kwargs: "answer for a")
    monkeypatch.setattr(server, "build_registry", lambda logger: ({}, None, None, None))

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": chat_a, "message": "hello from a", "attachments": []},
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] == chat_a
    messages_a = store.get_conversation(chat_a)["messages"]
    messages_b = store.get_conversation(chat_b)["messages"]
    assert [m["role"] for m in messages_a] == ["user", "assistant"]
    assert messages_a[0]["metadata"]["status"] == "completed"
    assert messages_a[1]["content"] == "answer for a"
    assert messages_b == []


class _LockedChat:
    def locked(self):
        return True


def test_chat_lock_returns_409(monkeypatch, tmp_path):
    _install_temp_attachment_store(monkeypatch, tmp_path)
    conversation_id = server.session_store.new_conversation("locked")
    monkeypatch.setattr(server, "chat_lock", _LockedChat())

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "blocked", "attachments": []},
    )

    assert response.status_code == 409
    assert "already in progress" in response.text
