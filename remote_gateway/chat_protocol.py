"""Application-protocol (chat/attachments/tts) message shapes exchanged with
Relay 0.6.0, synchronized with G:\\SienaRelay\\app\\chat\\models.py (read-only
reference — Relay is the contract owner). This module only covers messages
the Home Gateway receives from Relay (chat.request, chat.cancel, tts.request)
and the ones it sends back (chat.accepted/delta/completed/failed/cancelled,
tts.ready/failed) — it never talks to Ollama, storage, or anything else.

Parsing here is defensive-but-light: Relay has already strictly validated
these messages before forwarding them, so this module only guards against
malformed/unexpected shapes reaching the chat pipeline (never trust a
network boundary blindly, even a semi-trusted one).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

ALLOWED_ATTACHMENT_ACTIONS = frozenset({"auto", "analyze", "ocr", "translate"})
ALLOWED_ATTACHMENT_KINDS = frozenset({"image"})


class ChatProtocolError(ValueError):
    pass


def _require_str(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value:
        raise ChatProtocolError(f"missing or invalid '{key}'")
    return value


@dataclass(slots=True)
class ChatRequestMessage:
    request_id: str
    conversation_id: str
    message_id: str
    device_id: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)


def parse_chat_request(message: dict[str, Any]) -> ChatRequestMessage:
    request_id = _require_str(message, "request_id")
    conversation_id = _require_str(message, "conversation_id")
    message_id = _require_str(message, "message_id")
    device_id = _require_str(message, "device_id")
    text = message.get("text")
    if not isinstance(text, str):
        raise ChatProtocolError("missing or invalid 'text'")

    raw_attachments = message.get("attachments") or []
    if not isinstance(raw_attachments, list):
        raise ChatProtocolError("'attachments' must be a list")

    attachments: list[dict[str, Any]] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            raise ChatProtocolError("attachment entry must be an object")
        attachment_id = raw.get("attachment_id")
        kind = raw.get("kind")
        action = raw.get("action")
        if not isinstance(attachment_id, str) or not attachment_id:
            raise ChatProtocolError("attachment missing attachment_id")
        if kind not in ALLOWED_ATTACHMENT_KINDS:
            raise ChatProtocolError(f"unsupported attachment kind: {kind!r}")
        if action not in ALLOWED_ATTACHMENT_ACTIONS:
            raise ChatProtocolError(f"unsupported attachment action: {action!r}")
        attachments.append({
            "attachment_id": attachment_id,
            "kind": kind,
            "action": action,
            "target_language": raw.get("target_language"),
        })

    return ChatRequestMessage(
        request_id=request_id,
        conversation_id=conversation_id,
        message_id=message_id,
        device_id=device_id,
        text=text,
        attachments=attachments,
    )


@dataclass(slots=True)
class ChatCancelMessage:
    request_id: str
    conversation_id: str


def parse_chat_cancel(message: dict[str, Any]) -> ChatCancelMessage:
    return ChatCancelMessage(
        request_id=_require_str(message, "request_id"),
        conversation_id=_require_str(message, "conversation_id"),
    )


@dataclass(slots=True)
class TtsRequestMessage:
    request_id: str
    conversation_id: str
    message_id: str
    device_id: str
    text: str
    language: str | None


def parse_tts_request(message: dict[str, Any]) -> TtsRequestMessage:
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ChatProtocolError("missing or invalid 'text'")
    language = message.get("language")
    if language is not None and not isinstance(language, str):
        raise ChatProtocolError("'language' must be a string when present")
    return TtsRequestMessage(
        request_id=_require_str(message, "request_id"),
        conversation_id=_require_str(message, "conversation_id"),
        message_id=_require_str(message, "message_id"),
        device_id=_require_str(message, "device_id"),
        text=text,
        language=language,
    )


# ---- outbound builders -----------------------------------------------------

def build_chat_accepted(*, request_id: str, conversation_id: str, message_id: str) -> dict[str, Any]:
    return {
        "type": "chat.accepted",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }


def build_chat_delta(
    *, request_id: str, conversation_id: str, message_id: str, sequence: int, text: str,
) -> dict[str, Any]:
    return {
        "type": "chat.delta",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "sequence": sequence,
        "text": text,
    }


def build_chat_completed(
    *,
    request_id: str,
    conversation_id: str,
    message_id: str,
    sequence: int,
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "chat.completed",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "sequence": sequence,
        "finish_reason": finish_reason,
        "usage": usage or {},
    }


def build_chat_failed(
    *, request_id: str, conversation_id: str, code: str, message: str,
) -> dict[str, Any]:
    return {
        "type": "chat.failed",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "code": code,
        "message": message,
    }


def build_chat_cancelled(*, request_id: str, conversation_id: str) -> dict[str, Any]:
    return {
        "type": "chat.cancelled",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
    }


def build_tts_ready(
    *,
    request_id: str,
    conversation_id: str,
    attachment_id: str,
    mime_type: str,
    size_bytes: int,
    duration_ms: float | None,
) -> dict[str, Any]:
    return {
        "type": "tts.ready",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "attachment_id": attachment_id,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "duration_ms": duration_ms,
    }


# Strips control characters and Markdown-ish emphasis markers (the title
# comes from a deterministic truncation of plain user text, never the model,
# so this is just belt-and-braces before it ever reaches Relay/Android).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKDOWN_CHARS_RE = re.compile(r"[*_`#\[\]]")
MAX_TITLE_CHARS = 80


def sanitize_conversation_title(title: str) -> str:
    """Trims, strips control/Markdown characters, and caps at
    MAX_TITLE_CHARS — matches the Relay-side validation
    (conversation.title, 1-80 chars, no control chars, no markdown) so a
    title that already passes this never gets rejected downstream."""
    cleaned = _MARKDOWN_CHARS_RE.sub("", _CONTROL_CHARS_RE.sub("", title)).strip()
    return cleaned[:MAX_TITLE_CHARS] or "New Chat"


def build_conversation_title(
    *, conversation_id: str, device_id: str, title: str,
) -> dict[str, Any]:
    """Gateway -> Relay -> the one Device that owns `conversation_id`.
    Includes `device_id` explicitly because Relay has no persistent
    conversation_id -> device_id mapping (chat routing there is keyed by
    ephemeral in-flight request_id) — see G:\\SienaRelay\\app\\chat\\gateway_handlers.py.
    Never persisted by Relay, never logged with the title text (see that
    module's audit-safe-fields convention)."""
    return {
        "type": "conversation.title",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": f"title-{secrets.token_hex(8)}",
        "conversation_id": conversation_id,
        "device_id": device_id,
        "title": sanitize_conversation_title(title),
    }


def build_tts_failed(
    *, request_id: str, conversation_id: str, code: str, message: str,
) -> dict[str, Any]:
    return {
        "type": "tts.failed",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "code": code,
        "message": message,
    }
