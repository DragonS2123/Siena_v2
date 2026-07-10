"""Точка входа Siena v2: консольный REPL.

main.py — это ровно "тело" в терминологии ARCHITECTURE.md: собирает компоненты
и передаёт ввод пользователя в agent_loop. Никакой логики принятия решений здесь нет.

Команды вида "/memory ..." — исключение: это debug-интерфейс ДЛЯ ЧЕЛОВЕКА, чтобы
посмотреть, что реально лежит в памяти. Они не идут в модель и не являются
tool call — Runtime не решает и не сохраняет ничего сам, здесь только чтение
существующих файлов/базы (и, для clear-short, явное действие человека, а не модели).
"""

from __future__ import annotations

import sys

# Консоль Windows по умолчанию использует cp1251/cp866 и падает на эмодзи/юникоде,
# который может сгенерировать модель. Переключаем stdout/stderr на UTF-8 до того,
# как что-либо будет напечатано или создан логгер.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
from core.agent_loop import MaxIterationsReached, run as run_agent_loop
from core.errors import SienaInfraError
from core.ollama_client import OllamaClient
from core.session import Session
from logging_.logger import SienaLogger
from memory.candidate_memory_store import CandidateMemoryStore
from memory.embedding_service import EmbeddingService
from memory.long_memory_store import LongMemoryStore
from memory.short_memory_store import ShortMemoryStore
from memory.vector_store import VectorStore
from tools.candidate_memory_tools import CandidateMemoryCreateTool
from tools.current_time import GetCurrentTimeTool
from tools.delegate_model import DelegateModelTool
from tools.memory_tools import (
    LongMemoryListTool,
    LongMemorySaveTool,
    LongMemorySearchTool,
    ShortMemoryClearTool,
    ShortMemorySaveTool,
    ShortMemorySearchTool,
)
from tools.open_url import OpenUrlTool
from tools.registry import ToolRegistry
from tools.web_search import WebSearchTool

EXIT_COMMANDS = {"exit", "quit", "выход"}

REQUIRED_TOOL_NAMES = [
    "web_search",
    "open_url",
    "get_current_time",
    "short_memory_save",
    "short_memory_search",
    "short_memory_clear",
    "long_memory_save",
    "long_memory_search",
    "long_memory_list",
    "candidate_memory_create",
    "delegate_model",
]


def build_registry(
    logger: SienaLogger,
) -> tuple[ToolRegistry, ShortMemoryStore, LongMemoryStore, CandidateMemoryStore]:
    # Embedding-модель грузится лениво (см. EmbeddingService) — конструирование
    # здесь никогда не падает, даже если sentence-transformers не установлен
    # или веса не скачались; поиск в этом случае просто откатится на
    # keyword/fuzzy (memory/search.py) внутри самих stores.
    embedding_service = EmbeddingService(config.EMBEDDING_MODEL_NAME, logger) if config.EMBEDDINGS_ENABLED else None
    vector_store = VectorStore(config.MEMORY_VECTORS_DB_PATH) if config.EMBEDDINGS_ENABLED else None

    short_store = ShortMemoryStore(
        config.SHORT_MEMORY_PATH,
        embedding_service=embedding_service,
        embedding_min_score=config.EMBEDDING_MIN_SCORE,
        logger=logger,
    )
    long_store = LongMemoryStore(
        config.LONG_MEMORY_DB_PATH,
        config.LONG_MEMORY_SEARCH_HARD_LIMIT,
        embedding_service=embedding_service,
        vector_store=vector_store,
        embedding_search_limit=config.EMBEDDING_SEARCH_LIMIT,
        embedding_min_score=config.EMBEDDING_MIN_SCORE,
        logger=logger,
    )
    candidate_store = CandidateMemoryStore(config.CANDIDATE_MEMORY_DB_PATH)

    # Отдельный клиент для делегируемых вызовов: свой (обычно больший) таймаут,
    # т.к. генерация кода может идти дольше обычного диалогового ответа
    # (ARCHITECTURE.md §12, открытый вопрос про DELEGATE_TIMEOUT_SECONDS).
    # host/think те же — agent_loop.py об этом клиенте ничего не знает.
    delegate_ollama_client = OllamaClient(
        host=config.OLLAMA_HOST,
        model=config.PRIMARY_MODEL,
        timeout=config.DELEGATE_TIMEOUT_SECONDS,
        think=config.OLLAMA_THINK,
        num_ctx=config.OLLAMA_NUM_CTX,
        num_predict=config.OLLAMA_NUM_PREDICT,
    )

    registry = ToolRegistry()
    registry.register(WebSearchTool(config.WEB_SEARCH_MAX_RESULTS, config.WEB_SEARCH_TIMEOUT_SECONDS))
    registry.register(OpenUrlTool(config.OPEN_URL_TIMEOUT_SECONDS, config.OPEN_URL_MAX_CHARS))
    registry.register(GetCurrentTimeTool())
    registry.register(ShortMemorySaveTool(short_store, logger))
    registry.register(ShortMemorySearchTool(short_store, logger))
    registry.register(ShortMemoryClearTool(short_store, logger))
    registry.register(LongMemorySaveTool(long_store, logger))
    registry.register(LongMemorySearchTool(long_store, logger))
    registry.register(LongMemoryListTool(long_store, logger, config.LONG_MEMORY_LIST_DEFAULT_LIMIT))
    # Только create — promote/reject/later/delete НЕ являются tools модели,
    # см. tools/candidate_memory_tools.py (human-in-the-loop только через REST).
    registry.register(CandidateMemoryCreateTool(candidate_store, logger))
    registry.register(
        DelegateModelTool(delegate_ollama_client, config.DELEGATE_MODELS, logger, config.PRIMARY_MODEL)
    )
    return registry, short_store, long_store, candidate_store


def print_registered_tools(registry: ToolRegistry) -> None:
    print("[TOOLS]")
    for name in registry.names():
        print(f"  - {name}")

    missing = [name for name in REQUIRED_TOOL_NAMES if name not in registry.names()]
    if missing:
        raise SienaInfraError(
            f"Обязательные инструменты не зарегистрированы: {missing}. "
            "Модель не сможет их вызвать — это ошибка конфигурации Runtime, не запускаемся."
        )


def print_short_memory(store: ShortMemoryStore) -> None:
    entries = store.search("")
    if not entries:
        print("[MEMORY][SHORT] пусто.")
        return
    print(f"[MEMORY][SHORT] {len(entries)} запис(ей):")
    for e in entries:
        print(f"  id={e['id']} | {e['created_at']} | {e['text']}")


def print_long_memory(store: LongMemoryStore, limit: int) -> None:
    entries = store.list_recent(limit)
    if not entries:
        print("[MEMORY][LONG] пусто.")
        return
    print(f"[MEMORY][LONG] последние {len(entries)} запис(ей):")
    print(f"  {'id':<5}| {'created_at':<32}| {'category':<14}| {'importance':<10}| text")
    for e in entries:
        category = e["category"] or "-"
        importance = e["importance"] or "-"
        print(f"  {e['id']:<5}| {e['created_at']:<32}| {category:<14}| {importance:<10}| {e['text']}")


def handle_memory_command(user_input: str, short_store: ShortMemoryStore, long_store: LongMemoryStore) -> None:
    parts = user_input.split()
    sub = parts[1] if len(parts) > 1 else ""

    if sub == "short":
        print_short_memory(short_store)
    elif sub == "long":
        limit = config.LONG_MEMORY_LIST_DEFAULT_LIMIT
        if len(parts) > 2 and parts[2].isdigit():
            limit = int(parts[2])
        print_long_memory(long_store, limit)
    elif sub == "clear-short":
        cleared = short_store.clear()
        print(f"[MEMORY][SHORT] очищено вручную пользователем: {cleared} запис(ей).")
    else:
        print("Неизвестная debug-команда. Доступно: /memory short | /memory long [N] | /memory clear-short")


def main() -> None:
    logger = SienaLogger(config.LOG_DIR, config.LOG_LEVEL)
    registry, short_store, long_store, _candidate_store = build_registry(logger)
    print_registered_tools(registry)

    ollama_client = OllamaClient(
        host=config.OLLAMA_HOST,
        model=config.PRIMARY_MODEL,
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        think=config.OLLAMA_THINK,
        num_ctx=config.OLLAMA_NUM_CTX,
        num_predict=config.OLLAMA_NUM_PREDICT,
    )
    session = Session(config.SYSTEM_PROMPT)

    print(f"Siena v2 — модель: {config.PRIMARY_MODEL}. Введите сообщение (или 'exit' для выхода).")

    while True:
        try:
            user_input = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nЗавершение работы.")
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            print("Завершение работы.")
            break
        if user_input.startswith("/memory"):
            handle_memory_command(user_input, short_store, long_store)
            continue

        session.add_user(user_input)
        logger.event("user_message", content=user_input)

        try:
            answer = run_agent_loop(
                session=session,
                ollama_client=ollama_client,
                registry=registry,
                logger=logger,
                max_iterations=config.MAX_ITERATIONS,
                max_context_messages=config.MAX_CONTEXT_MESSAGES,
            )
        except SienaInfraError as exc:
            logger.error("infra_error", console_message=f"[Ошибка инфраструктуры] {exc}", error=str(exc))
            print(f"\nSiena: [инфраструктурная ошибка, обратитесь к логам] {exc}")
            continue
        except MaxIterationsReached as exc:
            print(f"\nSiena: [цикл прерван защитой от зацикливания] {exc}")
            continue

        logger.event("final_answer", content=answer)
        if not answer.strip():
            # Runtime не сочиняет ответ за модель — только помечает, что модель
            # сама вернула пустой content. Подробности (done_reason, полный raw-ответ
            # Ollama) уже записаны agent_loop в JSONL-лог под событием empty_final_answer.
            print("\nSiena: [пустой ответ модели без вызова инструментов — см. logs/*.jsonl, событие empty_final_answer]")
        else:
            print(f"\nSiena: {answer}")


if __name__ == "__main__":
    main()
