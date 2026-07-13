"""Bridges Relay's tts.request into the SAME production VoiceService the
local React UI uses (current provider/voice/settings — the phone may only
pass `language`, never provider/model/path). Synthesizes to a temp WAV file
exactly like /api/voice/synthesize, uploads the bytes to Relay for the
requesting Device via the Gateway attachment endpoint, then deletes the local
temp file. One active TTS request per Device. Never logs the text being
spoken.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from remote_gateway.attachment_client import AttachmentClient, AttachmentClientError
from remote_gateway.chat_protocol import (
    ChatProtocolError,
    build_tts_failed,
    build_tts_ready,
    parse_tts_request,
)


class RemoteTtsService:
    def __init__(
        self,
        *,
        synthesize: Callable[[str], Awaitable[dict[str, Any]]],
        sanitize_text: Callable[[str], str],
        attachment_client: AttachmentClient,
        on_diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._synthesize = synthesize
        self._sanitize_text = sanitize_text
        self._attachment_client = attachment_client
        self._on_diagnostic = on_diagnostic or (lambda _event, _fields: None)

        self._active_by_device: dict[str, asyncio.Task] = {}

    def _diag(self, event: str, fields: dict[str, Any]) -> None:
        try:
            self._on_diagnostic(event, fields)
        except Exception:
            pass

    async def handle_message(self, message: dict[str, Any], send: Callable[[dict[str, Any]], Awaitable[None]], gateway_id: str) -> None:
        if message.get("type") != "tts.request":
            return
        try:
            parsed = parse_tts_request(message)
        except ChatProtocolError:
            return

        device_id = parsed.device_id
        existing = self._active_by_device.get(device_id)
        if existing is not None and not existing.done():
            await send(build_tts_failed(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                code="tts_already_active", message="A voice response is already being generated.",
            ))
            return

        task = asyncio.create_task(self._run_tts(parsed, send))
        self._active_by_device[device_id] = task

        def _cleanup(_task: asyncio.Task, device_id: str = device_id) -> None:
            if self._active_by_device.get(device_id) is _task:
                self._active_by_device.pop(device_id, None)

        task.add_done_callback(_cleanup)

    async def shutdown(self) -> None:
        tasks = [task for task in self._active_by_device.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_tts(self, parsed: Any, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._diag("remote_tts_started", {"request_id": parsed.request_id})
        audio_path: str | None = None
        try:
            sanitized = self._sanitize_text(parsed.text)
            if not sanitized.strip():
                await send(build_tts_failed(
                    request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                    code="empty_text", message="Nothing left to speak after sanitizing the text.",
                ))
                return

            result = await self._synthesize(sanitized)
            audio_path = result.get("audio_path")
            if not audio_path or not os.path.isfile(audio_path):
                await send(build_tts_failed(
                    request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                    code="synthesis_failed", message="Voice synthesis did not produce an audio file.",
                ))
                return

            with open(audio_path, "rb") as handle:
                audio_bytes = handle.read()

            try:
                uploaded = await self._attachment_client.upload_gateway_attachment(
                    parsed.device_id, data=audio_bytes, content_type="audio/wav",
                )
            except AttachmentClientError:
                await send(build_tts_failed(
                    request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                    code="upload_failed", message="Could not deliver the generated audio.",
                ))
                return

            duration_sec = result.get("duration_sec")
            duration_ms = round(duration_sec * 1000) if isinstance(duration_sec, (int, float)) else None

            await send(build_tts_ready(
                request_id=parsed.request_id,
                conversation_id=parsed.conversation_id,
                attachment_id=uploaded["attachment_id"],
                mime_type=uploaded.get("mime_type", "audio/wav"),
                size_bytes=uploaded.get("size_bytes", len(audio_bytes)),
                duration_ms=duration_ms,
            ))
            self._diag("remote_tts_completed", {"request_id": parsed.request_id})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._diag("remote_tts_failed", {"request_id": parsed.request_id})
            await send(build_tts_failed(
                request_id=parsed.request_id, conversation_id=parsed.conversation_id,
                code="internal_error", message="Internal error.",
            ))
        finally:
            if audio_path is not None:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
