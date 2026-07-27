from __future__ import annotations

from fastapi import APIRouter, Depends

import config
from api.dependencies import runtime
from core.runtime import Runtime

router = APIRouter(tags=["diagnostics"])


@router.get("/api/runtime/status")
def runtime_status(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    return {
        "status": "ok",
        "ollama": {"available": catalog["available"], "error": catalog["error"]},
        "registered_tools": [{"name": name} for name in app.registry.names()],
        "model_roles": app.roles.describe(catalog),
        "paths": {
            "conversations": str(config.CONVERSATIONS_DB_PATH),
            "attachments": str(config.ATTACHMENTS_STORAGE_ROOT),
        },
    }


@router.get("/api/tools")
def tools(app: Runtime = Depends(runtime)) -> dict:
    return {"tools": [{"name": name, "schema": schema} for name, schema in zip(app.registry.names(), app.registry.schemas())]}


@router.get("/api/diagnostics")
def diagnostics(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    assignments = app.roles.describe(catalog)
    return {
        "ollama_available": catalog["available"],
        "missing_models": [item for item in assignments if item["missing"]],
        "stt_available": app.stt.is_available(),
        "stt_error": app.stt.unavailable_reason(),
        "tts_available": app.tts.is_available(),
    }
