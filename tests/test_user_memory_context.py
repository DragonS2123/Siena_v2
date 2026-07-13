"""Remote chat memory parity fix.

Root cause was NOT a code bug — desktop and remote already use the exact
same DB paths (config.LONG_MEMORY_DB_PATH) and the exact same shared
run_chat_turn() pipeline (api/server.py). The real gap: desktop keeps a
warm, cumulative Session across turns, while a remote turn rebuilds a cold
Session every time (RemoteChatService._build_session), so anything the
model would only "remember" via prior conversation turns is invisible on
a cold remote turn even though the same facts exist in the same DB.

memory/user_memory_context.py closes this gap deterministically: every
single run_chat_turn() invocation (desktop AND remote) now injects a
[USER_MEMORY_CONTEXT] block built directly from LongMemoryStore, so a
high-importance fact like the user's name no longer depends on the model
choosing to call long_memory_search itself, nor on a warm Session.

This suite locks in: (1) the deterministic block includes high-importance
facts and excludes low/medium-importance ones, (2) it never includes raw
DB/store internals or content beyond the fact text itself in its
diagnostics event, (3) desktop and remote genuinely read from the SAME
store instance/db file (parity, not just "same code").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.long_memory_store import LongMemoryStore  # noqa: E402
from memory.user_memory_context import (  # noqa: E402
    build_user_memory_context,
    memory_context_event_fields,
)


def _store(tmp_path: Path) -> LongMemoryStore:
    return LongMemoryStore(tmp_path / "long_memory.sqlite3", search_hard_limit=200)


def test_empty_store_yields_empty_context(tmp_path):
    store = _store(tmp_path)
    assert build_user_memory_context(store) == ""


def test_high_importance_fact_is_included(tmp_path):
    store = _store(tmp_path)
    store.save("Пользователя зовут Максим", category="identity", importance="high")

    context = build_user_memory_context(store)

    assert "[USER_MEMORY_CONTEXT]" in context
    assert "Пользователя зовут Максим" in context
    assert "[/USER_MEMORY_CONTEXT]" in context


def test_low_and_medium_importance_facts_are_excluded(tmp_path):
    store = _store(tmp_path)
    store.save("Любит дабстеп", category="preference", importance="low")
    store.save("Живёт в Москве", category="fact", importance="medium")

    assert build_user_memory_context(store) == ""


def test_only_high_importance_facts_are_included_among_mixed(tmp_path):
    store = _store(tmp_path)
    store.save("Любит дабстеп", category="preference", importance="low")
    store.save("Пользователя зовут Максим", category="identity", importance="high")

    context = build_user_memory_context(store)

    assert "Максим" in context
    assert "дабстеп" not in context


def test_respects_limit(tmp_path):
    store = _store(tmp_path)
    for i in range(5):
        store.save(f"Факт номер {i}", category="fact", importance="high")

    context = build_user_memory_context(store, limit=2)
    fact_lines = [line for line in context.splitlines() if line.startswith("- ")]
    assert len(fact_lines) == 2


def test_long_fact_text_is_truncated(tmp_path):
    store = _store(tmp_path)
    long_text = "x" * 500
    store.save(long_text, category="fact", importance="high")

    context = build_user_memory_context(store)
    fact_line = next(line for line in context.splitlines() if line.startswith("- "))
    assert len(fact_line) < 500
    assert fact_line.endswith("…")


def test_store_failure_yields_empty_context_not_exception(tmp_path, monkeypatch):
    store = _store(tmp_path)

    def boom(limit=20):
        raise RuntimeError("db is locked")

    monkeypatch.setattr(store, "list_high_importance", boom)
    assert build_user_memory_context(store) == ""


def test_event_fields_never_carry_fact_text(tmp_path):
    store = _store(tmp_path)
    store.save("Пользователя зовут Максим, его пароль supersecret123", category="identity", importance="high")

    context = build_user_memory_context(store)
    fields = memory_context_event_fields(context)

    assert fields == {"count": 1, "limit": 8}
    serialized = repr(fields)
    assert "Максим" not in serialized
    assert "supersecret123" not in serialized


def test_event_fields_empty_context_reports_zero_count():
    assert memory_context_event_fields("") == {"count": 0}


def test_desktop_and_remote_share_the_same_db_path_and_content(tmp_path):
    # Simulates the real parity fix: both "sides" construct their own
    # LongMemoryStore instance (exactly like build_registry() does per
    # request/session in api/server.py), but pointed at the identical
    # config.LONG_MEMORY_DB_PATH file — so a fact saved via one instance
    # is immediately visible to the other, with no separate remote DB.
    db_path = tmp_path / "long_memory.sqlite3"
    desktop_store = LongMemoryStore(db_path, search_hard_limit=200)
    desktop_store.save("Пользователя зовут Максим", category="identity", importance="high")

    remote_store = LongMemoryStore(db_path, search_hard_limit=200)
    remote_context = build_user_memory_context(remote_store)

    assert "Максим" in remote_context
