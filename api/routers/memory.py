from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import runtime
from core.runtime import Runtime
from core.errors import SienaToolError
from memory.policy import SOURCES

router = APIRouter(prefix='/api/memory', tags=['memory'])


class LongMemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    category: str | None = None
    importance: str = 'medium'
    fact_key: str | None = None
    source: str = 'user'


@router.get('/short')
def short_memory(query: str = '', app: Runtime = Depends(runtime)) -> dict:
    return {'entries': app.short_memory.search(query) if query else app.short_memory.list()}


@router.delete('/short')
def clear_short_memory(app: Runtime = Depends(runtime)) -> dict:
    return {'deleted': app.short_memory.clear()}


@router.get('/long')
def long_memory(query: str = '', limit: int = 20, include_inactive: bool = False, app: Runtime = Depends(runtime)) -> dict:
    entries = app.long_memory.search(query, min(limit, 5)) if query else app.long_memory.list_recent(min(limit, 200), include_inactive=include_inactive)
    return {'entries': entries}


def _save(app, payload, replaces_id=None):
    if payload.source not in SOURCES:
        raise HTTPException(400, 'invalid provenance source')
    try:
        return app.long_memory.save(payload.text, payload.category, payload.importance, payload.source,
                                    {'confirmed_by_user': True}, key=payload.fact_key, replaces_id=replaces_id)
    except SienaToolError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/long')
def save_long_memory(payload: LongMemoryCreate, app: Runtime = Depends(runtime)) -> dict:
    return _save(app, payload)


@router.put('/long/{entry_id}')
def update_long_memory(entry_id: int, payload: LongMemoryCreate, app: Runtime = Depends(runtime)) -> dict:
    if app.long_memory.get(entry_id) is None: raise HTTPException(404, 'memory entry not found')
    return _save(app, payload, entry_id)


@router.post('/long/{entry_id}/deactivate')
def deactivate_long_memory(entry_id: int, app: Runtime = Depends(runtime)) -> dict:
    if app.long_memory.get(entry_id) is None: raise HTTPException(404, 'memory entry not found')
    app.long_memory.deactivate(entry_id)
    return {'id': entry_id, 'active': False}


@router.delete('/long/{entry_id}')
def delete_long_memory(entry_id: int, app: Runtime = Depends(runtime)) -> dict:
    if not app.long_memory.delete(entry_id): raise HTTPException(404, 'memory entry not found')
    return {'id': entry_id, 'deleted': True}
