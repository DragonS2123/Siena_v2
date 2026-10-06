from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import runtime
from core.runtime import Runtime
from core.runtime_settings import (
    ALIASES,
    DEFAULTS,
    LIVE_FIELDS,
    RESTART_REQUIRED_FIELDS,
    SettingsValidationError,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _classification() -> dict[str, str]:
    result = {
        key: "restart-required" if key in RESTART_REQUIRED_FIELDS else "live"
        for key in DEFAULTS
        if key != "settings_revision"
    }
    for alias, canonical in ALIASES.items():
        result[alias] = result[canonical]
    return result


@router.get("")
def get_settings(app: Runtime = Depends(runtime)) -> dict:
    snapshot = app.settings.current()
    return {
        "values": snapshot.as_dict(),
        "settings_revision": snapshot.revision,
        "classification": _classification(),
        "applied": sorted(LIVE_FIELDS),
        "restart_required": [],
        "errors": [],
    }


@router.post("")
def update_settings(payload: dict[str, Any], app: Runtime = Depends(runtime)) -> dict:
    if "settings_revision" in payload:
        raise HTTPException(status_code=422, detail="settings_revision is read-only")
    if "model_roles" in payload:
        raise HTTPException(status_code=422, detail="assign models through /api/models/roles/{role}")
    try:
        result = app.settings.update(payload)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        app.logger.event("settings.persistence_failed", error=str(exc)[:300])
        raise HTTPException(status_code=500, detail=f"settings persistence failed: {exc}") from exc
    return {**result.as_dict(), "classification": _classification()}
