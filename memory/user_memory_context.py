"""Deterministic user-memory context — surfaces confirmed high-importance
long-term facts (e.g. the user's name) into EVERY chat turn, desktop and
remote alike, WITHOUT depending on the model spontaneously choosing to call
long_memory_search.

Why this exists: run_chat_turn (api/server.py) is the one shared pipeline
both desktop and remote (Android) chat use, and both already carry the soft
SYSTEM_PROMPT instruction "check long_memory before replying" — but remote
turns rebuild a cold, empty Session on every message (see
remote_gateway/remote_chat_service.py), so the model has much less
"momentum" to spontaneously honor that soft instruction than an
already-warmed-up desktop conversation. This module is a hard, deterministic
supplement, not a replacement — the model can still call long_memory_search
itself for anything not covered here (older/lower-importance facts, fuzzy
recall, etc.).

Nothing here is a semantic judgment: which facts are "important" was already
decided by the model at save time (long_memory_save's importance="high"
argument) — this module only deterministically re-surfaces what was already
marked, on every turn, the same way for every caller.
"""

from __future__ import annotations

from typing import Any

from memory.long_memory_store import LongMemoryStore

MAX_FACTS = 8
MAX_FACT_CHARS = 200


def build_user_memory_context(long_store: LongMemoryStore, *, limit: int = MAX_FACTS) -> str:
    """Returns a compact [USER_MEMORY_CONTEXT] block, or "" if there are no
    high-importance facts yet (or on any storage failure — this must never
    crash or block a chat turn; see the try/except below)."""
    try:
        facts = long_store.list_high_importance(limit=limit)
    except Exception:
        return ""
    if not facts:
        return ""

    lines = ["[USER_MEMORY_CONTEXT]", "Подтверждённые важные факты о пользователе (используй, если уместно):"]
    for fact in facts:
        text = (fact.get("text") or "").strip()
        if not text:
            continue
        if len(text) > MAX_FACT_CHARS:
            text = text[:MAX_FACT_CHARS] + "…"
        lines.append(f"- {text}")
    lines.append("[/USER_MEMORY_CONTEXT]")
    return "\n".join(lines) if len(lines) > 3 else ""


def memory_context_event_fields(context: str, requested_limit: int = MAX_FACTS) -> dict[str, Any]:
    """Safe diagnostic fields for memory_context_injected/empty trace events
    — count only, never fact content (see module docstring)."""
    if not context:
        return {"count": 0}
    # Each fact is one "- " line between the two bracket lines.
    fact_lines = [line for line in context.splitlines() if line.startswith("- ")]
    return {"count": len(fact_lines), "limit": requested_limit}
