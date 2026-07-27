from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import runtime
from api.errors import not_found
from core.runtime import Runtime

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    model_override: str | None = None


@router.get("")
def list_conversations(limit: int = 50, app: Runtime = Depends(runtime)) -> dict:
    return {"conversations": app.conversations.list_conversations(min(max(limit, 1), 200))}


@router.post("")
def create_conversation(payload: ConversationCreate, app: Runtime = Depends(runtime)) -> dict:
    conversation_id = app.conversations.create_conversation(payload.title)
    return {"conversation_id": conversation_id, "conversation": app.conversations.get_conversation(conversation_id)}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, app: Runtime = Depends(runtime)) -> dict:
    conversation = app.conversations.get_conversation(conversation_id)
    if conversation is None:
        raise not_found("conversation")
    return conversation


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: str, payload: ConversationUpdate, app: Runtime = Depends(runtime)
) -> dict:
    if app.conversations.get_conversation(conversation_id) is None:
        raise not_found("conversation")
    if payload.title is not None:
        app.conversations.update_title(conversation_id, payload.title.strip() or "New Chat")
    if "model_override" in payload.model_fields_set:
        if payload.model_override is not None:
            installed = {item["name"] for item in app.catalog.refresh().get("models", [])}
            if payload.model_override not in installed:
                raise HTTPException(status_code=422, detail="model is not installed")
        app.conversations.update_model_override(conversation_id, payload.model_override)
    return app.conversations.get_conversation(conversation_id)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, app: Runtime = Depends(runtime)) -> dict:
    if app.conversations.get_conversation(conversation_id) is None:
        raise not_found("conversation")
    app.conversations.delete_conversation(conversation_id)
    return {"deleted": True}
