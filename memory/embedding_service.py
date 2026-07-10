"""Ленивая обёртка над sentence-transformers для embedding-поиска по памяти.

Это чисто техническая retrieval-операция: Runtime не решает, когда искать
и что "релевантно" — он лишь считает cosine similarity и сортирует. Если
модель не установлена, веса не скачались или упали при загрузке — is_available()
возвращает False и вызывающий store обязан откатиться на keyword/fuzzy fallback
(memory/search.py), не роняя сохранение/поиск целиком.
"""

from __future__ import annotations

import threading
from typing import Any, Sequence

import numpy as np


class EmbeddingUnavailableError(Exception):
    pass


class EmbeddingService:
    def __init__(self, model_name: str, logger: Any | None = None):
        self._model_name = model_name
        self._logger = logger
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_available(self) -> bool:
        self._ensure_loaded()
        return self._model is not None

    @property
    def dimension(self) -> int | None:
        self._ensure_loaded()
        if self._model is None:
            return None
        return self._model.get_sentence_embedding_dimension()

    def _ensure_loaded(self) -> None:
        if self._model is not None or self._load_failed:
            return
        with self._lock:
            if self._model is not None or self._load_failed:
                return
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name)
            except Exception as exc:
                self._load_failed = True
                if self._logger is not None:
                    self._logger.event(
                        "embedding_error",
                        model=self._model_name,
                        error=str(exc),
                        console_message=f"[EMBEDDING] модель {self._model_name} не загрузилась, fallback на keyword-поиск: {exc}",
                    )

    def _needs_e5_prefix(self) -> bool:
        return "e5" in self._model_name.lower()

    def encode(self, text: str) -> list[float]:
        self._ensure_loaded()
        if self._model is None:
            raise EmbeddingUnavailableError(f"Embedding model unavailable: {self._model_name}")
        vector = self._model.encode(text, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32).tolist()

    def encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_loaded()
        if self._model is None:
            raise EmbeddingUnavailableError(f"Embedding model unavailable: {self._model_name}")
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [np.asarray(v, dtype=np.float32).tolist() for v in vectors]

    def encode_query(self, text: str) -> list[float]:
        return self.encode(f"query: {text}" if self._needs_e5_prefix() else text)

    def encode_document(self, text: str) -> list[float]:
        return self.encode(f"passage: {text}" if self._needs_e5_prefix() else text)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)
