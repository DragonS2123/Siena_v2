"""Bridges Relay's chat.request/chat.cancel into the SAME production chat
pipeline the local React UI uses (run_chat_turn, injected from api/server.py
— never a second, simplified LLM call). One active generation per Device;
idempotent on repeated request_id; cancellation is best-effort (see
run_chat_turn's own docstring on asyncio.to_thread not being forcibly
preemptible). Never logs message text/deltas/OCR/translation content — only
request_id-keyed diagnostic events with zero content, via on_diagnostic.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from remote_gateway.attachment_client import AttachmentClient, AttachmentClientError
from remote_gateway.chat_protocol import (
    ChatProtocolError,
    build_chat_accepted,
    build_chat_cancelled,
    build_chat_completed,
    build_chat_delta,
    build_chat_failed,
    build_conversation_title,
    parse_chat_cancel,
    parse_chat_request,
)
from storage.conversation_store import generate_conversation_title

MAX_DELTA_TEXT_BYTES = 16_000
MAX_TOTAL_RESPONSE_BYTES = 256_000

ALLOWED_TARGET_LANGUAGES = frozenset({"ru", "en", "ja", "de", "fr", "es"})


def _sniff_image_mime(data: bytes) -> str | None:
    """Independent magic-byte sniff of the DOWNLOADED bytes — never trusts
    Relay's Content-Type header alone, mirrors the same discipline Relay
    itself applies to uploads (see G:\\SienaRelay\\app\\attachments\\mime.py)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _chunk_text_utf8_safe(text: str, max_bytes: int) -> list[str]:
    """Splits text into chunks whose UTF-8 encoding never exceeds max_bytes,
    never splitting inside a multi-byte codepoint. A UTF-8 continuation byte
    always has the top bits `10xxxxxx` (0x80-0xBF); if the first EXCLUDED
    byte is a continuation byte, the cut lands mid-codepoint and must back
    off until it doesn't."""
    if not text:
        return []
    encoded = text.encode("utf-8")
    total = len(encoded)
    chunks: list[str] = []
    start = 0
    while start < total:
        end = min(start + max_bytes, total)
        while end > start and end < total and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
    return chunks


class RemoteChatService:
    def __init__(
        self,
        *,
        run_chat_turn: Callable[..., Awaitable[dict[str, Any]]],
        conversation_store: Any,
        build_session: Callable[[str], Any],
        create_conversation: Callable[[str | None], str],
        logger_factory: Callable[[str], Any],
        chat_lock: asyncio.Lock,
        attachment_client: AttachmentClient,
        process_remote_image: Callable[..., Awaitable[dict[str, Any]]],
        max_image_bytes: int,
        on_diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._run_chat_turn = run_chat_turn
        self._conversation_store = conversation_store
        self._build_session = build_session
        self._create_conversation = create_conversation
        self._logger_factory = logger_factory
        self._chat_lock = chat_lock
        self._attachment_client = attachment_client
        self._process_remote_image = process_remote_image
        self._max_image_bytes = max_image_bytes
        self._on_diagnostic = on_diagnostic or (lambda _event, _fields: None)

        self._active_by_device: dict[str, asyncio.Task] = {}
        self._active_request_id: dict[str, str] = {}

    def _diag(self, event: str, fields: dict[str, Any]) -> None:
        try:
            self._on_diagnostic(event, fields)
        except Exception:
            pass

    async def handle_message(self, message: dict[str, Any], send: Callable[[dict[str, Any]], Awaitable[None]], gateway_id: str) -> None:
        message_type = message.get("type")
        if message_type == "chat.request":
            await self._handle_chat_request(message, send, gateway_id)
        elif message_type == "chat.cancel":
            await self._handle_chat_cancel(message)

    async def shutdown(self) -> None:
        """Cancels every in-flight remote generation — called from the
        FastAPI lifespan shutdown so no orphaned task keeps running (best
        effort — see run_chat_turn re: asyncio.to_thread not being forcibly
        preemptible)."""
        tasks = [task for task in self._active_by_device.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- chat.request -----------------------------------------------------

    async def _handle_chat_request(
        self, message: dict[str, Any], send: Callable[[dict[str, Any]], Awaitable[None]], gateway_id: str
    ) -> None:
        try:
            parsed = parse_chat_request(message)
        except ChatProtocolError:
            return

        device_id = parsed.device_id
        existing = self._active_by_device.get(device_id)
        if existing is not None and not existing.done():
            if self._active_request_id.get(device_id) == parsed.request_id:
                return  # idempotent resend of the same in-flight request
            await send(build_chat_failed(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                code="generation_already_active", message="A response is already being generated.",
            ))
            return

        task = asyncio.create_task(self._run_chat_generation(parsed, send, gateway_id))
        self._active_by_device[device_id] = task
        self._active_request_id[device_id] = parsed.request_id

        def _cleanup(_task: asyncio.Task, device_id: str = device_id) -> None:
            if self._active_by_device.get(device_id) is _task:
                self._active_by_device.pop(device_id, None)
                self._active_request_id.pop(device_id, None)

        task.add_done_callback(_cleanup)

    async def _run_chat_generation(
        self, parsed: Any, send: Callable[[dict[str, Any]], Awaitable[None]], gateway_id: str
    ) -> None:
        self._diag("remote_chat_received", {"request_id": parsed.request_id})
        try:
            local_conversation_id, is_new_conversation = self._resolve_local_conversation(parsed.conversation_id, gateway_id)
            logger = self._logger_factory(local_conversation_id)
            session = self._build_session(local_conversation_id)

            await send(build_chat_accepted(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id, message_id=parsed.message_id,
            ))
            self._diag("remote_chat_accepted", {"request_id": parsed.request_id})

            image_ocr_context, image_vision_context, ocr_results, vision_results = await self._process_attachments(
                parsed, logger,
            )

            async with self._chat_lock:
                user_message = self._conversation_store.append_message(
                    local_conversation_id, "user", parsed.text,
                    metadata={"status": "processing", "source": "siena_remote"},
                )
                self._diag("remote_chat_stream_started", {"request_id": parsed.request_id})
                try:
                    turn_result = await self._run_chat_turn(
                        text=parsed.text,
                        conversation_id=local_conversation_id,
                        logger=logger,
                        session=session,
                        user_message=user_message,
                        image_ocr_context=image_ocr_context,
                        image_vision_context=image_vision_context,
                        ocr_results=ocr_results,
                        vision_results=vision_results,
                    )
                except Exception:
                    self._diag("remote_chat_failed", {"request_id": parsed.request_id})
                    await send(build_chat_failed(
                        request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                        code="generation_failed", message="The response could not be generated.",
                    ))
                    return

            await self._stream_answer(send, parsed, turn_result)
            self._diag("remote_chat_completed", {"request_id": parsed.request_id})

            # Conversation title (bugfix): the FIRST completed user turn of a
            # brand-new remote conversation gets the exact same deterministic
            # title storage/conversation_store.py::append_message already
            # assigned server-side — pushed to the one Device that owns it so
            # Android's Room ConversationEntity.title updates without a
            # restart, instead of the phone silently keeping its own
            # locally-derived title. Never logs the title text itself.
            if is_new_conversation:
                title = generate_conversation_title(parsed.text)
                await send(build_conversation_title(
                    conversation_id=parsed.conversation_id,
                    device_id=parsed.device_id,
                    title=title,
                ))
                self._diag("remote_chat_title_sent", {"request_id": parsed.request_id})
        except asyncio.CancelledError:
            self._diag("remote_chat_cancelled", {"request_id": parsed.request_id})
            await send(build_chat_cancelled(request_id=parsed.request_id, conversation_id=parsed.conversation_id))
            raise
        except Exception:
            self._diag("remote_chat_failed", {"request_id": parsed.request_id})
            await send(build_chat_failed(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                code="internal_error", message="Internal error.",
            ))

    def _resolve_local_conversation(self, remote_conversation_id: str, gateway_id: str) -> tuple[str, bool]:
        """Returns (local_conversation_id, is_new) — `is_new` tells the
        caller whether this is the very first turn of a brand-new local
        conversation (used to decide whether to push conversation.title)."""
        link = self._conversation_store.get_remote_link(remote_conversation_id)
        if link is not None:
            return link["conversation_id"], False
        local_conversation_id = self._create_conversation(None)
        self._conversation_store.create_remote_link(remote_conversation_id, gateway_id, local_conversation_id)
        # A concurrent first message for the same remote_conversation_id can
        # race here (two devices/tasks both see no link and both create one)
        # — create_remote_link() is ON CONFLICT DO NOTHING, so re-read to
        # make sure every caller converges on the SAME winning conversation.
        # (In that rare race, both callers report is_new=True and a
        # conversation.title may be sent twice — harmless, Android's update
        # is idempotent.)
        return self._conversation_store.get_remote_link(remote_conversation_id)["conversation_id"], True

    async def _process_attachments(self, parsed: Any, logger: Any) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
        ocr_blocks: list[str] = []
        vision_blocks: list[str] = []
        ocr_results: list[dict[str, Any]] = []
        vision_results: list[dict[str, Any]] = []

        for index, attachment in enumerate(parsed.attachments):
            attachment_id = attachment["attachment_id"]
            try:
                data = await self._attachment_client.download_device_attachment(attachment_id)
            except AttachmentClientError:
                continue
            self._diag("remote_attachment_downloaded", {"request_id": parsed.request_id})

            try:
                if len(data) > self._max_image_bytes:
                    continue
                mime = _sniff_image_mime(data)
                if mime is None:
                    continue

                target_language = attachment.get("target_language")
                if target_language is not None and target_language not in ALLOWED_TARGET_LANGUAGES:
                    target_language = None

                result = await self._process_remote_image(
                    image_bytes=data,
                    mime_type=mime,
                    action=attachment["action"],
                    user_text=parsed.text,
                    target_language=target_language,
                    logger=logger,
                    image_index=index,
                )
            finally:
                await self._attachment_client.delete_attachment(attachment_id)

            if result.get("ocr_context"):
                ocr_blocks.append(result["ocr_context"])
            if result.get("vision_context"):
                vision_blocks.append(result["vision_context"])
            if result.get("ocr_result"):
                ocr_results.append(result["ocr_result"])
            if result.get("vision_result"):
                vision_results.append(result["vision_result"])

        return "\n\n".join(ocr_blocks), "\n\n".join(vision_blocks), ocr_results, vision_results

    async def _stream_answer(
        self, send: Callable[[dict[str, Any]], Awaitable[None]], parsed: Any, turn_result: dict[str, Any],
    ) -> None:
        answer = turn_result["answer"]
        assistant_message_id = f"msg_{turn_result['assistant_message_id']}"

        encoded_len = len(answer.encode("utf-8"))
        if encoded_len > MAX_TOTAL_RESPONSE_BYTES:
            truncated = _chunk_text_utf8_safe(answer, MAX_TOTAL_RESPONSE_BYTES)[0]
            answer = truncated

        chunks = _chunk_text_utf8_safe(answer, MAX_DELTA_TEXT_BYTES)
        sequence = 0
        for chunk in chunks:
            sequence += 1
            await send(build_chat_delta(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                message_id=assistant_message_id, sequence=sequence, text=chunk,
            ))

        await send(build_chat_completed(
            request_id=parsed.request_id, conversation_id=parsed.conversation_id,
            message_id=assistant_message_id, sequence=sequence,
            finish_reason="stop",
            usage={"input_tokens": None, "output_tokens": None},
        ))

    # ---- chat.cancel --------------------------------------------------------

    async def _handle_chat_cancel(self, message: dict[str, Any]) -> None:
        try:
            parsed = parse_chat_cancel(message)
        except ChatProtocolError:
            return
        for device_id, task in list(self._active_by_device.items()):
            if self._active_request_id.get(device_id) == parsed.request_id and not task.done():
                task.cancel()
                return
