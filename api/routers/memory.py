from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import runtime
from core.runtime import Runtime

router = APIRouter(prefix="/api/memory", tags=["memory"])


class LongMemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    category: str | None = None
    importance: str = "medium"


@router.get("/short")
def short_memory(query: str = "", app: Runtime = Depends(runtime)) -> dict:
    return {"entries": app.short_memory.search(query) if query else app.short_memory.list()}


@router.delete("/short")
def clear_short_memory(app: Runtime = Depends(runtime)) -> dict:
    return {"deleted": app.short_memory.clear()}


@router.get("/long")
def long_memory(query: str = "", limit: int = 20, app: Runtime = Depends(runtime)) -> dict:
    entries = app.long_memory.search(query, min(limit, 200)) if query else app.long_memory.list_recent(min(limit, 200))
    return {"entries": entries}


@router.post("/long")
def save_long_memory(payload: LongMemoryCreate, app: Runtime = Depends(runtime)) -> dict:
    return app.long_memory.save(payload.text, payload.category, payload.importance, "explicit_user_action")
