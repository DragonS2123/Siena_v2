"""SQLite-хранилище embedding-векторов памяти (memory/memory_vectors.sqlite3).

Отдельная база от long_memory/short_memory/candidate_memory — это чисто
технический retrieval-индекс. Runtime не принимает здесь смысловых решений:
единственная логика — CRUD по векторам и (в store-классах, не здесь) сортировка
кандидатов по cosine similarity.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

from core.errors import SienaInfraError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_vectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(memory_type, memory_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_memory_vectors_type ON memory_vectors(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_vectors_memory_id ON memory_vectors(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_vectors_model ON memory_vectors(embedding_model);
CREATE INDEX IF NOT EXISTS idx_memory_vectors_text_hash ON memory_vectors(text_hash);
"""


class VectorStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось инициализировать memory_vectors.sqlite3: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось открыть memory_vectors.sqlite3: {exc}") from exc

    def upsert(
        self,
        memory_type: str,
        memory_id: str,
        embedding_model: str,
        vector: list[float],
        text_hash: str,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        blob = np.asarray(vector, dtype=np.float32).tobytes()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_vectors
                        (memory_type, memory_id, embedding_model, vector, dimension, text_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(memory_type, memory_id, embedding_model)
                    DO UPDATE SET vector = excluded.vector,
                                  dimension = excluded.dimension,
                                  text_hash = excluded.text_hash,
                                  updated_at = excluded.updated_at
                    """,
                    (memory_type, memory_id, embedding_model, blob, len(vector), text_hash, now, now),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка записи в memory_vectors.sqlite3: {exc}") from exc

    def get_by_type(self, memory_type: str, embedding_model: str) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT memory_id, vector, dimension, text_hash FROM memory_vectors
                    WHERE memory_type = ? AND embedding_model = ?
                    """,
                    (memory_type, embedding_model),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения memory_vectors.sqlite3: {exc}") from exc

        result = []
        for row in rows:
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            result.append({"memory_id": row["memory_id"], "vector": vector, "text_hash": row["text_hash"]})
        return result

    def delete(self, memory_type: str, memory_id: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM memory_vectors WHERE memory_type = ? AND memory_id = ?",
                    (memory_type, memory_id),
                )
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка удаления из memory_vectors.sqlite3: {exc}") from exc
