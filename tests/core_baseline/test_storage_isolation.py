from __future__ import annotations

from pathlib import Path

from logging_.logger import SienaLogger
from memory.candidate_memory_store import CandidateMemoryStore
from memory.long_memory_store import LongMemoryStore
from memory.short_memory_store import ShortMemoryStore
from memory.vector_store import VectorStore
from storage.conversation_store import ConversationStore
from storage.settings_store import SettingsStore


def test_settings_store_round_trip_uses_only_tmp_path(tmp_path: Path):
    path = tmp_path / "storage" / "settings.json"
    store = SettingsStore(path)
    store.save({"log_level": "debug"})
    values, error = store.load()
    assert error is None
    assert values["log_level"] == "debug"
    assert path.is_relative_to(tmp_path)


def test_conversation_store_round_trip_uses_only_tmp_path(tmp_path: Path):
    path = tmp_path / "storage" / "conversations.sqlite3"
    store = ConversationStore(path)
    conversation_id = store.create_conversation("Baseline")
    store.append_message(conversation_id, "user", "isolated")
    reloaded = ConversationStore(path).get_conversation(conversation_id)
    assert reloaded is not None
    assert reloaded["title"] == "isolated"
    assert reloaded["messages"][0]["content"] == "isolated"
    assert path.is_relative_to(tmp_path)


def test_all_memory_stores_create_only_under_tmp_path(tmp_path: Path):
    memory_root = tmp_path / "memory"
    short_path = memory_root / "short_memory.json"
    long_path = memory_root / "long_memory.sqlite3"
    candidate_path = memory_root / "candidate_memory.sqlite3"
    vector_path = memory_root / "memory_vectors.sqlite3"

    short = ShortMemoryStore(short_path)
    long = LongMemoryStore(long_path)
    candidate = CandidateMemoryStore(candidate_path)
    VectorStore(vector_path)

    assert short.save("temporary fact")["text"] == "temporary fact"
    assert long.save("temporary long fact")["text"] == "temporary long fact"
    created = candidate.create("o", "i", "r", "temporary candidate")
    assert created["proposed_memory"] == "temporary candidate"
    assert all(path.exists() and path.is_relative_to(tmp_path) for path in (short_path, long_path, candidate_path, vector_path))


def test_attachment_storage_is_redirected_to_isolated_runtime(backend, isolated_runtime: Path):
    root = Path(backend.config.ATTACHMENTS_STORAGE_ROOT)
    assert root.is_relative_to(isolated_runtime)
    conversation_id = backend.conversation_store.create_conversation("attachment")
    message = backend.conversation_store.append_message(conversation_id, "user", "file")
    attachment = backend.ChatAttachment(
        name="note.txt",
        type="text",
        mime="text/plain",
        content="temporary attachment",
    )
    persisted = backend._persist_uploaded_attachments(conversation_id, message["id"], [attachment])
    stored = isolated_runtime / persisted[0]["stored_relative_path"]
    assert stored.read_text(encoding="utf-8") == "temporary attachment"
    assert stored.is_relative_to(isolated_runtime)


def test_logger_writes_only_to_tmp_path(tmp_path: Path):
    log_dir = tmp_path / "logs"
    logger = SienaLogger(log_dir, "error")
    logger.event("core_baseline_event", marker="isolated")
    logs = list(log_dir.glob("*.jsonl"))
    assert len(logs) == 1
    assert logs[0].is_relative_to(tmp_path)
    assert "core_baseline_event" in logs[0].read_text(encoding="utf-8")
