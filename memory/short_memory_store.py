"""JSON-хранилище кратковременной (рабочей) памяти сессии.

Формат файла — плоский список записей:
[{"id": "uuid", "created_at": "2026-07-05T12:00:00+03:00", "text": "...", "source": "model_tool_call"}].
JSON выбран намеренно, чтобы разработчик мог открыть файл глазами и проверить,
что именно Siena решила сохранить (см. ARCHITECTURE.md, раздел 5.1).

Store не решает, что сохранять и когда искать — это уже решила модель, вызвав tool.

Поиск — гибридный, как и в long_memory_store.py: vector similarity (если
embedding-модель передана и доступна) первично, keyword+fuzzy (memory/search.py)
— fallback. Записей в короткой памяти немного и она живёт один сеанс, поэтому
embeddings считаются на лету при каждом search(), без отдельного vector-индекса.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.errors import SienaInfraError
from memory import search as keyword_search
from memory.embedding_service import EmbeddingService


class ShortMemoryStore:
    def __init__(
        self,
        path: Path,
        embedding_service: EmbeddingService | None = None,
        embedding_min_score: float = 0.35,
        logger: Any | None = None,
    ):
        self._path = path
        self._embedding_service = embedding_service
        self._embedding_min_score = embedding_min_score
        self._logger = logger
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write([])

    def _read(self) -> list[dict]:
        try:
            raw = self._path.read_text(encoding="utf-8")
            return json.loads(raw) if raw.strip() else []
        except (OSError, json.JSONDecodeError) as exc:
            raise SienaInfraError(f"short_memory.json повреждён или недоступен: {exc}") from exc

    def _write(self, entries: list[dict]) -> None:
        tmp_path = self._path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError as exc:
            raise SienaInfraError(f"Не удалось записать short_memory.json: {exc}") from exc

    def save(self, text: str) -> dict:
        entries = self._read()
        entry = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now().astimezone().isoformat(),
            "text": text,
            "source": "model_tool_call",
        }
        entries.append(entry)
        self._write(entries)
        return entry

    def search(self, query: str) -> list[dict]:
        entries = self._read()
        if not query.strip():
            return entries

        vector_hits = self._vector_search(query, entries)
        keyword_hits = keyword_search.rank(query, entries, ["text"], limit=len(entries))
        keyword_ids = {e["id"] for e in keyword_hits}

        merged: list[dict] = []
        seen_ids: set[str] = set()
        for hit in vector_hits:
            entry = dict(hit)
            is_keyword_match = entry["id"] in keyword_ids
            entry["keyword_match"] = is_keyword_match
            entry["search_source"] = "hybrid" if is_keyword_match else "vector"
            merged.append(entry)
            seen_ids.add(entry["id"])

        for hit in keyword_hits:
            if hit["id"] in seen_ids:
                continue
            entry = dict(hit)
            entry["keyword_match"] = True
            entry["search_source"] = "keyword"
            merged.append(entry)
            seen_ids.add(entry["id"])

        return merged

    def _vector_search(self, query: str, entries: list[dict]) -> list[dict]:
        if self._embedding_service is None or not entries:
            return []
        try:
            if not self._embedding_service.is_available():
                return []
            from memory.embedding_service import cosine_similarity

            query_vector = self._embedding_service.encode_query(query)
            scored = []
            for entry in entries:
                doc_vector = self._embedding_service.encode_document(entry["text"])
                sim = cosine_similarity(query_vector, doc_vector)
                if sim >= self._embedding_min_score:
                    hit = dict(entry)
                    hit["vector_score"] = sim
                    scored.append((sim, hit))
            if not scored:
                return []
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return [hit for _, hit in scored]
        except Exception as exc:  # noqa: BLE001 — сбой embedding -> vector_hits=[], keyword всё равно работает
            if self._logger is not None:
                self._logger.event(
                    "embedding_error",
                    query=query,
                    error=str(exc),
                    console_message=f"[EMBEDDING][SHORT] поиск не удался, fallback на keyword: {exc}",
                )
            return []

    def clear(self) -> int:
        entries = self._read()
        self._write([])
        return len(entries)
