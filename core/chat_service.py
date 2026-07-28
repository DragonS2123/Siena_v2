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
