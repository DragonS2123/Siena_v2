"""Deterministic registry for the desktop assistant's allowed tools."""

from __future__ import annotations

from typing import Any, Callable

import config
from core.model_provider import ModelProvider
from core.provider_factory import create_provider
from core.runtime_settings import RuntimeSettingsService
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
from tools.web import WebSearchTool, WebReadTool

TOOL_NAMES = (
    "short_memory_save",
    "short_memory_search",
    "short_memory_clear",
    "long_memory_save",
    "long_memory_search",
    "long_memory_list",
    "candidate_memory_create",
    "delegate_model",
    "web_search",
    "web_read",
)


def build_tool_registry(
    logger: Any,
    settings: RuntimeSettingsService,
    assignments: Callable[[], dict[str, str]],
    installed_models: Callable[[], set[str]],
) -> tuple[ToolRegistry, ShortMemoryStore, LongMemoryStore, CandidateMemoryStore]:
    embedding = EmbeddingService(
        lambda: str(settings.current().get("ollama_host")),
        lambda: assignments()["embedding"],
        logger,
    )
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

    def delegate_client(model: str) -> ModelProvider:
        snapshot = settings.current()
        return create_provider(
            snapshot,
            model,
            int(snapshot.get("request_timeout_seconds")),
            num_ctx=int(snapshot.get("context_size")),
            num_predict=int(snapshot.get("chat_output_tokens")),
        )

    registry = ToolRegistry()
    for tool in (
        ShortMemorySaveTool(short, logger),
        ShortMemorySearchTool(short, logger),
        ShortMemoryClearTool(short, logger),
        LongMemorySaveTool(long, logger),
        LongMemorySearchTool(long, logger),
        LongMemoryListTool(long, logger),
        CandidateMemoryCreateTool(candidates, logger),
        DelegateModelTool(delegate_client, assignments, installed_models, logger),
        WebSearchTool(),
        WebReadTool(),
    ):
        registry.register(tool)
    if tuple(registry.names()) != TOOL_NAMES:
        raise RuntimeError("tool registry order changed")
    return registry, short, long, candidates
