"""Business logic for a local conversation turn."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime
from typing import Any

import config
from core.agent_loop import AgentResult, run as run_agent_loop
from core.errors import SienaInfraError
from core.attachment_service import AttachmentService
from core.model_roles import ModelRoles
from core.model_router import ModelSelection, is_coding_intent, resolve_model
from core.model_provider import ModelProvider
from core.provider_factory import ProviderCatalog, create_provider
from core.session import Session
from core.streaming_chat import (
    PrematureHtmlFenceFilter,
    continuation_messages,
    has_incomplete_structure,
    response_missing_requested_structure,
    seam_merge,
    with_stream_timeouts,
)
from memory.user_memory_context import build_user_memory_context, memory_context_event_fields
from memory.policy import recent_messages, memory_turn
from storage.conversation_store import ConversationStore
from core.runtime_settings import RuntimeSettingsService, RuntimeSettingsSnapshot
from tools.registry import ToolRegistry


class ChatService:
    def __init__(
        self,
        conversations: ConversationStore,
        roles: ModelRoles,
        catalog: ProviderCatalog,
        registry: ToolRegistry,
        long_memory: Any,
        attachments: AttachmentService,
        logger: Any,
        settings: RuntimeSettingsService,
    ):
        self._conversations = conversations
        self._roles = roles
        self._catalog = catalog
        self._registry = registry
        self._long_memory = long_memory
        self._attachments = attachments
        self._logger = logger
        self._settings = settings

    def _memory_context(self, text: str, conversation_id: str) -> str:
        context = build_user_memory_context(self._long_memory, text)
        self._logger.event('memory_context', conversation_id=conversation_id,
                           **memory_context_event_fields(context))
        return context

    @staticmethod
    def _is_code_request(text: str, attachments: list[dict[str, Any]] | None = None) -> bool:
        return is_coding_intent(text, attachments or [])

    @staticmethod
    def _generation_options(snapshot: RuntimeSettingsSnapshot) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": float(snapshot.get("temperature")),
            "top_p": float(snapshot.get("top_p")),
            "top_k": int(snapshot.get("top_k")),
            "repeat_penalty": float(snapshot.get("repeat_penalty")),
        }
        if snapshot.get("seed") is not None:
            options["seed"] = int(snapshot.get("seed"))
        return options

    def _resolve_selection(
        self,
        *,
        conversation: dict[str, Any],
        text: str,
        attachments: list[dict[str, Any]],
        explicit_mode: str,
        model_override: str | None,
        snapshot: RuntimeSettingsSnapshot,
    ) -> ModelSelection:
        return resolve_model(
            text=text,
            attachments=attachments,
            explicit_mode=explicit_mode,
            request_override=model_override,
            conversation_override=conversation.get("model_override"),
            roles=self._roles.assignments(),
            code_auto_enabled=bool(snapshot.get("enable_code_specialist_auto")),
            settings_revision=snapshot.revision,
        )

    def _log_model_role(self, selection: ModelSelection, conversation_id: str) -> None:
        fields = {
            **selection.metadata(),
            "conversation_id": conversation_id,
        }
        self._logger.event("model.role.resolved", **fields)
    def _generation_limits(
        self,
        text: str,
        session: Session,
        *,
        coding: bool,
        snapshot: RuntimeSettingsSnapshot,
    ) -> tuple[int, int, int, int]:
        num_ctx = int(snapshot.get("context_size"))
        requested = int(snapshot.get("code_output_tokens" if coding else "chat_output_tokens"))
        timeout = int(snapshot.get("code_request_timeout_seconds" if coding else "request_timeout_seconds"))
        max_messages = int(snapshot.get("max_context_messages"))
        estimated_prompt_tokens = 0
        effective_messages = max_messages
        while effective_messages >= 4:
            prompt_chars = sum(
                len(str(message.get("content") or ""))
                for message in session.get_context_messages(effective_messages)
            )
            estimated_prompt_tokens = math.ceil(prompt_chars / 2)
            if num_ctx - estimated_prompt_tokens - 512 >= requested or effective_messages == 4:
                break
            effective_messages = max(4, effective_messages - 4)
        available = num_ctx - estimated_prompt_tokens - 512
        if available < 64:
            raise ValueError(
                f"context budget exhausted: num_ctx={num_ctx}, estimated_prompt_tokens={estimated_prompt_tokens}"
            )
        effective = min(requested, available)
        self._logger.event(
            "generation.budget",
            code_request=coding,
            num_ctx=num_ctx,
            requested_output_tokens=requested,
            effective_output_tokens=effective,
            estimated_prompt_tokens=estimated_prompt_tokens,
            request_timeout_seconds=timeout,
            configured_context_messages=max_messages,
            effective_context_messages=effective_messages,
            settings_revision=snapshot.revision,
        )
        return num_ctx, effective, timeout, effective_messages

    async def _stream_model_with_retry(
        self,
        client: ModelProvider,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        hard_deadline: float,
        requested_role: str,
        resolved_model: str,
        conversation_id: str,
        assistant_message_id: str,
        segment: int,
    ):
        attempt = 0
        while True:
            emitted = False
            timed = None
            try:
                source = client.stream_chat(messages, tools=tools)
                timed = with_stream_timeouts(
                    source,
                    first_token_timeout=config.INFERENCE_FIRST_TOKEN_TIMEOUT_SECONDS,
                    idle_timeout=config.INFERENCE_STREAM_IDLE_TIMEOUT_SECONDS,
                    hard_deadline=hard_deadline,
                )
                async for chunk in timed:
                    emitted = True
                    yield chunk
                return
            except SienaInfraError as exc:
                if emitted or attempt >= 2:
                    raise
                attempt += 1
                delay = 0.25 * (2 ** (attempt - 1))
                self._logger.event(
                    "model.request.retrying",
                    requested_role=requested_role,
                    resolved_model=resolved_model,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message_id,
                    segment=segment,
                    attempt=attempt,
                    backoff_seconds=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            finally:
                if timed is not None:
                    await timed.aclose()

    async def _review_generated_code(
        self,
        *,
        snapshot: RuntimeSettingsSnapshot,
        installed: set[str],
        conversation_id: str,
        assistant_message_id: str,
        user_request: str,
        content: str,
    ) -> dict[str, Any]:
        reviewer_model = self._roles.assignments()["reviewer"]
        if reviewer_model not in installed:
            raise ValueError(f"selected reviewer model is unavailable from inference provider: {reviewer_model}")
        max_chars = max(4000, (int(snapshot.get("context_size")) - 2048) * 2)
        review_content = content[-max_chars:]
        truncated = len(review_content) != len(content)
        started_at = datetime.now().astimezone().isoformat()
        self._logger.event(
            "model.request.started",
            requested_role="reviewer",
            resolved_model=reviewer_model,
            selection_reason="code_review_pipeline",
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            settings_revision=snapshot.revision,
            started_at=started_at,
        )
        client = create_provider(
            snapshot,
            reviewer_model,
            int(snapshot.get("request_timeout_seconds")),
            False,
            int(snapshot.get("context_size")),
            min(2048, int(snapshot.get("chat_output_tokens"))),
            self._generation_options(snapshot),
        )
        prompt = (
            "Review the generated code against the user's task. Do not reproduce the whole file. "
            "Return APPROVED if there are no material issues; otherwise return concise, actionable issues only.\n\n"
            f"User task:\n{user_request}\n\nGenerated code{' (tail only)' if truncated else ''}:\n{review_content}"
        )
        raw = await asyncio.to_thread(client.chat, [{"role": "user", "content": prompt}])
        finished_at = datetime.now().astimezone().isoformat()
        feedback = str((raw.get("message") or {}).get("content") or "").strip()
        result = {
            "requested_role": "reviewer",
            "resolved_model": reviewer_model,
            "ollama_model": str(raw.get("model") or reviewer_model),
            "selection_reason": "code_review_pipeline",
            "started_at": started_at,
            "finished_at": finished_at,
            "done_reason": raw.get("done_reason"),
            "generated_tokens": int(raw.get("eval_count") or 0),
            "truncated_input": truncated,
            "feedback": feedback,
        }
        self._logger.event("model.request.completed", **{key: value for key, value in result.items() if key != "feedback"})
        return result

    async def resume_interrupted_message(self, record: dict[str, Any]) -> None:
        """Resume a crash-left stream in its existing assistant row, without a new message id."""
        snapshot = self._settings.current()
        if not bool(snapshot.get("auto_resume_interrupted")):
            return
        assistant_id = str(record["id"])
        conversation_id = str(record["conversation_id"])
        metadata = dict(record.get("metadata") or {})
        accumulated = str(record.get("content") or "")
        selected = str(metadata.get("resolved_model") or record.get("model") or "")
        requested_role = str(metadata.get("requested_role") or "chat")
        installed = {item["name"] for item in self._catalog.refresh().get("models", [])}
        if not selected or selected not in installed:
            metadata.update({"status": "incomplete", "incomplete": True, "resume_available": True, "recovery_error": f"model unavailable: {selected}"})
            self._conversations.update_message(assistant_id, content=accumulated, model=selected or None, metadata=metadata)
            return
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            return
        base_messages: list[dict[str, Any]] = [{"role": "system", "content": config.SYSTEM_PROMPT}]
        user_request = ""
        for message in conversation["messages"]:
            if message["id"] == assistant_id:
                break
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "user":
                user_request = content
                base_messages.append({"role": "user", "content": content})
            elif role == "assistant":
                base_messages.append({"role": "assistant", "content": content})
        base_messages = base_messages[:1] + recent_messages(base_messages[1:])
        memory_context = self._memory_context(user_request, conversation_id)
        if memory_context:
            base_messages.append({"role": "user", "content": memory_context})
        num_ctx = int(snapshot.get("context_size"))
        num_predict = int(snapshot.get("code_output_tokens" if requested_role == "coder" else "chat_output_tokens"))
        max_rounds = int(snapshot.get("auto_continue_max_rounds"))
        max_total = int(snapshot.get("auto_continue_max_total_tokens"))
        total_eval = int(metadata.get("eval_count_total") or 0)
        continuation_count = int(metadata.get("continuation_count") or 0)
        segment_count = int(metadata.get("segment_count") or 0)
        segments = list(metadata.get("segments") or [])
        deadline = asyncio.get_running_loop().time() + int(snapshot.get("auto_continue_timeout_seconds"))
        done_reason: str | None = str(metadata.get("done_reason") or "backend_restart")
        self._logger.event(
            "model.request.continuing",
            requested_role=requested_role,
            resolved_model=selected,
            conversation_id=conversation_id,
            assistant_message_id=assistant_id,
            round=continuation_count + 1,
            reason="backend_restart",
            accumulated_characters=len(accumulated),
            total_generated_tokens=total_eval,
        )
        active_stream = None
        try:
            while continuation_count < max_rounds and total_eval < max_total:
                continuation_count += 1
                segment_count += 1
                segment_limit = min(num_predict, max_total - total_eval)
                client = create_provider(
                    snapshot, selected,
                    int(snapshot.get("code_request_timeout_seconds" if requested_role == "coder" else "request_timeout_seconds")),
                    True, num_ctx, segment_limit, self._generation_options(snapshot),
                )
                started_at = datetime.now().astimezone().isoformat()
                self._logger.event(
                    "model.request.started",
                    requested_role=requested_role,
                    resolved_model=selected,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    continuation=continuation_count,
                    segment=segment_count,
                    selection_reason="backend_restart_resume",
                    started_at=started_at,
                )
                raw_content = ""
                latest: dict[str, Any] = {}
                messages = continuation_messages(base_messages, accumulated, num_ctx=num_ctx)
                active_stream = self._stream_model_with_retry(
                    client, messages, tools=None if requested_role == "coder" else self._registry.schemas(),
                    hard_deadline=deadline, requested_role=requested_role, resolved_model=selected,
                    conversation_id=conversation_id, assistant_message_id=assistant_id, segment=segment_count,
                )
                async for chunk in active_stream:
                    latest = chunk
                    raw_content += str((chunk.get("message") or {}).get("content") or "")
                await active_stream.aclose()
                active_stream = None
                merged, overlap = seam_merge(accumulated, raw_content, int(snapshot.get("auto_continue_overlap_window")))
                accumulated += merged
                eval_count = int(latest.get("eval_count") or 0)
                total_eval += eval_count
                done_reason = latest.get("done_reason")
                segment_result = {
                    "index": segment_count,
                    "continuation": continuation_count,
                    "requested_role": requested_role,
                    "resolved_model": selected,
                    "ollama_model": str(latest.get("model") or selected),
                    "done_reason": done_reason,
                    "eval_count": eval_count,
                    "overlap_removed": overlap,
                    "added_chars": len(merged),
                    "raw_content": raw_content,
                    "started_at": started_at,
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
                segments.append(segment_result)
                metadata.update({
                    "status": "validating", "incomplete": True, "resume_available": False,
                    "done_reason": done_reason, "continuation_count": continuation_count,
                    "segment_count": segment_count, "eval_count_total": total_eval,
                    "segments": segments,
                    "last_continuation_context": {"tail": accumulated[-int(snapshot.get("auto_continue_overlap_window")):]} ,
                })
                self._conversations.update_message(assistant_id, content=accumulated, model=selected, metadata=metadata)
                self._logger.event(
                    "model.request.completed" if done_reason != "length" else "model.request.limit_reached",
                    requested_role=requested_role, resolved_model=selected,
                    ollama_model=segment_result["ollama_model"], conversation_id=conversation_id,
                    assistant_message_id=assistant_id, continuation=continuation_count,
                    segment=segment_count, done_reason=done_reason, generated_tokens=eval_count,
                    overlap_removed=overlap, accumulated_characters=len(accumulated),
                    started_at=started_at, finished_at=segment_result["finished_at"],
                )
                incomplete_structure = has_incomplete_structure(accumulated) or response_missing_requested_structure(user_request, accumulated)
                limit_reached = done_reason == "length" or eval_count >= max(1, int(segment_limit * 0.98))
                if not limit_reached and not incomplete_structure:
                    break
                if not merged:
                    done_reason = "no_progress"
                    break
            incomplete = has_incomplete_structure(accumulated) or response_missing_requested_structure(user_request, accumulated) or done_reason in {"length", "no_progress"}
            metadata.update({"status": "incomplete" if incomplete else "completed", "incomplete": incomplete, "resume_available": incomplete})
            self._conversations.update_message(assistant_id, content=accumulated, model=selected, metadata=metadata)
            self._logger.event(
                "message.incomplete" if incomplete else "message.completed",
                conversation_id=conversation_id, assistant_message_id=assistant_id,
                requested_role=requested_role, resolved_model=selected,
                done_reason=done_reason, continuation_count=continuation_count,
                generated_tokens=total_eval, content_length=len(accumulated), incomplete=incomplete,
                recovery=True,
            )
        except Exception as exc:
            metadata.update({"status": "incomplete", "incomplete": True, "resume_available": True, "recovery_error": str(exc)})
            self._conversations.update_message(assistant_id, content=accumulated, model=selected, metadata=metadata)
            self._logger.error(
                "generation_recovery_failed",
                console_message=f"[Siena] Interrupted generation recovery failed: {exc}",
                conversation_id=conversation_id, assistant_message_id=assistant_id,
                requested_role=requested_role, resolved_model=selected,
            )
        finally:
            if active_stream is not None:
                await active_stream.aclose()

    async def turn(
        self,
        conversation_id: str,
        text: str,
        *,
        model_override: str | None = None,
        explicit_mode: str = "auto",
        attachment_context: str = "",
        ocr_context: str = "",
        vision_context: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self._settings.current()
        selection = self._resolve_selection(
            conversation=conversation,
            text=text,
            attachments=attachments or [],
            explicit_mode=explicit_mode,
            model_override=model_override,
            snapshot=snapshot,
        )
        catalog = self._catalog.refresh()
        installed = {model["name"] for model in catalog.get("models", [])}
        selected = selection.resolved_model
        if selected not in installed:
            raise ValueError(f"selected {selection.requested_role} model is unavailable from inference provider: {selected}")
        self._log_model_role(selection, conversation_id)

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
        session.memory_user_text = text
        session.conversation_id = conversation_id
        session.user_message_id = user_record["id"]
        for message in recent_messages(conversation["messages"]):
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                session.add_user(content)
            elif role == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": content})

        now = datetime.now().astimezone()
        context = [
            f"[RUNTIME]\ndate={now:%Y-%m-%d}\ntime={now:%H:%M:%S}\ntimezone={now.tzname()}\n[/RUNTIME]",
            self._memory_context(text, conversation_id),
            attachment_context,
            ocr_context,
            vision_context,
        ]
        combined = "\n\n".join(item for item in context if item)
        session.add_user(f"{text}\n\n{combined}" if combined else text)
        num_ctx, num_predict, timeout, max_context_messages = self._generation_limits(
            text, session, coding=selection.requested_role == "coder", snapshot=snapshot
        )
        client = create_provider(
            snapshot,
            selected,
            timeout,
            config.OLLAMA_THINK,
            num_ctx,
            num_predict,
            self._generation_options(snapshot),
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
            metadata={**result.metadata(), **selection.metadata()},
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
            "requested_role": selection.requested_role,
            "selection_reason": selection.selection_reason,
            "settings_revision": snapshot.revision,
            "attachments": stored_attachments,
            **result.metadata(),
        }
    async def stream_turn(
        self,
        conversation_id: str,
        text: str,
        *,
        model_override: str | None = None,
        explicit_mode: str = "auto",
        review_code: bool = False,
        attachments: list[dict[str, Any]] | None = None,
    ):
        """Yield normalized end-to-end generation events for one logical message."""
        conversation = self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        snapshot = self._settings.current()
        selection = self._resolve_selection(
            conversation=conversation,
            text=text,
            attachments=attachments or [],
            explicit_mode=explicit_mode,
            model_override=model_override,
            snapshot=snapshot,
        )
        catalog = self._catalog.refresh()
        installed = {model["name"] for model in catalog.get("models", [])}
        selected = selection.resolved_model
        if selected not in installed:
            raise ValueError(f"selected {selection.requested_role} model is unavailable from inference provider: {selected}")
        self._log_model_role(selection, conversation_id)

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
        session.memory_user_text = text
        session.conversation_id = conversation_id
        session.user_message_id = user_record["id"]
        for message in recent_messages(conversation["messages"]):
            role = message.get("role")
            content = message.get("content") or ""
            if role == "user":
                session.add_user(content)
            elif role == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": content})
        now = datetime.now().astimezone()
        runtime_context = [
            f"[RUNTIME]\ndate={now:%Y-%m-%d}\ntime={now:%H:%M:%S}\ntimezone={now.tzname()}\n[/RUNTIME]",
            self._memory_context(text, conversation_id),
            attachment_context,
            ocr_context,
            vision_context,
        ]
        combined = "\n\n".join(item for item in runtime_context if item)
        session.add_user(f"{text}\n\n{combined}" if combined else text)
        num_ctx, num_predict, _request_timeout, max_context_messages = self._generation_limits(
            text,
            session,
            coding=selection.requested_role == "coder",
            snapshot=snapshot,
        )
        auto_continue = bool(snapshot.get("auto_continue_enabled"))
        max_continuations = int(snapshot.get("auto_continue_max_rounds"))
        max_total_tokens = int(snapshot.get("auto_continue_max_total_tokens"))
        overlap_window = int(snapshot.get("auto_continue_overlap_window"))
        repair_rounds = int(snapshot.get("auto_continue_repair_rounds"))

        assistant = self._conversations.append_message(
            conversation_id,
            "assistant",
            "",
            model=selected,
            metadata={
                **selection.metadata(),
                "status": "generating",
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
                    "connect": config.INFERENCE_CONNECT_TIMEOUT_SECONDS,
                    "first_token": config.INFERENCE_FIRST_TOKEN_TIMEOUT_SECONDS,
                    "stream_idle": config.INFERENCE_STREAM_IDLE_TIMEOUT_SECONDS,
                    "hard_total": int(snapshot.get("auto_continue_timeout_seconds")),
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
            "requested_role": selection.requested_role,
            "selection_reason": selection.selection_reason,
            "settings_revision": snapshot.revision,
            "attachments": stored_attachments,
        }

        accumulated = ""
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
        hard_deadline = loop.time() + int(snapshot.get("auto_continue_timeout_seconds"))
        last_checkpoint_at = loop.time()
        last_checkpoint_chars = 0
        done_reason: str | None = None
        reviewer_result: dict[str, Any] | None = None

        def metadata(status: str, *, incomplete: bool, error: str | None = None) -> dict[str, Any]:
            return {
                **({'sources': list(session.web_sources)} if session.web_sources else {}),
                **selection.metadata(),
                "status": status,
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
                    "connect": config.INFERENCE_CONNECT_TIMEOUT_SECONDS,
                    "first_token": config.INFERENCE_FIRST_TOKEN_TIMEOUT_SECONDS,
                    "stream_idle": config.INFERENCE_STREAM_IDLE_TIMEOUT_SECONDS,
                    "hard_total": int(snapshot.get("auto_continue_timeout_seconds")),
                },
                "incomplete": incomplete,
                "segments": segment_results,
                "generation_settings": {
                    "context_size": num_ctx,
                    "output_tokens": num_predict,
                    "auto_continue_enabled": auto_continue,
                    "auto_continue_max_rounds": max_continuations,
                    "auto_continue_max_total_tokens": max_total_tokens,
                    "auto_continue_timeout_seconds": int(snapshot.get("auto_continue_timeout_seconds")),
                    "auto_continue_overlap_window": overlap_window,
                    "auto_continue_repair_rounds": repair_rounds,
                    **self._generation_options(snapshot),
                },
                "last_continuation_context": {
                    "tail": accumulated[-overlap_window:],
                    "tail_characters": min(len(accumulated), overlap_window),
                },
                "review": reviewer_result,
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

        active_stream = None
        try:
            tool_passes = 0
            while True:
                remaining_budget = max_total_tokens - total_eval
                if remaining_budget <= 0:
                    done_reason = "max_total_generation_tokens"
                    break
                configured_segment_limit = min(num_predict, remaining_budget)
                client = create_provider(
                    snapshot,
                    selected,
                    _request_timeout,
                    True,
                    num_ctx,
                    configured_segment_limit,
                    self._generation_options(snapshot),
                )
                segment_count += 1
                request_started_at = datetime.now().astimezone().isoformat()
                self._logger.event(
                    "model.request.started",
                    requested_role=selection.requested_role,
                    resolved_model=selected,
                    ollama_model=selected,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    continuation=continuation_count,
                    segment=segment_count,
                    settings_revision=snapshot.revision,
                    configured_output_tokens=configured_segment_limit,
                    started_at=request_started_at,
                )
                is_continuation = continuation_count > 0
                segment_raw = ""
                continuation_buffer = ""
                seam_decided = not is_continuation
                segment_added = 0
                overlap_removed = 0
                latest: dict[str, Any] = {}
                tool_calls: list[dict[str, Any]] = []
                fence_filter = PrematureHtmlFenceFilter(accumulated)

                active_stream = self._stream_model_with_retry(
                    client,
                    current_messages,
                    tools=None if selection.requested_role == "coder" else self._registry.schemas(),
                    hard_deadline=hard_deadline,
                    requested_role=selection.requested_role,
                    resolved_model=selected,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    segment=segment_count,
                )
                async for chunk in active_stream:
                    latest = chunk
                    message = chunk.get("message") or {}
                    thinking_delta = message.get("thinking") or ""
                    if thinking_delta:
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
                                overlap_removed += overlap
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
                    checkpoint("continuing" if is_continuation else "generating")

                await active_stream.aclose()
                active_stream = None
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
                    overlap_removed += overlap
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
                    "overlap_removed": overlap_removed,
                    "continuation": continuation_count,
                    "requested_role": selection.requested_role,
                    "resolved_model": selected,
                    "ollama_model": str(latest.get("model") or selected),
                    "started_at": request_started_at,
                    "finished_at": datetime.now().astimezone().isoformat(),
                    "raw_content": segment_raw,
                }
                segment_results.append(segment_result)
                checkpoint("validating", force=True)
                request_event = "model.request.limit_reached" if done_reason == "length" else "model.request.completed"
                self._logger.event(
                    request_event,
                    requested_role=selection.requested_role,
                    resolved_model=selected,
                    ollama_model=segment_result["ollama_model"],
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    continuation=continuation_count,
                    segment=segment_count,
                    done_reason=done_reason,
                    generated_tokens=eval_count,
                    overlap_removed=overlap_removed,
                    accumulated_characters=len(accumulated),
                    started_at=request_started_at,
                    finished_at=segment_result["finished_at"],
                )
                yield {"type": "generation.segment.completed", **{k: v for k, v in segment_result.items() if k != "raw_content"}}

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
                        self._logger.event('tool_dispatch', name=name, tool_call_id=call.get('id'),
                                           argument_names=sorted(args), conversation_id=conversation_id)
                        with memory_turn(text, conversation_id, user_record["id"]):
                            result = await self._registry.dispatch_async(name, args, tool_call_id=call.get("id"))
                        self._logger.event('tool_result', name=name, tool_call_id=call.get('id'),
                                           ok=result.ok, error=result.error, conversation_id=conversation_id)
                        session.add_tool_result(name, result, args, tool_call_id=call.get("id"))
                    current_messages = session.get_context_messages(max_context_messages)
                    continue

                structure_incomplete = (
                    has_incomplete_structure(accumulated)
                    or response_missing_requested_structure(text, accumulated)
                )
                limit_reached = done_reason == "length" or (
                    latest.get("done") is True
                    and configured_segment_limit > 0
                    and eval_count >= max(1, int(configured_segment_limit * 0.98))
                )
                allowed_rounds = max_continuations + (repair_rounds if structure_incomplete and not limit_reached else 0)
                should_continue = (
                    auto_continue
                    and continuation_count < allowed_rounds
                    and total_eval < max_total_tokens
                    and (limit_reached or structure_incomplete)
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
                continuation_reason = "output_limit" if limit_reached else "structure_repair"
                checkpoint("continuing", force=True)
                self._logger.event(
                    "model.request.continuing",
                    requested_role=selection.requested_role,
                    resolved_model=selected,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    round=continuation_count,
                    reason=continuation_reason,
                    previous_done_reason=done_reason,
                    accumulated_characters=len(accumulated),
                    total_generated_tokens=total_eval,
                )
                yield {
                    "type": "generation.continuation.started",
                    "continuation": continuation_count,
                    "segment": segment_count + 1,
                    "reason": continuation_reason,
                }
                current_messages = continuation_messages(base_messages, accumulated, num_ctx=num_ctx)

            source_suffix = session.citation_suffix(accumulated)
            if source_suffix:
                accumulated += source_suffix
                yield {"type": "assistant.content.delta", "delta": source_suffix}

            final_structure_incomplete = (
                has_incomplete_structure(accumulated)
                or response_missing_requested_structure(text, accumulated)
            )
            review_enabled = selection.requested_role == "coder" and (
                review_code or bool(snapshot.get("enable_reviewer_explicit"))
            )
            if review_enabled and accumulated:
                checkpoint("validating", force=True)
                yield {
                    "type": "generation.review.started",
                    "requested_role": "reviewer",
                    "resolved_model": self._roles.assignments()["reviewer"],
                }
                reviewer_result = await self._review_generated_code(
                    snapshot=snapshot,
                    installed=installed,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    user_request=text,
                    content=accumulated,
                )
                yield {
                    "type": "generation.review.completed",
                    "requested_role": "reviewer",
                    "resolved_model": reviewer_result["resolved_model"],
                    "done_reason": reviewer_result["done_reason"],
                }
            incomplete = final_structure_incomplete or done_reason in {
                "length", "max_total_generation_tokens", "no_progress"
            }
            status = "incomplete" if incomplete else "completed"
            self._conversations.update_message(
                assistant_id,
                content=accumulated,
                model=selected,
                metadata=metadata(status, incomplete=incomplete),
            )
            self._conversations.merge_message_metadata(
                user_record["id"],
                {
                    "status": "completed",
                    "assistant_message_id": assistant_id,
                    "model_used": selected,
                    **selection.metadata(),
                },
            )
            self._logger.event(
                "message.incomplete" if incomplete else "message.completed",
                conversation_id=conversation_id,
                assistant_message_id=assistant_id,
                requested_role=selection.requested_role,
                resolved_model=selected,
                done_reason=done_reason,
                continuation_count=continuation_count,
                generated_tokens=total_eval,
                content_length=len(accumulated),
                incomplete=incomplete,
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
                "model.request.cancelled",
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
        finally:
            if active_stream is not None:
                await active_stream.aclose()
