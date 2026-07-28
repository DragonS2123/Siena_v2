"""Business logic for a local conversation turn."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime
from typing import Any

import config
from core.agent_loop import AgentResult, run as run_agent_loop
from core.attachment_service import AttachmentService
from core.model_catalog import ModelCatalog
from core.model_roles import ModelRoles
from core.ollama_client import OllamaClient
from core.session import Session
from core.streaming_chat import (
    PrematureHtmlFenceFilter,
    continuation_messages,
    has_incomplete_structure,
    response_missing_requested_structure,
    seam_merge,
    with_stream_timeouts,
)
from memory.user_memory_context import build_user_memory_context
from storage.conversation_store import ConversationStore
from storage.settings_store import SettingsStore
from tools.registry import ToolRegistry


class ChatService:
    def __init__(
        self,
        conversations: ConversationStore,
        roles: ModelRoles,
        catalog: ModelCatalog,
        registry: ToolRegistry,
        long_memory: Any,
        attachments: AttachmentService,
        logger: Any,
        settings: SettingsStore,
    ):
        self._conversations = conversations
        self._roles = roles
        self._catalog = catalog
        self._registry = registry
        self._long_memory = long_memory
        self._attachments = attachments
        self._logger = logger
        self._settings = settings

    @staticmethod
    def _is_code_request(text: str) -> bool:
        return bool(re.search(
            r"(```|~~~|\b(?:html|javascript|typescript|python|css|react|код|скрипт|"
            r"веб[- ]?страниц|сайт|визуализац|программ)\b)",
            text,
            re.IGNORECASE,
        ))

    def _generation_limits(self, text: str, session: Session) -> tuple[int, int, int, int]:
        values, _ = self._settings.load()
        num_ctx = int(values.get("num_ctx", config.OLLAMA_NUM_CTX))
        ordinary_limit = int(values.get("num_predict", config.OLLAMA_NUM_PREDICT))
        is_code = self._is_code_request(text)
        requested = int(values.get(
            "code_num_predict" if is_code else "num_predict",
            config.OLLAMA_CODE_NUM_PREDICT if is_code else ordinary_limit,
        ))
        timeout = int(values.get(
            "code_request_timeout_seconds" if is_code else "request_timeout_seconds",
            config.CODE_REQUEST_TIMEOUT_SECONDS if is_code else config.REQUEST_TIMEOUT_SECONDS,
        ))
        max_messages = int(values.get("max_context_messages", config.MAX_CONTEXT_MESSAGES))
        prompt_chars = sum(
            len(str(message.get("content") or ""))
            for message in session.get_context_messages(max_messages)
        )
        # Conservative local estimate; Ollama's exact prompt_eval_count is logged.
        estimated_prompt_tokens = math.ceil(prompt_chars / 2)
        available = max(1, num_ctx - estimated_prompt_tokens - 512)
        effective = max(1, min(requested, available))
        self._logger.event(
            "generation_budget",
            code_request=is_code,
            num_ctx=num_ctx,
            requested_num_predict=requested,
            effective_num_predict=effective,
            estimated_prompt_tokens=estimated_prompt_tokens,
            request_timeout_seconds=timeout,
            max_context_messages=max_messages,
        )
        return num_ctx, effective, timeout, max_messages

    async def turn(
        self,
        conversation_id: str,
        text: str,
        *,
        model_override: str | None = None,
        attachment_context: str = "",
        ocr_context: str = "",
        vision_context: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        catalog = self._catalog.refresh()
        installed = {model["name"] for model in catalog.get("models", [])}
        assigned = self._roles.assignments()
        selected = model_override or conversation.get("model_override") or assigned["chat"]
        if selected not in installed:
            raise ValueError(f"selected model is missing from Ollama: {selected}")

        user_record = self._conversations.append_message(
            conversation_id,
            "user",
            text,
            metadata={"status": "processing"},
        )
        stored_attachments: list[dict[str, Any]] = []
        try:
            if attachments:
                generated_text, generated_ocr, generated_vision, stored_attachments = await self._attachments.process(
                    conversation_id, user_record["id"], attachments, text
                )
                attachment_context = "\n\n".join(item for item in (attachment_context, generated_text) if item)
                ocr_context = "\n\n".join(item for item in (ocr_context, generated_ocr) if item)
                vision_context = "\n\n".join(item for item in (vision_context, generated_vision) if item)
        except Exception as exc:
            self._conversations.merge_message_metadata(
                user_record["id"], {"status": "failed", "error": str(exc)}
            )
            raise
        session = Session(config.SYSTEM_PROMPT)
        for message in conversation["messages"]:
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                session.add_user(content)
            elif role == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": content})

        now = datetime.now().astimezone()
        context = [
            f"[RUNTIME]\ndate={now:%Y-%m-%d}\ntime={now:%H:%M:%S}\ntimezone={now.tzname()}\n[/RUNTIME]",
            build_user_memory_context(self._long_memory),
            attachment_context,
            ocr_context,
            vision_context,
        ]
        combined = "\n\n".join(item for item in context if item)
        session.add_user(f"{text}\n\n{combined}" if combined else text)
        num_ctx, num_predict, timeout, max_context_messages = self._generation_limits(text, session)
        client = OllamaClient(
            config.OLLAMA_HOST,
            selected,
            timeout,
            config.OLLAMA_THINK,
            num_ctx,
            num_predict,
        )
        try:
            answer = await asyncio.to_thread(
                run_agent_loop,
                session,
                client,
                self._registry,
                self._logger,
                config.MAX_ITERATIONS,
                max_context_messages,
            )
        except Exception as exc:
            self._conversations.merge_message_metadata(
                user_record["id"], {"status": "failed", "error": str(exc)}
            )
            raise
        result = answer if isinstance(answer, AgentResult) else AgentResult(content=str(answer))
        assistant = self._conversations.append_message(
            conversation_id,
            "assistant",
            result.content,
            model=selected,
            metadata=result.metadata(),
        )
        self._conversations.merge_message_metadata(
            user_record["id"],
            {"status": "completed", "assistant_message_id": assistant["id"], "model_used": selected},
        )
        return {
            "answer": result.content,
            "conversation_id": conversation_id,
            "message_id": user_record["id"],
            "assistant_message_id": assistant["id"],
            "model_used": selected,
            "attachments": stored_attachments,
            **result.metadata(),
        }
    async def stream_turn(
        self,
        conversation_id: str,
        text: str,
        *,
        model_override: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ):
        """Yield normalized end-to-end generation events for one logical message."""
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        catalog = self._catalog.refresh()
        installed = {model["name"] for model in catalog.get("models", [])}
        assigned = self._roles.assignments()
        selected = model_override or conversation.get("model_override") or assigned["chat"]
        if selected not in installed:
            raise ValueError(f"selected model is missing from Ollama: {selected}")

        user_record = self._conversations.append_message(
            conversation_id, "user", text, metadata={"status": "processing"}
        )
        stored_attachments: list[dict[str, Any]] = []
        attachment_context = ocr_context = vision_context = ""
        try:
            if attachments:
                generated_text, generated_ocr, generated_vision, stored_attachments = await self._attachments.process(
                    conversation_id, user_record["id"], attachments, text
                )
                attachment_context = generated_text
                ocr_context = generated_ocr
                vision_context = generated_vision
        except Exception as exc:
            self._conversations.merge_message_metadata(
                user_record["id"], {"status": "failed", "error": str(exc)}
            )
            raise

        session = Session(config.SYSTEM_PROMPT)
        for message in conversation["messages"]:
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                session.add_user(content)
            elif role == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": content})
        now = datetime.now().astimezone()
        runtime_context = [
            f"[RUNTIME]\ndate={now:%Y-%m-%d}\ntime={now:%H:%M:%S}\ntimezone={now.tzname()}\n[/RUNTIME]",
            build_user_memory_context(self._long_memory),
            attachment_context,
            ocr_context,
            vision_context,
        ]
        combined = "\n\n".join(item for item in runtime_context if item)
        session.add_user(f"{text}\n\n{combined}" if combined else text)
        num_ctx, num_predict, _legacy_timeout, max_context_messages = self._generation_limits(text, session)
        values, _ = self._settings.load()
        auto_continue = bool(values.get("auto_continue_on_length", config.AUTO_CONTINUE_ON_LENGTH))
        max_continuations = int(values.get("max_auto_continuations", config.MAX_AUTO_CONTINUATIONS))
        max_total_tokens = int(values.get("max_total_generation_tokens", config.MAX_TOTAL_GENERATION_TOKENS))
        overlap_window = int(values.get("continuation_overlap_window_chars", config.CONTINUATION_OVERLAP_WINDOW_CHARS))

        assistant = self._conversations.append_message(
            conversation_id,
            "assistant",
            "",
            model=selected,
            metadata={
                "status": "pending",
                "thinking": "",
                "done_reason": None,
                "segment_count": 0,
                "continuation_count": 0,
                "eval_count_total": 0,
                "prompt_eval_count": 0,
                "total_duration": 0,
                "configured_num_predict": num_predict,
                "num_ctx": num_ctx,
                "timeout": False,
                "timeout_metadata": {
                    "connect": config.OLLAMA_CONNECT_TIMEOUT_SECONDS,
                    "first_token": config.OLLAMA_FIRST_TOKEN_TIMEOUT_SECONDS,
                    "stream_idle": config.OLLAMA_STREAM_IDLE_TIMEOUT_SECONDS,
                    "hard_total": config.OLLAMA_HARD_TOTAL_TIMEOUT_SECONDS,
                },
                "incomplete": True,
            },
        )
        assistant_id = assistant["id"]
        yield {
            "type": "generation.started",
            "message_id": user_record["id"],
            "assistant_message_id": assistant_id,
            "conversation_id": conversation_id,
            "model_used": selected,
            "attachments": stored_attachments,
        }

        accumulated = ""
        thinking = ""
        segment_count = 0
        continuation_count = 0
        total_eval = 0
        prompt_eval_count = 0
        total_duration = 0
        no_progress_segments = 0
        segment_results: list[dict[str, Any]] = []
        base_messages = session.get_context_messages(max_context_messages)
        current_messages = base_messages
        loop = asyncio.get_running_loop()
        hard_deadline = loop.time() + config.OLLAMA_HARD_TOTAL_TIMEOUT_SECONDS
        last_checkpoint_at = loop.time()
        last_checkpoint_chars = 0
        done_reason: str | None = None

        def metadata(status: str, *, incomplete: bool, error: str | None = None) -> dict[str, Any]:
            return {
                "status": status,
                "thinking": thinking,
                "done_reason": done_reason,
                "segment_count": segment_count,
                "continuation_count": continuation_count,
                "eval_count_total": total_eval,
                "prompt_eval_count": prompt_eval_count,
                "total_duration": total_duration,
                "configured_num_predict": num_predict,
                "num_ctx": num_ctx,
                "timeout": status == "failed" and error is not None and "timeout" in error.lower(),
                "timeout_metadata": {
                    "connect": config.OLLAMA_CONNECT_TIMEOUT_SECONDS,
                    "first_token": config.OLLAMA_FIRST_TOKEN_TIMEOUT_SECONDS,
                    "stream_idle": config.OLLAMA_STREAM_IDLE_TIMEOUT_SECONDS,
                    "hard_total": config.OLLAMA_HARD_TOTAL_TIMEOUT_SECONDS,
                },
                "incomplete": incomplete,
                "segments": segment_results,
                "error": error,
            }

        def checkpoint(status: str, *, force: bool = False) -> None:
            nonlocal last_checkpoint_at, last_checkpoint_chars
            now_tick = loop.time()
            if not force and (
                now_tick - last_checkpoint_at < config.STREAM_CHECKPOINT_SECONDS
                and len(accumulated) - last_checkpoint_chars < config.STREAM_CHECKPOINT_CHARS
            ):
                return
            self._conversations.update_message(
                assistant_id,
                content=accumulated,
                model=selected,
                metadata=metadata(status, incomplete=True),
            )
            last_checkpoint_at = now_tick
            last_checkpoint_chars = len(accumulated)

        try:
            tool_passes = 0
            while True:
                remaining_budget = max_total_tokens - total_eval
                if remaining_budget <= 0:
                    done_reason = "max_total_generation_tokens"
                    break
                configured_segment_limit = min(num_predict, remaining_budget)
                client = OllamaClient(
                    config.OLLAMA_HOST,
                    selected,
                    config.OLLAMA_STREAM_IDLE_TIMEOUT_SECONDS,
                    True,
                    num_ctx,
                    configured_segment_limit,
                )
                segment_count += 1
                is_continuation = continuation_count > 0
                segment_raw = ""
                continuation_buffer = ""
                seam_decided = not is_continuation
                segment_added = 0
                latest: dict[str, Any] = {}
                tool_calls: list[dict[str, Any]] = []
                fence_filter = PrematureHtmlFenceFilter(accumulated)

                source = client.stream_chat(
                    current_messages,
                    tools=None if self._is_code_request(text) else self._registry.schemas(),
                )
                async for chunk in with_stream_timeouts(
                    source,
                    first_token_timeout=config.OLLAMA_FIRST_TOKEN_TIMEOUT_SECONDS,
                    idle_timeout=config.OLLAMA_STREAM_IDLE_TIMEOUT_SECONDS,
                    hard_deadline=hard_deadline,
                ):
                    latest = chunk
                    message = chunk.get("message") or {}
                    thinking_delta = message.get("thinking") or ""
                    if thinking_delta:
                        thinking += thinking_delta
                        yield {"type": "assistant.thinking.delta", "delta": thinking_delta}
                    raw_content_delta = message.get("content") or ""
                    if raw_content_delta:
                        segment_raw += raw_content_delta
                        content_delta = fence_filter.feed(raw_content_delta)
                        if content_delta and seam_decided:
                            accumulated += content_delta
                            segment_added += len(content_delta)
                            yield {"type": "assistant.content.delta", "delta": content_delta}
                        elif content_delta:
                            continuation_buffer += content_delta
                            if len(continuation_buffer) >= overlap_window:
                                merged, overlap = seam_merge(accumulated, continuation_buffer, overlap_window)
                                self._logger.event(
                                    "continuation_seam_merged",
                                    segment=segment_count,
                                    overlap_chars=overlap,
                                    buffered_chars=len(continuation_buffer),
                                )
                                accumulated += merged
                                segment_added += len(merged)
                                seam_decided = True
                                if merged:
                                    yield {"type": "assistant.content.delta", "delta": merged}
                    if message.get("tool_calls"):
                        tool_calls = message["tool_calls"]
                        yield {
                            "type": "assistant.tool_call.delta",
                            "tool_calls": [
                                {
                                    "id": call.get("id"),
                                    "name": (call.get("function") or {}).get("name"),
                                }
                                for call in tool_calls
                            ],
                        }
                    checkpoint("continuing" if is_continuation else ("answering" if accumulated else "thinking"))

                trailing_ticks = fence_filter.flush()
                if trailing_ticks:
                    if seam_decided:
                        accumulated += trailing_ticks
                        segment_added += len(trailing_ticks)
                        yield {"type": "assistant.content.delta", "delta": trailing_ticks}
                    else:
                        continuation_buffer += trailing_ticks
                if fence_filter.suppressed:
                    self._logger.event(
                        "premature_html_fence_suppressed",
                        segment=segment_count,
                        count=fence_filter.suppressed,
                    )

                if not seam_decided:
                    merged, overlap = seam_merge(accumulated, continuation_buffer, overlap_window)
                    self._logger.event(
                        "continuation_seam_merged",
                        segment=segment_count,
                        overlap_chars=overlap,
                        buffered_chars=len(continuation_buffer),
                    )
                    accumulated += merged
                    segment_added += len(merged)
                    if merged:
                        yield {"type": "assistant.content.delta", "delta": merged}

                done_reason = latest.get("done_reason")
                eval_count = int(latest.get("eval_count") or 0)
                segment_prompt_eval = int(latest.get("prompt_eval_count") or 0)
                segment_duration = int(latest.get("total_duration") or 0)
                total_eval += eval_count
                prompt_eval_count = max(prompt_eval_count, segment_prompt_eval)
                total_duration += segment_duration
                segment_result = {
                    "index": segment_count,
                    "done": latest.get("done"),
                    "done_reason": done_reason,
                    "eval_count": eval_count,
                    "prompt_eval_count": segment_prompt_eval,
                    "total_duration": segment_duration,
                    "eval_duration": latest.get("eval_duration"),
                    "error": latest.get("error"),
                    "added_chars": segment_added,
                }
                segment_results.append(segment_result)
                checkpoint("continuing" if done_reason == "length" else "answering", force=True)
                yield {"type": "generation.segment.completed", **segment_result}

                if tool_calls and segment_raw.strip():
                    self._logger.event(
                        "late_tool_call_ignored_after_content",
                        segment=segment_count,
                        content_length=len(segment_raw),
                        tool_names=[(call.get("function") or {}).get("name") for call in tool_calls],
                    )
                    tool_calls = []

                if tool_calls:
                    tool_passes += 1
                    if tool_passes >= config.MAX_ITERATIONS:
                        raise RuntimeError("streaming tool loop reached MAX_ITERATIONS")
                    session.add_assistant_raw({
                        "role": "assistant",
                        "content": segment_raw,
                        "tool_calls": tool_calls,
                    })
                    for call in tool_calls:
                        function = call.get("function") or {}
                        name = function.get("name")
                        args = function.get("arguments") or {}
                        result = self._registry.dispatch(name, args, tool_call_id=call.get("id"))
                        session.add_tool_result(name, result, args, tool_call_id=call.get("id"))
                    current_messages = session.get_context_messages(max_context_messages)
                    continue

                structure_incomplete = (
                    has_incomplete_structure(accumulated)
                    or response_missing_requested_structure(text, accumulated)
                )
                should_continue = (
                    auto_continue
                    and continuation_count < max_continuations
                    and total_eval < max_total_tokens
                    and (self._is_code_request(text) or structure_incomplete)
                    and (done_reason == "length" or structure_incomplete)
                )
                if not should_continue:
                    break
                if segment_added < 32:
                    no_progress_segments += 1
                else:
                    no_progress_segments = 0
                if no_progress_segments >= 2:
                    done_reason = "no_progress"
                    break
                continuation_count += 1
                checkpoint("continuing", force=True)
                yield {
                    "type": "generation.continuation.started",
                    "continuation": continuation_count,
                    "segment": segment_count + 1,
                }
                current_messages = continuation_messages(base_messages, accumulated, num_ctx=num_ctx)

            incomplete = (
                done_reason != "stop"
                or has_incomplete_structure(accumulated)
                or response_missing_requested_structure(text, accumulated)
            )
            status = "completed" if done_reason == "stop" and not incomplete else "length_limited"
            self._conversations.update_message(
                assistant_id,
                content=accumulated,
                model=selected,
                metadata=metadata(status, incomplete=incomplete),
            )
            self._conversations.merge_message_metadata(
                user_record["id"],
                {"status": "completed", "assistant_message_id": assistant_id, "model_used": selected},
            )
            yield {
                "type": "generation.completed",
                "assistant_message_id": assistant_id,
                "done_reason": done_reason,
                "status": status,
                "segment_count": segment_count,
                "continuation_count": continuation_count,
                "eval_count_total": total_eval,
                "prompt_eval_count": prompt_eval_count,
                "total_duration": total_duration,
                "configured_num_predict": num_predict,
                "num_ctx": num_ctx,
                "incomplete": incomplete,
            }
        except (asyncio.CancelledError, GeneratorExit):
            self._conversations.update_message(
                assistant_id,
                content=accumulated,
                model=selected,
                metadata=metadata("cancelled", incomplete=True),
            )
            self._conversations.merge_message_metadata(
                user_record["id"], {"status": "completed", "assistant_message_id": assistant_id}
            )
            self._logger.event(
                "generation_cancelled",
                assistant_message_id=assistant_id,
                content_length=len(accumulated),
                segment_count=segment_count,
            )
            raise
        except Exception as exc:
            error = str(exc)
            self._conversations.update_message(
                assistant_id,
                content=accumulated,
                model=selected,
                metadata=metadata("failed", incomplete=True, error=error),
            )
            self._conversations.merge_message_metadata(
                user_record["id"],
                {"status": "failed", "assistant_message_id": assistant_id, "error": error},
            )
            self._logger.error(
                "generation_failed",
                console_message=f"[Siena] Streaming generation failed: {error}",
                assistant_message_id=assistant_id,
                content_length=len(accumulated),
                segment_count=segment_count,
            )
            yield {
                "type": "generation.failed",
                "assistant_message_id": assistant_id,
                "error": error,
                "incomplete": True,
            }
