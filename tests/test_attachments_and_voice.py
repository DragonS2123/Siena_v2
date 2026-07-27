from __future__ import annotations

import base64
import io
import wave
from pathlib import Path


def _wav() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 160)
    return target.getvalue()


def _ready_chat(client, monkeypatch):
    runtime = client.app.state.runtime
    monkeypatch.setattr(
        runtime.catalog,
        "refresh",
        lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]},
    )
    monkeypatch.setattr("core.chat_service.run_agent_loop", lambda *_args, **_kwargs: "answer")
    return runtime, client.post("/api/conversations", json={}).json()["conversation_id"]


def test_text_attachment_is_safely_persisted(client, monkeypatch):
    runtime, conversation_id = _ready_chat(client, monkeypatch)
    response = client.post("/api/chat", json={
        "conversation_id": conversation_id,
        "message": "read this",
        "attachments": [{"name": "../../notes.txt", "type": "text", "mime": "text/plain", "content": "hello"}],
    })
    assert response.status_code == 200, response.text
    attachment = response.json()["attachments"][0]
    stored = (runtime.chat._attachments._root / attachment["stored_relative_path"]).resolve()
    assert stored.is_file() and stored.read_text(encoding="utf-8") == "hello"
    assert runtime.chat._attachments._root.resolve() in stored.parents
    assert attachment["original_name"] == "notes.txt"


def test_image_attachment_records_ocr_and_vision(client, monkeypatch):
    from ocr.glm_ocr_service import GlmOcrService
    from vision.qwen_vision_service import QwenVisionService
    _, conversation_id = _ready_chat(client, monkeypatch)
    monkeypatch.setattr(GlmOcrService, "extract_text", lambda *_args: {"text": "visible text"})
    monkeypatch.setattr(QwenVisionService, "describe_image", lambda *_args: {"text": "a local scene"})
    encoded = base64.b64encode(b"image bytes").decode()
    response = client.post("/api/chat", json={
        "conversation_id": conversation_id,
        "message": "describe this image",
        "attachments": [{"name": "view.png", "type": "image", "mime": "image/png", "data_url": encoded}],
    })
    assert response.status_code == 200, response.text
    attachment = response.json()["attachments"][0]
    assert attachment["metadata"]["ocr_status"] == "completed"
    assert attachment["metadata"]["vision_status"] == "completed"


def test_invalid_attachment_leaves_failed_message_without_file(client, monkeypatch):
    runtime, conversation_id = _ready_chat(client, monkeypatch)
    response = client.post("/api/chat", json={
        "conversation_id": conversation_id,
        "message": "bad file",
        "attachments": [{"name": "bad.exe", "type": "binary", "mime": "application/octet-stream"}],
    })
    assert response.status_code == 422
    conversation = runtime.conversations.get_conversation(conversation_id)
    assert conversation["messages"][0]["metadata"]["status"] == "failed"
    assert not list(runtime.chat._attachments._root.rglob("*")) if runtime.chat._attachments._root.exists() else True


def test_stt_and_tts_success_and_controlled_failure(client, monkeypatch, tmp_path):
    runtime = client.app.state.runtime
    monkeypatch.setattr(runtime.stt, "transcribe_wav", lambda *_args: {"text": "hello", "provider": "test"})
    stt = client.post("/api/voice/stt/transcribe", files={"file": ("sample.wav", _wav(), "audio/wav")})
    assert stt.status_code == 200 and stt.json()["text"] == "hello"

    audio = tmp_path / "result.wav"
    audio.write_bytes(_wav())
    monkeypatch.setattr(runtime.tts, "synthesize_to_file", lambda *_args: {"audio_path": str(audio)})
    tts = client.post("/api/voice/synthesize", json={"text": "hello"})
    assert tts.status_code == 200 and tts.json()["audio_url"].endswith("result.wav")

    monkeypatch.setattr(runtime.stt, "transcribe_wav", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    assert client.post("/api/voice/stt/transcribe", files={"file": ("sample.wav", _wav(), "audio/wav")}).status_code == 503
    monkeypatch.setattr(runtime.tts, "synthesize_to_file", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    assert client.post("/api/voice/synthesize", json={"text": "hello"}).status_code == 503
