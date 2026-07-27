from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import runtime
from core.runtime import Runtime
from storage.settings_store import PERSISTABLE_FIELDS

router = APIRouter(prefix="/api/settings", tags=["settings"])

CLASSIFICATION = {
    "model_roles": "persisted",
    "max_context_messages": "persisted",
    "num_ctx": "restart-required",
    "num_predict": "restart-required",
    "request_timeout_seconds": "restart-required",
    "stt_language": "persisted",
    "tts_provider": "restart-required",
    "interface_language": "persisted",
    "appearance_theme": "persisted",
    "ui_font_size": "persisted",
    "ui_density": "persisted",
    "show_message_timestamps": "persisted",
    "show_typing_animation": "persisted",
    "startup_page": "persisted",
    "log_level": "persisted",
}


@router.get("")
def get_settings(app: Runtime = Depends(runtime)) -> dict:
    values, error = app.settings.load()
    values.setdefault("model_roles", app.roles.assignments())
    return {"values": values, "classification": CLASSIFICATION, "error": error}


@router.post("")
def update_settings(payload: dict[str, Any], app: Runtime = Depends(runtime)) -> dict:
    unknown = set(payload) - PERSISTABLE_FIELDS
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown settings: {sorted(unknown)}")
    if "model_roles" in payload:
        raise HTTPException(status_code=422, detail="assign models through /api/models/roles/{role}")
    values = app.settings.save(payload)
    return {"values": values, "classification": CLASSIFICATION}
