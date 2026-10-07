"""SQLite Memory v1: bounded lexical retrieval, provenance and replaceable facts.

Legacy embedding arguments remain accepted; v1 never indexes or queries vectors.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from core.errors import SienaInfraError, SienaToolError
from memory.policy import MAX_FACTS, SOURCES, canonical, fact_key, terms, validate_fact
from memory.search import tokenize

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS long_memory (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 text TEXT NOT NULL, category TEXT, importance TEXT, source TEXT, metadata_json TEXT
);
'''
_FIELDS = 'id, created_at, updated_at, text, category, importance, source, active, fact_key, metadata_json'


class LongMemoryStore:
    def __init__(self, db_path: Path, search_hard_limit: int = 200,
                 embedding_service=None, vector_store=None, embedding_search_limit=50,
                 embedding_min_score=0.35, logger: Any = None):
        self._db_path = db_path
        self._search_hard_limit = search_hard_limit
        self._embedding_service = embedding_service  # legacy diagnostic compatibility only
        self._vector_store = vector_store
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                columns = {r['name'] for r in conn.execute('PRAGMA table_info(long_memory)')}
                for name, declaration in (('active', 'INTEGER NOT NULL DEFAULT 1'), ('fact_key', 'TEXT')):
                    if name not in columns:
                        conn.execute(f'ALTER TABLE long_memory ADD COLUMN {name} {declaration}')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_active_key ON long_memory(active, fact_key)')
                # Only aliases with known human provenance are migrated. Unknown legacy facts
                # remain visible in the management API, but are excluded from prompt retrieval.
                conn.execute("UPDATE long_memory SET source='user' WHERE source='explicit_user_action'")
                conn.execute("UPDATE long_memory SET source='conversation' WHERE source LIKE 'candidate_memory:%'")
                for row in conn.execute('SELECT id,text,category FROM long_memory WHERE fact_key IS NULL').fetchall():
                    try:
                        validate_fact(row['text'], row['category'])
                    except SienaToolError:
                        continue
                    conn.execute('UPDATE long_memory SET fact_key=? WHERE id=?', (fact_key(row['text']), row['id']))
        except sqlite3.Error as exc:
            raise SienaInfraError(f'memory initialization failed: {exc}') from exc

    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row(row):
        result = dict(row)
        result['active'] = bool(result['active'])
        result['metadata'] = json.loads(result.pop('metadata_json') or '{}')
        return result

    def save(self, text: str, category=None, importance=None, source='user', metadata=None,
             *, key: str | None = None, replaces_id: int | None = None) -> dict:
        if category is not None and (not isinstance(category, str) or len(category) > 100):
            raise SienaToolError('invalid memory category')
        text = validate_fact(text, category)
        if source not in SOURCES:
            raise SienaToolError('memory source must be user, conversation or explicit preference')
        key = key or fact_key(text)
        if not isinstance(key, str) or not 1 <= len(key) <= 1100:
            raise SienaToolError('invalid fact key')
        # Keys and importance are persisted too: they must not become a secret bypass.
        validate_fact(key, max_chars=1100)
        if importance is not None:
            validate_fact(importance)
        # Metadata has a fixed provenance shape: no raw user text/HTML/reasoning/secret fields.
        allowed = {'conversation_id', 'message_id', 'candidate_memory_id', 'confirmed_by_user'}
        meta = {k: v for k, v in (metadata or {}).items() if k in allowed}
        for value in meta.values():
            if isinstance(value, str):
                validate_fact(value)
        now = datetime.now().astimezone().isoformat()
        try:
            with self._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                target = conn.execute(f'SELECT {_FIELDS} FROM long_memory WHERE id=?', (replaces_id,)).fetchone() if replaces_id is not None else None
                if replaces_id is not None and target is None:
                    raise SienaToolError('memory entry to replace does not exist')
                if target is None:
                    target = conn.execute(f'SELECT {_FIELDS} FROM long_memory WHERE fact_key=? OR text=? ORDER BY updated_at DESC LIMIT 1', (key, text)).fetchone()
                if target:
                    entry_id = target['id']
                    conn.execute('UPDATE long_memory SET updated_at=?, text=?, category=?, importance=?, source=?, metadata_json=?, active=1, fact_key=? WHERE id=?',
                                 (now, text, category, importance, source, json.dumps(meta, ensure_ascii=False), key, entry_id))
                else:
                    entry_id = conn.execute('INSERT INTO long_memory (created_at,updated_at,text,category,importance,source,metadata_json,active,fact_key) VALUES (?,?,?,?,?,?,?,1,?)',
                                            (now, now, text, category, importance, source, json.dumps(meta, ensure_ascii=False), key)).lastrowid
                conn.execute('UPDATE long_memory SET active=0, updated_at=? WHERE id<>? AND active=1 AND (fact_key=? OR text=?)', (now, entry_id, key, text))
                row = conn.execute(f'SELECT {_FIELDS} FROM long_memory WHERE id=?', (entry_id,)).fetchone()
                return self._row(row)
        except sqlite3.Error as exc:
            raise SienaInfraError(f'memory save failed: {exc}') from exc

    def get(self, entry_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(f'SELECT {_FIELDS} FROM long_memory WHERE id=?', (entry_id,)).fetchone()
        return self._row(row) if row else None

    def deactivate(self, entry_id: int) -> bool:
        with self._connect() as conn:
            return bool(conn.execute('UPDATE long_memory SET active=0, updated_at=? WHERE id=? AND active=1',
                                     (datetime.now().astimezone().isoformat(), entry_id)).rowcount)

    def delete(self, entry_id: int) -> bool:
        with self._connect() as conn:
            return bool(conn.execute('DELETE FROM long_memory WHERE id=?', (entry_id,)).rowcount)

    def search(self, query: str, limit: int = MAX_FACTS) -> list[dict]:
        query_terms = terms(query)
        if not query_terms:
            return []
        with self._connect() as conn:
            rows = conn.execute(f'SELECT {_FIELDS} FROM long_memory WHERE active=1 AND source IN (?,?,?) ORDER BY updated_at DESC LIMIT 2000', tuple(sorted(SOURCES))).fetchall()
        scored = []
        primary_requested = 'primary' in {canonical(t) for t in tokenize(query)}
        for row in rows:
            try:
                validate_fact(row['text'], row['category'])
            except SienaToolError:
                continue  # Legacy secret/raw-content rows never enter a prompt.
            fact_terms = terms(row['text'])
            if primary_requested and row['fact_key'] in {'gpu:secondary', 'cpu:secondary'}:
                continue
            overlap = query_terms & fact_terms
            if overlap:
                score = len(overlap) / len(query_terms)
                scored.append((score, self._row(row)))
        scored.sort(key=lambda pair: (pair[0], pair[1]['updated_at'], pair[1]['id']), reverse=True)
        return [{**row, 'score': score} for score, row in scored[:min(max(int(limit), 1), MAX_FACTS, self._search_hard_limit)]]

    def list_recent(self, limit=20, *, include_inactive=False) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(f'SELECT {_FIELDS} FROM long_memory ' + ('' if include_inactive else 'WHERE active=1 ') + 'ORDER BY updated_at DESC LIMIT ?',
                                (min(max(int(limit), 1), self._search_hard_limit),)).fetchall()
        return [self._row(row) for row in rows]

    def list_high_importance(self, limit=20) -> list[dict]:
        # Legacy management compatibility. No caller uses this for prompt assembly.
        return [row for row in self.list_recent(self._search_hard_limit) if row['importance'] == 'high'][:limit]
