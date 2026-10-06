from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field

from api.dependencies import runtime
from api.errors import unavailable
from core.runtime import Runtime

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatAttachment(BaseModel):
    name: str
    type: str
    mime: str | None = None
    content: str | None = None
    data_url: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str
    model_override: str | None = None
    mode: Literal["auto", "chat", "code", "deep"] = "auto"
    review_code: bool = False
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=5)


@router.post("/stream")
async def chat_stream(payload: ChatRequest, request: Request, app: Runtime = Depends(runtime)) -> StreamingResponse:
    if app.conversations.get_conversation(payload.conversation_id) is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    async def ndjson():
        stream = app.chat.stream_turn(
            payload.conversation_id,
            payload.message.strip(),
            model_override=payload.model_override,
            explicit_mode=payload.mode,
            review_code=payload.review_code,
            attachments=[attachment.model_dump() for attachment in payload.attachments],
        )
        try:
            async for event in stream:
                if await request.is_disconnected():
                    await stream.aclose()
                    return
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        finally:
            await stream.aclose()

    return StreamingResponse(
        ndjson(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

@router.post("")
async def chat(payload: ChatRequest, app: Runtime = Depends(runtime)) -> dict:
    try:
        return await app.chat.turn(
            payload.conversation_id,
            payload.message.strip(),
            model_override=payload.model_override,
            explicit_mode=payload.mode,
            attachments=[attachment.model_dump() for attachment in payload.attachments],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise unavailable("chat", str(exc)) from exc


