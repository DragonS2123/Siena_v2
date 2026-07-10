"""SQLite-хранилище долговременной памяти. Store не решает, что важно и что сохранить —
category/importance приходят только от модели через аргументы tool call (см.
ARCHITECTURE.md, раздел 5.2). source/metadata_json — технические поля, заполняются Runtime.

Поиск — гибридный: vector similarity (memory/embedding_service.py +
memory/vector_store.py) первично, keyword+fuzzy ranking (memory/search.py) —
fallback, если embedding-модель недоступна или не дала результатов выше порога.
Runtime не решает, когда искать — это по-прежнему решает модель, вызывая
long_memory_search; какой из двух механизмов сработал — техническая деталь
retrieval, не влияющая на tool-контракт (см. ARCHITECTURE.md, раздел 9).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from core.errors import SienaInfraError
from memory import search as keyword_search
from memory.embedding_service import EmbeddingService
from memory.vector_store import VectorStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS long_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    text TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    source TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_long_memory_text ON long_memory(text);
CREATE INDEX IF NOT EXISTS idx_long_memory_category ON long_memory(category);
CREATE INDEX IF NOT EXISTS idx_long_memory_importance ON long_memory(importance);
CREATE INDEX IF NOT EXISTS idx_long_memory_created_at ON long_memory(created_at);
"""

_MEMORY_TYPE = "long"
_KEYWORD_FETCH_BOUND = 2000  # технический предохранитель для fallback-поиска, не смысловое решение


class LongMemoryStore:
    def __init__(
        self,
        db_path: Path,
        search_hard_limit: int = 200,
        embedding_service: EmbeddingService | None = None,
        vector_store: VectorStore | None = None,
        embedding_search_limit: int = 50,
        embedding_min_score: float = 0.35,
        logger: Any | None = None,
    ):
        self._db_path = db_path
        self._search_hard_limit = search_hard_limit
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._embedding_search_limit = embedding_search_limit
        self._embedding_min_score = embedding_min_score
        self._logger = logger
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось инициализировать long_memory.sqlite3: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось открыть long_memory.sqlite3: {exc}") from exc

    def save(
        self,
        text: str,
        category: str | None = None,
        importance: str | None = None,
        source: str = "siena_v2",
        metadata: dict | None = None,
    ) -> dict:
        now = datetime.now().astimezone().isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO long_memory (created_at, updated_at, text, category, importance, source, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now, now, text, category, importance, source, metadata_json),
                )
                row_id = cur.lastrowid
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка записи в long_memory.sqlite3: {exc}") from exc

        # Embedding — техническая retrieval-оптимизация, не часть решения "что сохранить".
        # Сбой здесь никогда не должен ронять уже выполненное сохранение факта.
        self.index_vector(row_id, text, category)

        return {
            "id": row_id,
            "created_at": now,
            "text": text,
            "category": category,
            "importance": importance,
        }

    def index_vector(self, row_id: int, text: str, category: str | None) -> None:
        if self._embedding_service is None or self._vector_store is None:
            return
        try:
            if not self._embedding_service.is_available():
                return
            combined = f"{text} {category or ''}".strip()
            vector = self._embedding_service.encode_document(combined)
            text_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
            self._vector_store.upsert(_MEMORY_TYPE, str(row_id), self._embedding_service.model_name, vector, text_hash)
        except Exception as exc:  # noqa: BLE001 — embedding — best-effort, никогда не фатально
            if self._logger is not None:
                self._logger.event(
                    "embedding_error",
                    memory_id=row_id,
                    error=str(exc),
                    console_message=f"[EMBEDDING][LONG] индексация id={row_id} не удалась: {exc}",
                )

    def search(self, query: str, limit: int = 50) -> list[dict]:
        limit = min(max(limit, 1), self._search_hard_limit)
        vector_hits = self._vector_search(query, limit)
        if vector_hits:
            return vector_hits
        return self._keyword_search(query, limit)

    def _vector_search(self, query: str, limit: int) -> list[dict]:
        if self._embedding_service is None or self._vector_store is None:
            return []
        try:
            if not self._embedding_service.is_available():
                return []
            query_vector = self._embedding_service.encode_query(query)
            candidates = self._vector_store.get_by_type(_MEMORY_TYPE, self._embedding_service.model_name)
            if not candidates:
                return []

            from memory.embedding_service import cosine_similarity

            scored = []
            for candidate in candidates:
                sim = cosine_similarity(query_vector, candidate["vector"])
                if sim >= self._embedding_min_score:
                    scored.append((sim, candidate["memory_id"]))
            if not scored:
                return []

            scored.sort(key=lambda pair: pair[0], reverse=True)
            top = scored[: min(limit, self._embedding_search_limit)]
            id_to_score = {memory_id: sim for sim, memory_id in top}
            rows_by_id = self._fetch_rows_by_ids([memory_id for _, memory_id in top])

            result = []
            for _, memory_id in top:
                row = rows_by_id.get(memory_id)
                if row is not None:
                    row = dict(row)
                    row["score"] = id_to_score[memory_id]
                    result.append(row)
            return result
        except Exception as exc:  # noqa: BLE001 — любой сбой embedding-поиска -> fallback на keyword
            if self._logger is not None:
                self._logger.event(
                    "embedding_error",
                    query=query,
                    error=str(exc),
                    console_message=f"[EMBEDDING][LONG] поиск не удался, fallback на keyword: {exc}",
                )
            return []

    def _fetch_rows_by_ids(self, ids: list[str]) -> dict[str, sqlite3.Row]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id, created_at, updated_at, text, category, importance, source
                    FROM long_memory WHERE id IN ({placeholders})
                    """,
                    [int(i) for i in ids],
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения long_memory.sqlite3: {exc}") from exc
        return {str(row["id"]): row for row in rows}

    def _keyword_search(self, query: str, limit: int) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, created_at, updated_at, text, category, importance, source
                    FROM long_memory
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (_KEYWORD_FETCH_BOUND,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения long_memory.sqlite3: {exc}") from exc

        candidates = [dict(row) for row in rows]
        return keyword_search.rank(query, candidates, ["text", "category"], limit)

    def list_recent(self, limit: int = 20) -> list[dict]:
        limit = min(max(limit, 1), self._search_hard_limit)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, created_at, updated_at, text, category, importance, source
                    FROM long_memory
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения long_memory.sqlite3: {exc}") from exc

        return [dict(row) for row in rows]
