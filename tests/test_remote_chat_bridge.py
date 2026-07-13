"""Siena Remote (0.6.0) — chat/attachment/tts bridge.

Unit tests for RemoteChatService/RemoteTtsService against fake
attachment/run_chat_turn/synthesize callables (isolating the bridge's own
orchestration: conversation mapping, idempotency, one-per-device,
cancellation, shutdown, attachment lifecycle, zero-content diagnostics) plus
one real local fake-Relay integration test proving the actual wire encoding
round-trips through HomeGatewayAgent exactly like test_remote_gateway.py's
own smoke test.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_gateway.agent import HomeGatewayAgent  # noqa: E402
from remote_gateway.attachment_client import AttachmentClientError  # noqa: E402
from remote_gateway.remote_chat_service import RemoteChatService  # noqa: E402
from remote_gateway.remote_tts_service import RemoteTtsService  # noqa: E402
from storage.conversation_store import ConversationStore  # noqa: E402
from tests.test_remote_gateway import (  # noqa: E402
    FakeConnection,
    FakeTransport,
    TEST_GATEWAY_ID,
    TEST_TOKEN,
    _connected_message,
    run_async,
    wait_for_state,
)
from remote_gateway.agent import GatewayState  # noqa: E402

VALID_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 200
DEVICE_ID = "dev_test0000000001"


class _StubLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def event(self, event_type, console_message=None, **fields):
        self.events.append((event_type, fields))

    def error(self, event_type, console_message, **fields):
        self.events.append((event_type, fields))


class FakeAttachmentClient:
    def __init__(self, *, download_bytes: bytes | None = None, download_error: Exception | None = None,
                 upload_result: dict | None = None, upload_error: Exception | None = None):
        self.download_bytes = download_bytes if download_bytes is not None else VALID_JPEG_BYTES
        self.download_error = download_error
        self.upload_result = upload_result or {"attachment_id": "att_fake0000000001", "mime_type": "audio/wav", "size_bytes": 999}
        self.upload_error = upload_error
        self.downloaded: list[str] = []
        self.deleted: list[str] = []
        self.uploaded: list[tuple[str, bytes, str]] = []

    async def download_device_attachment(self, attachment_id: str) -> bytes:
        self.downloaded.append(attachment_id)
        if self.download_error is not None:
            raise self.download_error
        return self.download_bytes

    async def upload_gateway_attachment(self, device_id: str, *, data: bytes, content_type: str) -> dict:
        self.uploaded.append((device_id, data, content_type))
        if self.upload_error is not None:
            raise self.upload_error
        return self.upload_result

    async def delete_attachment(self, attachment_id: str) -> None:
        self.deleted.append(attachment_id)


def _chat_request_message(**overrides) -> dict:
    message = {
        "type": "chat.request",
        "protocol_version": 1,
        "request_id": "chat-1",
        "conversation_id": "conv_test00000001",
        "message_id": "msg_test00000001",
        "device_id": DEVICE_ID,
        "text": "Привет!",
        "attachments": [],
    }
    message.update(overrides)
    return message


def _tts_request_message(**overrides) -> dict:
    message = {
        "type": "tts.request",
        "protocol_version": 1,
        "request_id": "tts-1",
        "conversation_id": "conv_test00000001",
        "message_id": "msg_test00000001",
        "device_id": DEVICE_ID,
        "text": "Здравствуйте!",
        "language": "ru",
    }
    message.update(overrides)
    return message


class _Recorder:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


class _DiagnosticRecorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, fields: dict) -> None:
        self.events.append((event, fields))


# Building a full SessionStore drags in api/server.py's module-level import
# surface indirectly (it lives in api/server.py itself) — instead we use the
# tiny standalone Session class directly (core/session.py), matching exactly
# what SessionStore.build_session() constructs internally.
from core.session import Session  # noqa: E402


def _make_chat_service(tmp_path, *, run_chat_turn=None, process_remote_image=None, attachment_client=None, diagnostics=None):
    conversation_store = ConversationStore(tmp_path / "conversations.sqlite3")

    def build_session(conversation_id: str) -> Session:
        return Session("system prompt")

    def create_conversation(title: str | None) -> str:
        return conversation_store.create_conversation(title)

    async def default_run_chat_turn(**kwargs):
        user_message = kwargs["user_message"]
        assistant_message = conversation_store.append_message(
            kwargs["conversation_id"], "assistant", "canned answer", model="test-model",
        )
        return {
            "answer": "canned answer",
            "conversation_id": kwargs["conversation_id"],
            "message_id": user_message["id"],
            "assistant_message_id": assistant_message["id"],
            "ocr_results": [],
            "vision_results": [],
            "model_used": "test-model",
            "model_role": "chat",
            "routing_reason": "test",
            "routing_mode": "auto",
            "manual_only": False,
        }

    async def default_process_remote_image(**kwargs):
        return {"ocr_context": "", "vision_context": "", "ocr_result": None, "vision_result": None}

    service = RemoteChatService(
        run_chat_turn=run_chat_turn or default_run_chat_turn,
        conversation_store=conversation_store,
        build_session=build_session,
        create_conversation=create_conversation,
        logger_factory=lambda conversation_id: _StubLogger(),
        chat_lock=asyncio.Lock(),
        attachment_client=attachment_client or FakeAttachmentClient(),
        process_remote_image=process_remote_image or default_process_remote_image,
        max_image_bytes=6 * 1024 * 1024,
        on_diagnostic=diagnostics or (lambda event, fields: None),
    )
    return service, conversation_store


# ---- conversation mapping / persistence ------------------------------------

def test_conversation_link_created_once_and_reused(tmp_path):
    service, conversation_store = _make_chat_service(tmp_path)
    gateway_id = TEST_GATEWAY_ID

    first = service._resolve_local_conversation("conv_remote_abc", gateway_id)
    second = service._resolve_local_conversation("conv_remote_abc", gateway_id)
    assert first == second

    link = conversation_store.get_remote_link("conv_remote_abc")
    assert link["conversation_id"] == first
    assert link["gateway_id"] == gateway_id


def test_conversation_link_persists_across_service_restart(tmp_path):
    db_path = tmp_path / "conversations.sqlite3"
    store_a = ConversationStore(db_path)
    local_id = store_a.create_conversation(None)
    store_a.create_remote_link("conv_remote_xyz", TEST_GATEWAY_ID, local_id)

    # Simulate a backend restart: fresh ConversationStore instance, same file.
    store_b = ConversationStore(db_path)
    link = store_b.get_remote_link("conv_remote_xyz")
    assert link is not None
    assert link["conversation_id"] == local_id


# ---- chat.request happy path -----------------------------------------------

def test_chat_request_sends_accepted_then_deltas_then_completed(tmp_path):
    async def run_chat_turn(**kwargs):
        conversation_store.append_message(kwargs["conversation_id"], "assistant", "hello world", model="m")
        return {
            "answer": "hello world", "conversation_id": kwargs["conversation_id"],
            "message_id": kwargs["user_message"]["id"], "assistant_message_id": "abc-123",
            "ocr_results": [], "vision_results": [], "model_used": "m", "model_role": "chat",
            "routing_reason": "r", "routing_mode": "auto", "manual_only": False,
        }

    service, conversation_store = _make_chat_service(tmp_path, run_chat_turn=run_chat_turn)
    recorder = _Recorder()

    run_async(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))

    types = [m["type"] for m in recorder.sent]
    assert types == ["chat.accepted", "chat.delta", "chat.completed"]
    assert recorder.sent[0]["request_id"] == "chat-1"
    assert recorder.sent[1]["text"] == "hello world"
    assert recorder.sent[1]["sequence"] == 1
    assert recorder.sent[1]["message_id"] == "msg_abc-123"
    assert recorder.sent[2]["sequence"] == 1
    assert recorder.sent[2]["finish_reason"] == "stop"


def test_long_answer_splits_into_multiple_deltas(tmp_path):
    long_answer = "x" * 40_000

    async def run_chat_turn(**kwargs):
        return {
            "answer": long_answer, "conversation_id": kwargs["conversation_id"],
            "message_id": kwargs["user_message"]["id"], "assistant_message_id": "abc-999",
            "ocr_results": [], "vision_results": [], "model_used": "m", "model_role": "chat",
            "routing_reason": "r", "routing_mode": "auto", "manual_only": False,
        }

    service, _ = _make_chat_service(tmp_path, run_chat_turn=run_chat_turn)
    recorder = _Recorder()
    run_async(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))

    deltas = [m for m in recorder.sent if m["type"] == "chat.delta"]
    assert len(deltas) == 3  # 16000 + 16000 + 8000
    assert all(len(d["text"].encode("utf-8")) <= 16_000 for d in deltas)
    assert "".join(d["text"] for d in deltas) == long_answer
    completed = [m for m in recorder.sent if m["type"] == "chat.completed"][0]
    assert completed["sequence"] == 3


def test_run_chat_turn_failure_sends_chat_failed(tmp_path):
    async def failing_run_chat_turn(**kwargs):
        raise RuntimeError("boom")

    service, _ = _make_chat_service(tmp_path, run_chat_turn=failing_run_chat_turn)
    recorder = _Recorder()
    run_async(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))

    assert len(recorder.sent) == 2
    assert recorder.sent[0]["type"] == "chat.accepted"
    assert recorder.sent[1]["type"] == "chat.failed"
    assert recorder.sent[1]["code"] == "generation_failed"


def test_duplicate_request_id_is_idempotent_noop(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run_chat_turn(**kwargs):
        started.set()
        await release.wait()
        return {
            "answer": "done", "conversation_id": kwargs["conversation_id"],
            "message_id": kwargs["user_message"]["id"], "assistant_message_id": "abc-1",
            "ocr_results": [], "vision_results": [], "model_used": "m", "model_role": "chat",
            "routing_reason": "r", "routing_mode": "auto", "manual_only": False,
        }

    service, _ = _make_chat_service(tmp_path, run_chat_turn=slow_run_chat_turn)
    recorder = _Recorder()

    async def scenario():
        task = asyncio.create_task(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))
        await started.wait()
        # Duplicate resend of the SAME request_id while active — no-op.
        await service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID)
        release.set()
        await task

    run_async(scenario())
    assert [m["type"] for m in recorder.sent] == ["chat.accepted", "chat.delta", "chat.completed"]


def test_different_request_id_while_active_rejected(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run_chat_turn(**kwargs):
        started.set()
        await release.wait()
        return {
            "answer": "done", "conversation_id": kwargs["conversation_id"],
            "message_id": kwargs["user_message"]["id"], "assistant_message_id": "abc-1",
            "ocr_results": [], "vision_results": [], "model_used": "m", "model_role": "chat",
            "routing_reason": "r", "routing_mode": "auto", "manual_only": False,
        }

    service, _ = _make_chat_service(tmp_path, run_chat_turn=slow_run_chat_turn)
    recorder = _Recorder()

    async def scenario():
        task = asyncio.create_task(service.handle_message(_chat_request_message(request_id="chat-1"), recorder.send, TEST_GATEWAY_ID))
        await started.wait()
        await service.handle_message(_chat_request_message(request_id="chat-2"), recorder.send, TEST_GATEWAY_ID)
        release.set()
        await task

    run_async(scenario())
    failed = [m for m in recorder.sent if m["type"] == "chat.failed"]
    assert len(failed) == 1
    assert failed[0]["code"] == "generation_already_active"
    assert failed[0]["request_id"] == "chat-2"


def test_chat_cancel_cancels_in_flight_generation(tmp_path):
    started = asyncio.Event()

    async def hanging_run_chat_turn(**kwargs):
        started.set()
        await asyncio.sleep(3600)

    service, _ = _make_chat_service(tmp_path, run_chat_turn=hanging_run_chat_turn)
    recorder = _Recorder()

    async def scenario():
        task = asyncio.create_task(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))
        await started.wait()
        await service.handle_message(
            {"type": "chat.cancel", "protocol_version": 1, "request_id": "chat-1", "conversation_id": "conv_test00000001"},
            recorder.send, TEST_GATEWAY_ID,
        )
        await task

    run_async(scenario())
    types = [m["type"] for m in recorder.sent]
    assert types == ["chat.accepted", "chat.cancelled"]


def test_shutdown_cancels_active_generation(tmp_path):
    started = asyncio.Event()

    async def hanging_run_chat_turn(**kwargs):
        started.set()
        await asyncio.sleep(3600)

    service, _ = _make_chat_service(tmp_path, run_chat_turn=hanging_run_chat_turn)
    recorder = _Recorder()

    async def scenario():
        task = asyncio.create_task(service.handle_message(_chat_request_message(), recorder.send, TEST_GATEWAY_ID))
        await started.wait()
        await service.shutdown()
        await task

    run_async(scenario())
    assert [m["type"] for m in recorder.sent] == ["chat.accepted", "chat.cancelled"]


def test_diagnostic_events_never_contain_message_text(tmp_path):
    diag = _DiagnosticRecorder()
    service, _ = _make_chat_service(tmp_path, diagnostics=diag)
    recorder = _Recorder()

    secret_text = "секретный текст пользователя, который нельзя логировать"
    run_async(service.handle_message(_chat_request_message(text=secret_text), recorder.send, TEST_GATEWAY_ID))

    blob = repr(diag.events)
    assert secret_text not in blob
    assert "canned answer" not in blob
    event_names = [name for name, _fields in diag.events]
    assert "remote_chat_received" in event_names
    assert "remote_chat_accepted" in event_names
    assert "remote_chat_completed" in event_names


# ---- attachments ------------------------------------------------------------

def test_image_attachment_downloaded_processed_and_deleted(tmp_path):
    attachment_client = FakeAttachmentClient()
    calls = []

    async def process_remote_image(**kwargs):
        calls.append(kwargs)
        return {"ocr_context": "OCR TEXT", "vision_context": "", "ocr_result": {"name": "x", "status": "extracted"}, "vision_result": None}

    captured_contexts = {}

    async def run_chat_turn(**kwargs):
        captured_contexts["image_ocr_context"] = kwargs["image_ocr_context"]
        return {
            "answer": "ok", "conversation_id": kwargs["conversation_id"], "message_id": kwargs["user_message"]["id"],
            "assistant_message_id": "abc-1", "ocr_results": [], "vision_results": [], "model_used": "m",
            "model_role": "chat", "routing_reason": "r", "routing_mode": "auto", "manual_only": False,
        }

    service, _ = _make_chat_service(
        tmp_path, run_chat_turn=run_chat_turn, process_remote_image=process_remote_image, attachment_client=attachment_client,
    )
    recorder = _Recorder()
    message = _chat_request_message(
        text="", attachments=[{"attachment_id": "att_abc12345678901234", "kind": "image", "action": "ocr", "target_language": None}],
    )
    run_async(service.handle_message(message, recorder.send, TEST_GATEWAY_ID))

    assert attachment_client.downloaded == ["att_abc12345678901234"]
    assert attachment_client.deleted == ["att_abc12345678901234"]
    assert len(calls) == 1
    assert calls[0]["action"] == "ocr"
    assert captured_contexts["image_ocr_context"] == "OCR TEXT"


def test_oversized_attachment_skipped(tmp_path):
    attachment_client = FakeAttachmentClient(download_bytes=b"\xff\xd8\xff" + b"\x00" * 100)
    calls = []

    async def process_remote_image(**kwargs):
        calls.append(kwargs)
        return {"ocr_context": "", "vision_context": "", "ocr_result": None, "vision_result": None}

    service, _ = _make_chat_service(tmp_path, process_remote_image=process_remote_image, attachment_client=attachment_client)
    service._max_image_bytes = 10  # force "oversized" for a tiny fake image

    recorder = _Recorder()
    message = _chat_request_message(
        text="hi", attachments=[{"attachment_id": "att_abc12345678901234", "kind": "image", "action": "auto", "target_language": None}],
    )
    run_async(service.handle_message(message, recorder.send, TEST_GATEWAY_ID))

    assert calls == []  # process_remote_image never invoked for an oversized download
    # Still deleted from Relay via the finally block — we downloaded it, so we
    # clean it up regardless of whether it was usable (never leave it for the
    # TTL sweep when we already know we're done with it).
    assert attachment_client.deleted == ["att_abc12345678901234"]


def test_download_failure_does_not_crash_turn(tmp_path):
    attachment_client = FakeAttachmentClient(download_error=AttachmentClientError("boom", status_code=404))
    service, _ = _make_chat_service(tmp_path, attachment_client=attachment_client)
    recorder = _Recorder()
    message = _chat_request_message(
        text="hi", attachments=[{"attachment_id": "att_abc12345678901234", "kind": "image", "action": "auto", "target_language": None}],
    )
    run_async(service.handle_message(message, recorder.send, TEST_GATEWAY_ID))
    assert [m["type"] for m in recorder.sent] == ["chat.accepted", "chat.delta", "chat.completed"]


def test_garbage_bytes_fail_mime_sniff_and_are_skipped(tmp_path):
    attachment_client = FakeAttachmentClient(download_bytes=b"not an image at all")
    calls = []

    async def process_remote_image(**kwargs):
        calls.append(kwargs)
        return {"ocr_context": "", "vision_context": "", "ocr_result": None, "vision_result": None}

    service, _ = _make_chat_service(tmp_path, process_remote_image=process_remote_image, attachment_client=attachment_client)
    recorder = _Recorder()
    message = _chat_request_message(
        text="hi", attachments=[{"attachment_id": "att_abc12345678901234", "kind": "image", "action": "auto", "target_language": None}],
    )
    run_async(service.handle_message(message, recorder.send, TEST_GATEWAY_ID))
    assert calls == []


# ---- RemoteTtsService --------------------------------------------------------

def _make_tts_service(tmp_path, *, synthesize=None, sanitize=None, attachment_client=None, diagnostics=None):
    audio_path = tmp_path / "synth.wav"

    async def default_synthesize(text: str):
        audio_path.write_bytes(b"RIFF....WAVEfake")
        return {"audio_path": str(audio_path), "duration_sec": 1.5, "provider": "silero"}

    service = RemoteTtsService(
        synthesize=synthesize or default_synthesize,
        sanitize_text=sanitize or (lambda text: text),
        attachment_client=attachment_client or FakeAttachmentClient(),
        on_diagnostic=diagnostics or (lambda event, fields: None),
    )
    return service, audio_path


def test_tts_request_uploads_and_sends_ready(tmp_path):
    attachment_client = FakeAttachmentClient(upload_result={"attachment_id": "att_audio001", "mime_type": "audio/wav", "size_bytes": 42})
    service, audio_path = _make_tts_service(tmp_path, attachment_client=attachment_client)
    recorder = _Recorder()

    run_async(service.handle_message(_tts_request_message(), recorder.send, TEST_GATEWAY_ID))

    assert [m["type"] for m in recorder.sent] == ["tts.ready"]
    ready = recorder.sent[0]
    assert ready["attachment_id"] == "att_audio001"
    assert ready["duration_ms"] == 1500
    assert attachment_client.uploaded[0][0] == DEVICE_ID
    assert not audio_path.exists()  # temp file deleted after upload


def test_tts_empty_after_sanitize_fails_without_synthesizing(tmp_path):
    synthesize_calls = []

    async def synthesize(text: str):
        synthesize_calls.append(text)
        return {"audio_path": "/tmp/unused.wav", "duration_sec": 1.0}

    service, _ = _make_tts_service(tmp_path, synthesize=synthesize, sanitize=lambda text: "   ")
    recorder = _Recorder()
    run_async(service.handle_message(_tts_request_message(), recorder.send, TEST_GATEWAY_ID))

    assert synthesize_calls == []
    assert recorder.sent[0]["type"] == "tts.failed"
    assert recorder.sent[0]["code"] == "empty_text"


def test_tts_upload_failure_still_deletes_temp_file(tmp_path):
    attachment_client = FakeAttachmentClient(upload_error=AttachmentClientError("boom", status_code=500))
    service, audio_path = _make_tts_service(tmp_path, attachment_client=attachment_client)
    recorder = _Recorder()
    run_async(service.handle_message(_tts_request_message(), recorder.send, TEST_GATEWAY_ID))

    assert recorder.sent[0]["type"] == "tts.failed"
    assert recorder.sent[0]["code"] == "upload_failed"
    assert not audio_path.exists()


def test_second_tts_request_rejected_while_active(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_synthesize(text: str):
        started.set()
        await release.wait()
        return {"audio_path": str(tmp_path / "never.wav"), "duration_sec": 1.0}

    service, _ = _make_tts_service(tmp_path, synthesize=slow_synthesize)
    recorder = _Recorder()

    async def scenario():
        task = asyncio.create_task(service.handle_message(_tts_request_message(request_id="tts-1"), recorder.send, TEST_GATEWAY_ID))
        await started.wait()
        await service.handle_message(_tts_request_message(request_id="tts-2"), recorder.send, TEST_GATEWAY_ID)
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    run_async(scenario())
    failed = [m for m in recorder.sent if m["type"] == "tts.failed"]
    assert any(m["code"] == "tts_already_active" for m in failed)


def test_tts_diagnostic_events_never_contain_spoken_text(tmp_path):
    diag = _DiagnosticRecorder()
    service, _ = _make_tts_service(tmp_path, diagnostics=diag)
    recorder = _Recorder()
    secret_text = "не должно попасть в лог никогда"
    run_async(service.handle_message(_tts_request_message(text=secret_text), recorder.send, TEST_GATEWAY_ID))
    blob = repr(diag.events)
    assert secret_text not in blob


# ---- real local fake-Relay integration (wire encoding round trip) ----------

def test_fake_relay_chat_round_trip_over_real_websocket(tmp_path):
    """Mirrors test_remote_gateway.py's own fake-Relay smoke test (same
    websocket.request.headers / recv()/send() pattern), but pushes a real
    chat.request frame at the agent and asserts real chat.accepted/delta/
    completed frames come back over the actual (local, fake) wire — proving
    HomeGatewayAgent's application-message dispatch + send-lock plumbing
    works end-to-end, not just against the in-memory FakeConnection."""
    from remote_gateway.transport import WebsocketsTransport

    service, _ = _make_chat_service(tmp_path)

    async def scenario():
        from websockets.asyncio.server import serve

        received: list[dict] = []
        done = asyncio.Event()

        async def handler(websocket):
            auth = websocket.request.headers.get("authorization", "")
            if auth != f"Bearer {TEST_TOKEN}":
                await websocket.close(4401, "Unauthorized")
                return
            await websocket.send(json.dumps(_connected_message(interval=0.05, timeout=5)))

            # First frame from a freshly connected agent is always a heartbeat.
            frame = json.loads(await websocket.recv())
            assert frame["type"] == "heartbeat"
            await websocket.send(json.dumps({"type": "heartbeat.ack", "request_id": frame["request_id"], "server_time_utc": "t"}))

            await websocket.send(json.dumps({
                "type": "chat.request", "protocol_version": 1, "request_id": "chat-wire-1",
                "conversation_id": "conv_wire00000001", "message_id": "msg_wire00000001",
                "device_id": DEVICE_ID, "text": "hello over the wire", "attachments": [],
            }))

            while not done.is_set():
                raw = await websocket.recv()
                message = json.loads(raw)
                if message["type"] == "heartbeat":
                    await websocket.send(json.dumps({"type": "heartbeat.ack", "request_id": message["request_id"], "server_time_utc": "t"}))
                    continue
                received.append(message)
                if message["type"] == "chat.completed":
                    done.set()

        async with serve(handler, "127.0.0.1", 0) as server_instance:
            port = server_instance.sockets[0].getsockname()[1]
            agent = HomeGatewayAgent(
                transport=WebsocketsTransport(),
                credentials_provider=lambda: (TEST_GATEWAY_ID, TEST_TOKEN),
                relay_url=f"ws://127.0.0.1:{port}",
                allow_insecure_localhost=True,
            )
            agent.set_application_handler(service.handle_message)
            agent.start()
            try:
                await wait_for_state(agent, GatewayState.CONNECTED, timeout=5.0)
                await asyncio.wait_for(done.wait(), timeout=5.0)
            finally:
                await agent.stop()

        assert [m["type"] for m in received] == ["chat.accepted", "chat.delta", "chat.completed"]
        assert received[0]["request_id"] == "chat-wire-1"
        assert received[1]["text"] == "canned answer"

    run_async(scenario(), timeout=30)
