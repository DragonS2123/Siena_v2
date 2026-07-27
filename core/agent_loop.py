"""Agent Loop: User -> Qwen -> Need Tool? -> Runtime -> Tool -> Result -> Qwen -> Final Answer.

Единственное решение, которое здесь принимает Python — остановка по MAX_ITERATIONS.
Это инженерная защита от зацикливания/затрат, не связанная с содержанием вызовов
(см. ARCHITECTURE.md, раздел 7.4). Всё остальное решает модель.
"""

from __future__ import annotations

from core.ollama_client import OllamaClient
from core.session import Session
from logging_.logger import SienaLogger
from tools.registry import ToolRegistry
from uuid import uuid4
import json

_KEEP_KEYS = ("role", "content", "tool_calls")


class MaxIterationsReached(Exception):
    pass


def _roles_count(messages: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in messages:
        role = m.get("role", "unknown")
        counts[role] = counts.get(role, 0) + 1
    return counts


def run(
    session: Session,
    ollama_client: OllamaClient,
    registry: ToolRegistry,
    logger: SienaLogger,
    max_iterations: int,
    max_context_messages: int,
) -> str:
    seen_tool_calls: set[str] = set()
    for iteration in range(1, max_iterations + 1):
        context_messages = session.get_context_messages(max_context_messages)

        # Технический "снимок" того, что реально уезжает в Ollama на этот вызов —
        # не смысловая оценка, а диагностика объёма (см. DIAGNOSIS_CONTEXT_OVERFLOW.md).
        logger.event(
            "context_window",
            iteration=iteration,
            context_messages_count=len(context_messages),
            total_session_messages_count=len(session.get_messages()),
            roles_count=_roles_count(context_messages),
            max_context_messages=max_context_messages,
            num_ctx=ollama_client.num_ctx,
            num_predict=ollama_client.num_predict,
            console_message=(
                f"[Siena] контекст: {len(context_messages)}/{max_context_messages} сообщений "
                f"(всего в сессии: {len(session.get_messages())}), num_ctx={ollama_client.num_ctx}, "
                f"num_predict={ollama_client.num_predict}"
            ),
        )

        raw_response = ollama_client.chat(context_messages, tools=registry.schemas())
        message = raw_response.get("message", {})
        tool_calls = message.get("tool_calls")
        content = message.get("content", "")
        done_reason = raw_response.get("done_reason")

        logger.event(
            "ollama_response",
            iteration=iteration,
            model=raw_response.get("model"),
            done_reason=done_reason,
            eval_count=raw_response.get("eval_count"),
            content_length=len(content),
            tool_call_count=len(tool_calls or []),
        )

        logger.event(
            "model_response",
            iteration=iteration,
            has_tool_calls=bool(tool_calls),
            content_length=len(content),
            done_reason=done_reason,
            console_message=(
                f"[Siena] думает... (итерация {iteration}, вызовов инструментов: {len(tool_calls) if tool_calls else 0}, done_reason={done_reason})"
            ),
        )

        session.add_assistant_raw({k: v for k, v in message.items() if k in _KEEP_KEYS})

        if not tool_calls:
            if not content.strip():
                # Модель не вызвала ни одного инструмента и вернула пустой content.
                # Runtime не придумывает ответ за модель и не решает, надо ли было
                # вызвать tool — он лишь фиксирует диагностику, чтобы это можно было
                # разобрать по логам (done_reason, полный raw-ответ выше).
                logger.error(
                    "empty_final_answer",
                    console_message=(
                        f"[Siena] Модель вернула пустой ответ без tool_calls "
                        f"(done_reason={done_reason}). См. событие ollama_raw_response выше в JSONL-логе."
                    ),
                    iteration=iteration,
                    done_reason=done_reason,
                    content_length=len(content),
                )
            return content

        for call in tool_calls:
            function = call.get("function", {})
            name = function.get("name")
            args = function.get("arguments") or {}
            tool_call_id = call.get("id") or f"siena-{uuid4()}"
            signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

            logger.event(
                "tool_dispatch",
                name=name,
                tool_call_id=tool_call_id,
                argument_names=sorted(args),
                console_message=f"  -> tool_call: {name}({args})",
            )

            if signature in seen_tool_calls:
                from core.message import ToolResult
                result = ToolResult(ok=False, error="duplicate_tool_call_blocked")
            else:
                seen_tool_calls.add(signature)
                result = registry.dispatch(name, args, tool_call_id=tool_call_id)

            logger.event(
                "tool_result",
                name=name,
                tool_call_id=tool_call_id,
                ok=result.ok,
                result_type=type(result.content).__name__ if result.content is not None else None,
                error=result.error,
                console_message=f"  <- tool_result: {name} ok={result.ok}",
            )

            session.add_tool_result(name, result, args, tool_call_id=tool_call_id)

    logger.error(
        "max_iterations_reached",
        console_message=f"[Siena] Agent loop остановлен защитой MAX_ITERATIONS ({max_iterations}).",
        max_iterations=max_iterations,
    )
    raise MaxIterationsReached(
        f"Agent loop не пришёл к финальному ответу за {max_iterations} итераций."
    )
