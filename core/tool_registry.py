"""Deterministic registry for the desktop assistant's allowed tools."""

from __future__ import annotations

from typing import Any, Callable

import config
from core.ollama_client import OllamaClient
from memory.candidate_memory_store import CandidateMemoryStore
from memory.embedding_service import EmbeddingService
from memory.long_memory_store import LongMemoryStore
from memory.short_memory_store import ShortMemoryStore
from memory.vector_store import VectorStore
from tools.candidate_memory_tools import CandidateMemoryCreateTool
from tools.delegate_model import DelegateModelTool
from tools.memory_tools import (
    LongMemoryListTool,
    LongMemorySaveTool,
    LongMemorySearchTool,
    ShortMemoryClearTool,
    ShortMemorySaveTool,
    ShortMemorySearchTool,
)
from tools.registry import ToolRegistry

TOOL_NAMES = (
    "short_memory_save",
    "short_memory_search",
    "short_memory_clear",
    "long_memory_save",
    "long_memory_search",
    "long_memory_list",
    "candidate_memory_create",
    "delegate_model",
)


def build_tool_registry(
    logger: Any,
    assignments: Callable[[], dict[str, str]],
    installed_models: Callable[[], set[str]],
) -> tuple[ToolRegistry, ShortMemoryStore, LongMemoryStore, CandidateMemoryStore]:
    roles = assignments()
    embedding = EmbeddingService(config.OLLAMA_HOST, roles["embedding"], logger)
    vectors = VectorStore(config.MEMORY_VECTORS_DB_PATH)
    short = ShortMemoryStore(
        config.SHORT_MEMORY_PATH,
        embedding_service=embedding,
        embedding_min_score=config.EMBEDDING_MIN_SCORE,
        logger=logger,
    )
    long = LongMemoryStore(
        config.LONG_MEMORY_DB_PATH,
        embedding_service=embedding,
        vector_store=vectors,
        embedding_min_score=config.EMBEDDING_MIN_SCORE,
        logger=logger,
    )
    candidates = CandidateMemoryStore(config.CANDIDATE_MEMORY_DB_PATH)

    registry = ToolRegistry()
    for tool in (
        ShortMemorySaveTool(short, logger),
        ShortMemorySearchTool(short, logger),
        ShortMemoryClearTool(short, logger),
        LongMemorySaveTool(long, logger),
        LongMemorySearchTool(long, logger),
        LongMemoryListTool(long, logger),
        CandidateMemoryCreateTool(candidates, logger),
        DelegateModelTool(
            lambda model: OllamaClient(
                config.OLLAMA_HOST,
                model,
                config.DELEGATE_TIMEOUT_SECONDS,
                num_ctx=config.OLLAMA_NUM_CTX,
                num_predict=config.OLLAMA_NUM_PREDICT,
            ),
            assignments,
            installed_models,
        ),
    ):
        registry.register(tool)
    if tuple(registry.names()) != TOOL_NAMES:
        raise RuntimeError("tool registry order changed")
    return registry, short, long, candidates
