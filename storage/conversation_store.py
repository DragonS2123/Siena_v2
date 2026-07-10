"""SQLite-хранилище истории переписок (Conversation History).

Важно отличать от memory/long_memory_store.py:
- Long Memory — факты, которые МОДЕЛЬ сама решила сохранить через long_memory_save.
- Conversation History — технический журнал переписки (кто что написал + полный
  trace агента), сохраняется АВТОМАТИЧЕСКИ как обычная persistence-функция
  приложения. Runtime не спрашивает модель, сохранять ли обычное сообщение —
  это не смысловое решение, а инженерная задача "не терять историю чатов".

Store не решает, что показать в UI и как — только CRUD над тремя таблицами.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.errors import SienaInfraError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS conversation_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS conversation_attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    stored_relative_path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    sha256 TEXT,
    metadata_json TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    FOREIGN KEY(message_id) REFERENCES conversation_messages(id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_id
ON conversation_messages(conversation_id);

CREATE INDEX IF NOT EXISTS idx_conversation_events_conversation_id
ON conversation_events(conversation_id);

CREATE INDEX IF NOT EXISTS idx_conversation_attachments_message_id
ON conversation_attachments(message_id);
"""

_DEFAULT_TITLE = "New Chat"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class ConversationStore:
    def __init__(self, db_path: Path, events_limit: int = 300):
        self._db_path = db_path
        self._events_limit = events_limit
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось инициализировать conversations.sqlite3: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось открыть conversations.sqlite3: {exc}") from exc

    # --- Conversations ---

    def create_conversation(self, title: str | None = None) -> str:
        conversation_id = str(uuid.uuid4())
        now = _now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (conversation_id, title or _DEFAULT_TITLE, now, now),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка создания разговора: {exc}") from exc
        return conversation_id

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT c.id, c.title, c.created_at, c.updated_at,
                        (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS message_count,
                        (SELECT content FROM conversation_messages m
                            WHERE m.conversation_id = c.id ORDER BY m.created_at DESC LIMIT 1) AS last_message
                    FROM conversations c
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения списка разговоров: {exc}") from exc

        result = []
        for row in rows:
            d = dict(row)
            last = d.pop("last_message", None)
            preview = (last or "").strip()
            if len(preview) > 120:
                preview = preview[:120] + "…"
            d["last_message_preview"] = preview
            result.append(d)
        return result

    def get_conversation(self, conversation_id: str, events_limit: int | None = None) -> dict[str, Any] | None:
        limit = events_limit if events_limit is not None else self._events_limit
        try:
            with self._connect() as conn:
                conv_row = conn.execute(
                    "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if conv_row is None:
                    return None

                message_rows = conn.execute(
                    """
                    SELECT id, role, content, model, created_at, metadata_json
                    FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                ).fetchall()

                attachment_rows = conn.execute(
                    """
                    SELECT id, conversation_id, message_id, kind, source, original_name,
                           stored_filename, stored_relative_path, mime_type, size_bytes,
                           created_at, sha256, metadata_json
                    FROM conversation_attachments
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                ).fetchall()

                event_rows = conn.execute(
                    """
                    SELECT id, event_type, created_at, payload_json
                    FROM conversation_events WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?
                    """,
                    (conversation_id, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения разговора {conversation_id}: {exc}") from exc

        attachments_by_message: dict[str, list[dict[str, Any]]] = {}
        for row in attachment_rows:
            extra = json.loads(row["metadata_json"] or "{}")
            attachment = {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "kind": row["kind"],
                "source": row["source"],
                "original_name": row["original_name"],
                "stored_filename": row["stored_filename"],
                "stored_relative_path": row["stored_relative_path"],
                "mime_type": row["mime_type"],
                "size_bytes": row["size_bytes"],
                "created_at": row["created_at"],
                "sha256": row["sha256"],
                "url": f"/api/attachments/{row['id']}/content",
                **extra,
            }
            attachments_by_message.setdefault(row["message_id"], []).append(attachment)

        messages = []
        for m in message_rows:
            metadata = json.loads(m["metadata_json"] or "{}")
            attachments = attachments_by_message.get(m["id"], [])
            if attachments:
                metadata = {**metadata, "attachments": attachments}
            messages.append({
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "model": m["model"],
                "created_at": m["created_at"],
                "metadata": metadata,
                "attachments": attachments,
            })
        events = [
            {"id": e["id"], "event": e["event_type"], "ts": e["created_at"], **json.loads(e["payload_json"] or "{}")}
            for e in reversed(event_rows)
        ]

        return {
            "id": conv_row["id"],
            "title": conv_row["title"],
            "created_at": conv_row["created_at"],
            "updated_at": conv_row["updated_at"],
            "messages": messages,
            "events": events,
        }

    def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, conversation_id, message_id, kind, source, original_name,
                           stored_filename, stored_relative_path, mime_type, size_bytes,
                           created_at, sha256, metadata_json
                    FROM conversation_attachments WHERE id = ?
                    """,
                    (attachment_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ attachment {attachment_id}: {exc}") from exc
        if row is None:
            return None
        extra = json.loads(row["metadata_json"] or "{}")
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "message_id": row["message_id"],
            "kind": row["kind"],
            "source": row["source"],
            "original_name": row["original_name"],
            "stored_filename": row["stored_filename"],
            "stored_relative_path": row["stored_relative_path"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "created_at": row["created_at"],
            "sha256": row["sha256"],
            "url": f"/api/attachments/{row['id']}/content",
            **extra,
        }

    def add_attachment(self, attachment: dict[str, Any]) -> dict[str, Any]:
        metadata = attachment.get("metadata") or {}
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO conversation_attachments (
                        id, conversation_id, message_id, kind, source, original_name,
                        stored_filename, stored_relative_path, mime_type, size_bytes,
                        created_at, sha256, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attachment["id"],
                        attachment["conversation_id"],
                        attachment["message_id"],
                        attachment["kind"],
                        attachment["source"],
                        attachment["original_name"],
                        attachment["stored_filename"],
                        attachment["stored_relative_path"],
                        attachment["mime_type"],
                        attachment["size_bytes"],
                        attachment["created_at"],
                        attachment.get("sha256"),
                        metadata_json,
                    ),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"РћС€РёР±РєР° Р·Р°РїРёСЃРё attachment {attachment.get('id')}: {exc}") from exc
        return {
            **attachment,
            "url": f"/api/attachments/{attachment['id']}/content",
        }

    def update_message_metadata(self, message_id: str, metadata: dict[str, Any]) -> None:
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE conversation_messages SET metadata_json = ? WHERE id = ?",
                    (metadata_json, message_id),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Message metadata update failed for {message_id}: {exc}") from exc

    def merge_message_metadata(self, message_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT metadata_json FROM conversation_messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(message_id)
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata.update(patch)
                conn.execute(
                    "UPDATE conversation_messages SET metadata_json = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), message_id),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Message metadata merge failed for {message_id}: {exc}") from exc
        return metadata

    def merge_attachment_metadata(self, attachment_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT metadata_json FROM conversation_attachments WHERE id = ?",
                    (attachment_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(attachment_id)
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata.update(patch)
                conn.execute(
                    "UPDATE conversation_attachments SET metadata_json = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), attachment_id),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Attachment metadata merge failed for {attachment_id}: {exc}") from exc
        return metadata

    def update_title(self, conversation_id: str, title: str) -> None:
        now = _now_iso()
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, conversation_id),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка переименования разговора {conversation_id}: {exc}") from exc

    def delete_conversation(self, conversation_id: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM conversation_attachments WHERE conversation_id = ?", (conversation_id,))
                conn.execute("DELETE FROM conversation_events WHERE conversation_id = ?", (conversation_id,))
                conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conversation_id,))
                conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка удаления разговора {conversation_id}: {exc}") from exc

    # --- Messages & events ---

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_id = str(uuid.uuid4())
        now = _now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        try:
            with self._connect() as conn:
                if role == "user":
                    # Автоматический заголовок — техническая функция приложения,
                    # не смысловое решение модели (только первое user-сообщение
                    # конкретного разговора задаёт заголовок).
                    count_row = conn.execute(
                        "SELECT COUNT(*) AS c FROM conversation_messages WHERE conversation_id = ?",
                        (conversation_id,),
                    ).fetchone()
                    if count_row is not None and count_row["c"] == 0:
                        title = " ".join(content.strip().split())[:40] or _DEFAULT_TITLE
                        conn.execute(
                            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
                        )

                conn.execute(
                    """
                    INSERT INTO conversation_messages (id, conversation_id, role, content, model, created_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (message_id, conversation_id, role, content, model, now, metadata_json),
                )
                conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка записи сообщения в разговор {conversation_id}: {exc}") from exc

        return {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "model": model,
            "created_at": now,
            "metadata": metadata or {},
        }

    def append_event(self, conversation_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event_id = str(uuid.uuid4())
        now = _now_iso()
        payload_json = json.dumps(payload, ensure_ascii=False)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO conversation_events (id, conversation_id, event_type, created_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_id, conversation_id, event_type, now, payload_json),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка записи события в разговор {conversation_id}: {exc}") from exc
