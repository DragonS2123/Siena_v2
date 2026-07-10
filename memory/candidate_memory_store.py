"""SQLite-хранилище кандидатов долговременной памяти (когнитивный цикл Siena:
Observation → Insight → Reflection → Candidate Memory).

Это НЕ Long Memory и намеренно не смешивается с ней (отдельный файл
candidate_memory.sqlite3, отдельная таблица). Observation/Insight/Reflection —
строки, которые формулирует модель и передаёт одним вызовом
candidate_memory_create (см. tools/candidate_memory_tools.py); здесь они
только сохраняются и отдаются обратно, без какой-либо интерпретации.

Human-in-the-loop: promote/reject/later/delete — не tools модели, а
REST-эндпоинты /api/insights/* (api/server.py), вызываемые явным действием
человека в интерфейсе Insights. Store здесь только исполняет CRUD по статусу.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.errors import SienaInfraError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    observation TEXT NOT NULL,
    insight TEXT NOT NULL,
    reflection TEXT NOT NULL,
    proposed_memory TEXT NOT NULL,
    confidence REAL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_memory_status ON candidate_memory(status);
CREATE INDEX IF NOT EXISTS idx_candidate_memory_created_at ON candidate_memory(created_at);
"""

VALID_STATUSES = {"pending", "promoted", "rejected", "later"}


class CandidateMemoryStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось инициализировать candidate_memory.sqlite3: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Не удалось открыть candidate_memory.sqlite3: {exc}") from exc

    def create(
        self,
        observation: str,
        insight: str,
        reflection: str,
        proposed_memory: str,
        confidence: float | None = None,
        category: str | None = None,
        source: str = "siena_v2",
        metadata: dict | None = None,
    ) -> dict:
        now = datetime.now().astimezone().isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO candidate_memory
                        (created_at, updated_at, observation, insight, reflection, proposed_memory,
                         confidence, category, status, source, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (now, now, observation, insight, reflection, proposed_memory, confidence, category, source, metadata_json),
                )
                row_id = cur.lastrowid
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка записи в candidate_memory.sqlite3: {exc}") from exc

        return {
            "id": row_id,
            "created_at": now,
            "updated_at": now,
            "observation": observation,
            "insight": insight,
            "reflection": reflection,
            "proposed_memory": proposed_memory,
            "confidence": confidence,
            "category": category,
            "status": "pending",
        }

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        try:
            with self._connect() as conn:
                if status:
                    rows = conn.execute(
                        """
                        SELECT id, created_at, updated_at, observation, insight, reflection,
                               proposed_memory, confidence, category, status
                        FROM candidate_memory WHERE status = ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT id, created_at, updated_at, observation, insight, reflection,
                               proposed_memory, confidence, category, status
                        FROM candidate_memory
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения candidate_memory.sqlite3: {exc}") from exc

        return [dict(row) for row in rows]

    def get(self, candidate_id: int) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, created_at, updated_at, observation, insight, reflection,
                           proposed_memory, confidence, category, status
                    FROM candidate_memory WHERE id = ?
                    """,
                    (candidate_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка чтения candidate_memory.sqlite3: {exc}") from exc

        return dict(row) if row is not None else None

    def set_status(self, candidate_id: int, status: str) -> dict | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid candidate_memory status: {status!r}")
        now = datetime.now().astimezone().isoformat()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE candidate_memory SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, candidate_id),
                )
                if cur.rowcount == 0:
                    return None
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка обновления candidate_memory.sqlite3: {exc}") from exc

        return self.get(candidate_id)

    def delete(self, candidate_id: int) -> bool:
        try:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM candidate_memory WHERE id = ?", (candidate_id,))
                return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise SienaInfraError(f"Ошибка удаления из candidate_memory.sqlite3: {exc}") from exc
