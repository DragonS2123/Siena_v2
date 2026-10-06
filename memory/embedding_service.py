"""Ollama-backed embeddings with keyword-search fallback on failure."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Sequence

import numpy as np
import requests


class EmbeddingUnavailableError(Exception):
    pass


class EmbeddingService:
    def __init__(
        self,
        host: str | Callable[[], str],
        model_name: str | Callable[[], str],
        logger: Any | None = None,
    ):
        self._host = host
        self._model_name = model_name
        self._logger = logger

    @property
    def host(self) -> str:
        value = self._host() if callable(self._host) else self._host
        return value.rstrip("/")

    @property
    def model_name(self) -> str:
        return self._model_name() if callable(self._model_name) else self._model_name

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
        model = self.model_name
        started = time.monotonic()
        if self._logger is not None:
            self._logger.event("model.request.started", requested_role="embedding", resolved_model=model)
        try:
            response = requests.post(
                f"{self.host}/api/embed",
                json={"model": model, "input": text},
                timeout=30,
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings") or []
            if not vectors:
                raise ValueError("empty embedding response")
            result = [float(value) for value in vectors[0]]
            if self._logger is not None:
                self._logger.event(
                    "model.request.completed",
                    requested_role="embedding",
                    resolved_model=model,
                    ollama_model=model,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    dimensions=len(result),
                )
            return result
        except Exception as exc:
            if self._logger is not None:
                self._logger.error(
                    "model.request.failed",
                    console_message=f"[Siena] Embedding model call failed: {model}",
                    requested_role="embedding",
                    resolved_model=model,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    error=str(exc),
                )
            raise EmbeddingUnavailableError(f"embedding model unavailable: {model}") from exc

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
