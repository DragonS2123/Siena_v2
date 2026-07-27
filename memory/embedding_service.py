"""Ollama-backed embeddings with keyword-search fallback on failure."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import requests


class EmbeddingUnavailableError(Exception):
    pass


class EmbeddingService:
    def __init__(self, host: str, model_name: str, logger: Any | None = None):
        self._host = host.rstrip("/")
        self._model_name = model_name
        self._logger = logger

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int | None:
        return None

    def is_available(self) -> bool:
        try:
            self.encode("health")
            return True
        except EmbeddingUnavailableError:
            return False

    def encode(self, text: str) -> list[float]:
        try:
            response = requests.post(
                f"{self._host}/api/embed",
                json={"model": self._model_name, "input": text},
                timeout=30,
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings") or []
            if not vectors:
                raise ValueError("empty embedding response")
            return [float(value) for value in vectors[0]]
        except Exception as exc:
            raise EmbeddingUnavailableError(f"embedding model unavailable: {self._model_name}") from exc

    def encode_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]

    def encode_query(self, text: str) -> list[float]:
        return self.encode(text)

    def encode_document(self, text: str) -> list[float]:
        return self.encode(text)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    left = np.asarray(a, dtype=np.float32)
    right = np.asarray(b, dtype=np.float32)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return 0.0 if denominator == 0.0 else float(np.dot(left, right) / denominator)
