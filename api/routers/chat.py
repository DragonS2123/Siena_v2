from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=5)


@router.post("")
async def chat(payload: ChatRequest, app: Runtime = Depends(runtime)) -> dict:
    try:
        return await app.chat.turn(
            payload.conversation_id,
            payload.message.strip(),
            model_override=payload.model_override,
            attachments=[attachment.model_dump() for attachment in payload.attachments],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise unavailable("chat", str(exc)) from exc
