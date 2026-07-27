"""Validation, persistence, OCR and vision processing for chat attachments."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from core.image_intent import decide_vision
from core.model_roles import ModelRoles
from ocr.glm_ocr_service import GlmOcrService, clean_ocr_text
from storage.conversation_store import ConversationStore
from vision.qwen_vision_service import QwenVisionService

_TEXT_TYPES = {"text", "code", "markdown", "json", "log"}
_EXTENSIONS = {
    "text": ".txt", "code": ".txt", "markdown": ".md", "json": ".json", "log": ".log", "image": ".img"
}


class AttachmentService:
    def __init__(
        self,
        root: Path,
        conversations: ConversationStore,
        roles: ModelRoles,
        logger: Any,
    ):
        self._root = root
        self._conversations = conversations
        self._roles = roles
        self._logger = logger

    async def process(
        self,
        conversation_id: str,
        message_id: str,
        attachments: list[dict[str, Any]],
        user_prompt: str,
    ) -> tuple[str, str, str, list[dict[str, Any]]]:
        if len(attachments) > config.MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError("too many attachments")
        total_text = sum(
            len(str(item.get("content") or ""))
            for item in attachments
            if str(item.get("type") or "") in _TEXT_TYPES
        )
        if total_text > config.MAX_TOTAL_ATTACHMENT_TEXT_CHARS:
            raise ValueError("total attachment text is too large")
        text_blocks: list[str] = []
        ocr_blocks: list[str] = []
        vision_blocks: list[str] = []
        public: list[dict[str, Any]] = []

        for attachment in attachments:
            kind = str(attachment.get("type") or "")
            name = Path(str(attachment.get("name") or "attachment")).name
            mime = str(attachment.get("mime") or "application/octet-stream")
            if kind in _TEXT_TYPES:
                content = str(attachment.get("content") or "")
                if len(content) > config.MAX_ATTACHMENT_TEXT_CHARS:
                    raise ValueError(f"attachment is too large: {name}")
                raw = content.encode("utf-8")
                text_blocks.append(f"[{name}]\n{content}")
                processing: dict[str, Any] = {}
            elif kind == "image":
                if not mime.startswith("image/"):
                    raise ValueError(f"invalid image MIME: {mime}")
                encoded = str(attachment.get("data_url") or "").split(",", 1)[-1]
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise ValueError(f"invalid image data: {name}") from exc
                if len(raw) > config.MAX_IMAGE_ATTACHMENT_BYTES:
                    raise ValueError(f"image is too large: {name}")
                processing = await self._process_image(encoded, name, user_prompt)
                if processing.get("ocr_text"):
                    ocr_blocks.append(f"[{name}]\n{processing['ocr_text']}")
                if processing.get("vision_text"):
                    vision_blocks.append(f"[{name}]\n{processing['vision_text']}")
            else:
                raise ValueError(f"unsupported attachment type: {kind}")

            attachment_id = str(uuid.uuid4())
            suffix = self._safe_suffix(name, kind)
            relative = Path(conversation_id) / message_id / f"{attachment_id}{suffix}"
            destination = (self._root / relative).resolve()
            root = self._root.resolve()
            if root not in destination.parents:
                raise ValueError("unsafe attachment path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            stored = self._conversations.add_attachment({
                "id": attachment_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "kind": kind,
                "source": "desktop",
                "original_name": name,
                "stored_filename": destination.name,
                "stored_relative_path": relative.as_posix(),
                "mime_type": mime,
                "size_bytes": len(raw),
                "created_at": datetime.now().astimezone().isoformat(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "metadata": processing,
            })
            public.append(stored)

        return (
            "\n\n".join(text_blocks),
            "Attached image OCR:\n" + "\n\n".join(ocr_blocks) if ocr_blocks else "",
            "Attached image vision:\n" + "\n\n".join(vision_blocks) if vision_blocks else "",
            public,
        )

    async def _process_image(self, encoded: str, name: str, prompt: str) -> dict[str, Any]:
        roles = self._roles.assignments()
        result: dict[str, Any] = {}
        ocr = GlmOcrService(config.OLLAMA_HOST, roles["ocr"], config.OCR_TIMEOUT_SECONDS, self._logger)
        try:
            raw_ocr = await asyncio.to_thread(ocr.extract_text, encoded)
            result["ocr_text"] = clean_ocr_text(raw_ocr["text"])[: config.OCR_MAX_EXTRACTED_CHARS]
            result["ocr_status"] = "completed"
        except Exception as exc:
            result.update({"ocr_status": "unavailable", "ocr_error": str(exc)[:300]})

        if decide_vision(prompt, True).run_vision:
            vision = QwenVisionService(
                config.OLLAMA_HOST, roles["vision"], config.IMAGE_UNDERSTANDING_TIMEOUT_SECONDS, self._logger
            )
            try:
                described = await asyncio.to_thread(vision.describe_image, encoded, prompt)
                result["vision_text"] = described["text"][: config.IMAGE_UNDERSTANDING_MAX_OUTPUT_CHARS]
                result["vision_status"] = "completed"
            except Exception as exc:
                result.update({"vision_status": "unavailable", "vision_error": str(exc)[:300]})
        return result

    @staticmethod
    def _safe_suffix(name: str, kind: str) -> str:
        suffix = Path(name).suffix.lower()
        return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else _EXTENSIONS[kind]
