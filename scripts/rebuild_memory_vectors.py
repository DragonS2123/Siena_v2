"""Backfill: пересоздаёт embedding-векторы для уже существующих записей
long_memory.sqlite3 в memory_vectors.sqlite3.

Ничего не решает и не меняет long_memory — просто (пере)индексирует то, что
там уже сохранено. Нужен один раз после включения EMBEDDINGS_ENABLED на базе,
где уже накопились записи без векторов (новые записи индексируются
автоматически в LongMemoryStore.save(), см. index_vector()).

Запуск: python scripts/rebuild_memory_vectors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from memory.embedding_service import EmbeddingService  # noqa: E402
from memory.long_memory_store import LongMemoryStore  # noqa: E402
from memory.vector_store import VectorStore  # noqa: E402


def main() -> None:
    if not config.EMBEDDINGS_ENABLED:
        print("EMBEDDINGS_ENABLED = False — nothing to rebuild.")
        return

    embedding_service = EmbeddingService(config.EMBEDDING_MODEL_NAME)
    if not embedding_service.is_available():
        print(f"Embedding model {config.EMBEDDING_MODEL_NAME!r} unavailable — aborting rebuild.")
        return

    vector_store = VectorStore(config.MEMORY_VECTORS_DB_PATH)
    long_store = LongMemoryStore(
        config.LONG_MEMORY_DB_PATH,
        config.LONG_MEMORY_SEARCH_HARD_LIMIT,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )

    entries = long_store.list_recent(limit=config.LONG_MEMORY_SEARCH_HARD_LIMIT)
    for entry in entries:
        long_store.index_vector(entry["id"], entry["text"], entry.get("category"))

    print(f"Reindexed {len(entries)} long_memory rows with model {config.EMBEDDING_MODEL_NAME!r}.")


if __name__ == "__main__":
    main()
