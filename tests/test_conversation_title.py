"""Conversation title generation + remote delivery bugfix.

Root cause: Siena_v2 already generated a deterministic title server-side
for every conversation (storage/conversation_store.py::append_message, same
code path for desktop and remote) — the actual gap was pure delivery:
nothing ever pushed that title to Android, so the phone fell back to
deriving its own title locally from the first message, ignoring
Siena_v2's real title logic/phrasing entirely.

This suite covers: (1) generate_conversation_title's deterministic
derivation (extracted out of append_message, unchanged behavior), (2)
sanitize_conversation_title's safety net (control chars, Markdown,
1-80 char cap, never empty), (3) build_conversation_title's wire shape
(exactly matches what remote_gateway/remote_chat_service.py sends and what
Relay/Android are expected to parse) and that it never leaks anything
beyond the sanitized title text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_gateway.chat_protocol import (  # noqa: E402
    MAX_TITLE_CHARS,
    build_conversation_title,
    sanitize_conversation_title,
)
from storage.conversation_store import generate_conversation_title  # noqa: E402


# --- generate_conversation_title (shared desktop/remote logic) -------------

def test_generate_title_uses_first_message_verbatim_when_short():
    assert generate_conversation_title("Как меня зовут?") == "Как меня зовут?"


def test_generate_title_collapses_internal_whitespace():
    assert generate_conversation_title("привет   мир\n\nкак дела") == "привет мир как дела"


def test_generate_title_truncates_to_40_chars():
    text = "x" * 100
    title = generate_conversation_title(text)
    assert len(title) == 40


def test_generate_title_empty_text_falls_back_to_default():
    assert generate_conversation_title("") == "New Chat"
    assert generate_conversation_title("   ") == "New Chat"


# --- sanitize_conversation_title (belt-and-braces before Relay/Android) ----

def test_sanitize_strips_control_characters():
    assert sanitize_conversation_title("hello\x00\x07world") == "helloworld"


def test_sanitize_strips_markdown_emphasis_markers():
    assert sanitize_conversation_title("**bold** _italic_ `code` #heading [link]") == "bold italic code heading link"


def test_sanitize_caps_at_max_title_chars():
    title = sanitize_conversation_title("y" * 200)
    assert len(title) == MAX_TITLE_CHARS == 80


def test_sanitize_empty_or_whitespace_only_falls_back():
    assert sanitize_conversation_title("") == "New Chat"
    assert sanitize_conversation_title("   \x01\x02  ") == "New Chat"


def test_sanitize_trims_surrounding_whitespace():
    assert sanitize_conversation_title("  hi there  ") == "hi there"


# --- build_conversation_title (the wire message) ----------------------------

def test_build_conversation_title_shape():
    message = build_conversation_title(
        conversation_id="conv_remote_abc", device_id="dev_xyz", title="Как меня зовут?",
    )

    assert message["type"] == "conversation.title"
    assert message["conversation_id"] == "conv_remote_abc"
    assert message["device_id"] == "dev_xyz"
    assert message["title"] == "Как меня зовут?"
    assert "request_id" in message and message["request_id"].startswith("title-")
    assert "protocol_version" in message


def test_build_conversation_title_sanitizes_before_sending():
    message = build_conversation_title(
        conversation_id="conv_1", device_id="dev_1", title="**system prompt leak** \x00",
    )
    assert message["title"] == "system prompt leak"


def test_build_conversation_title_request_id_is_unique_per_call():
    first = build_conversation_title(conversation_id="c", device_id="d", title="t")
    second = build_conversation_title(conversation_id="c", device_id="d", title="t")
    assert first["request_id"] != second["request_id"]


def test_build_conversation_title_never_exceeds_80_chars_on_wire():
    message = build_conversation_title(conversation_id="c", device_id="d", title="z" * 500)
    assert len(message["title"]) <= 80
