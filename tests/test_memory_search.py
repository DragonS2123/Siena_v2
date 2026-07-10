"""Тесты гибридного поиска памяти и human-in-the-loop контракта candidate memory.

Ничего не бьёт в сеть/реальную embedding-модель: недоступность симулируется
через _UnavailableEmbeddingService (is_available() всегда False, как если бы
sentence-transformers не установился), а сам vector-путь проверяется через
_FakeEmbeddingService — детерминированный bag-of-words без реальной ML-модели.
Это специально: тесты должны быть быстрыми и не зависеть от сети/GPU.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import main  # noqa: E402
from logging_.logger import SienaLogger  # noqa: E402
from memory import search as keyword_search  # noqa: E402
from memory.embedding_service import EmbeddingService, cosine_similarity  # noqa: E402
from memory.long_memory_store import LongMemoryStore  # noqa: E402
from memory.vector_store import VectorStore  # noqa: E402
from tools.memory_tools import LongMemorySearchTool, ShortMemorySearchTool  # noqa: E402

SEEDED_TEXT = "Пользователя зовут Максим, он считает себя создателем Siena"

REQUIRED_QUERIES = [
    "как меня зовут",
    "имя пользователя",
    "Максим создатель",
    "создатель Siena",
]


class _UnavailableEmbeddingService(EmbeddingService):
    """Симулирует недоступную embedding-модель (например, sentence-transformers
    не установлен или веса не скачались) без обращения к сети."""

    def __init__(self):
        super().__init__(model_name="unavailable-test-model")
        self._load_failed = True


class _FakeEmbeddingService(EmbeddingService):
    """Детерминированный bag-of-words embedding для проверки интеграции
    VectorStore + cosine ranking без реальной ML-модели."""

    _VOCAB = ["пользователя", "зовут", "максим", "создателем", "siena", "имя", "создатель"]

    def __init__(self):
        super().__init__(model_name="fake-test-model")
        self._model = object()  # non-None -> is_available() True, без реальной загрузки

    def _vectorize(self, text: str) -> list[float]:
        normalized = text.lower()
        return [1.0 if word in normalized else 0.0 for word in self._VOCAB]

    def encode_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    def encode_document(self, text: str) -> list[float]:
        return self._vectorize(text)


def test_keyword_rank_matches_required_phrases():
    rows = [{"text": SEEDED_TEXT, "category": None, "created_at": "2026-01-01"}]
    for query in REQUIRED_QUERIES:
        matches = keyword_search.rank(query, rows, ["text", "category"], limit=10)
        assert matches, f"query {query!r} should match seeded text via keyword/fuzzy ranking"


def test_cosine_similarity_orders_similar_vectors_higher():
    a = [1.0, 0.0, 0.0]
    b_similar = [0.9, 0.1, 0.0]
    b_different = [0.0, 0.0, 1.0]
    assert cosine_similarity(a, b_similar) > cosine_similarity(a, b_different)


def test_long_memory_store_falls_back_to_keyword_when_embedding_unavailable(tmp_path):
    store = LongMemoryStore(
        tmp_path / "long_memory.sqlite3",
        embedding_service=_UnavailableEmbeddingService(),
        vector_store=VectorStore(tmp_path / "memory_vectors.sqlite3"),
    )
    entry = store.save(SEEDED_TEXT, category="decision")
    assert entry["id"] is not None  # save must succeed even though embedding indexing is unavailable

    for query in REQUIRED_QUERIES:
        results = store.search(query)
        assert any(r["id"] == entry["id"] for r in results), f"query {query!r} should find seeded row via fallback"


def test_long_memory_store_vector_search_finds_seeded_row(tmp_path):
    store = LongMemoryStore(
        tmp_path / "long_memory.sqlite3",
        embedding_service=_FakeEmbeddingService(),
        vector_store=VectorStore(tmp_path / "memory_vectors.sqlite3"),
        embedding_min_score=0.1,
    )
    entry = store.save(SEEDED_TEXT, category="decision")

    results = store.search("имя пользователя")
    assert any(r["id"] == entry["id"] for r in results)
    assert "score" in results[0]  # vector path attaches a similarity score


def test_only_candidate_memory_create_is_registered_as_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SHORT_MEMORY_PATH", tmp_path / "short_memory.json")
    monkeypatch.setattr(config, "LONG_MEMORY_DB_PATH", tmp_path / "long_memory.sqlite3")
    monkeypatch.setattr(config, "CANDIDATE_MEMORY_DB_PATH", tmp_path / "candidate_memory.sqlite3")
    monkeypatch.setattr(config, "MEMORY_VECTORS_DB_PATH", tmp_path / "memory_vectors.sqlite3")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")

    logger = SienaLogger(tmp_path / "logs", "error")
    registry, *_ = main.build_registry(logger)
    names = set(registry.names())

    assert "candidate_memory_create" in names
    for forbidden in (
        "candidate_memory_promote",
        "candidate_memory_reject",
        "candidate_memory_later",
        "candidate_memory_delete",
        "candidate_memory_list",
        "candidate_memory_clear",
    ):
        assert forbidden not in names


def test_long_memory_search_tool_contract_unchanged():
    assert LongMemorySearchTool.name == "long_memory_search"
    assert set(LongMemorySearchTool.parameters["required"]) == {"query"}


def test_short_memory_search_tool_contract_unchanged():
    assert ShortMemorySearchTool.name == "short_memory_search"
    assert set(ShortMemorySearchTool.parameters["required"]) == {"query"}
