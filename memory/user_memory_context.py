"""Surface confirmed high-importance facts in each local chat turn.

The model can still search memory for facts outside this compact context.
Only facts previously marked important are included.
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
