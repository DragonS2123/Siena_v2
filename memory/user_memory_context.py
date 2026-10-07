"""Bounded relevant facts for this user query, never the full memory store."""
from __future__ import annotations

import json
from typing import Any
from memory.long_memory_store import LongMemoryStore
from memory.policy import MAX_FACTS, RETRIEVAL_CHARS


def build_user_memory_context(long_store: LongMemoryStore, query: str = '', *, limit: int = MAX_FACTS,
                              max_chars: int = RETRIEVAL_CHARS) -> str:
    try:
        facts = long_store.search(query, limit=min(limit, MAX_FACTS))
        selected = []
        for fact in facts:
            row = {key: fact[key] for key in ('id', 'text', 'source')}
            encoded = json.dumps({'memory_v1': selected + [row]}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
            if len(encoded) <= min(max_chars, RETRIEVAL_CHARS):
                selected.append(row)
        return json.dumps({'memory_v1': selected}, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e') if selected else ''
    except Exception:
        return ''  # Memory retrieval must not prevent an otherwise healthy chat.


def memory_context_event_fields(context: str, requested_limit: int = MAX_FACTS) -> dict[str, Any]:
    return {'count': len(json.loads(context)['memory_v1']) if context else 0,
            'characters': len(context), 'limit': min(requested_limit, MAX_FACTS), 'character_budget': RETRIEVAL_CHARS}
