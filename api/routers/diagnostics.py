from __future__ import annotations

from fastapi import APIRouter, Depends

import config
from api.dependencies import runtime
from core.runtime import Runtime

router = APIRouter(tags=["diagnostics"])


@router.get("/api/runtime/status")
def runtime_status(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    legacy = app.settings.current().get("inference_provider") == "ollama"
    return {
        "status": "ok",
        "inference": {**catalog.get("diagnostics", {}), "available": catalog["available"], "error": catalog["error"]},
        "llama_server": app.llama_cpp.diagnostics(),
        "ollama": {"available": catalog["available"] if legacy else None,
                   "error": catalog["error"] if legacy else None, "active": legacy},
        "registered_tools": [{"name": name} for name in app.registry.names()],
        "model_roles": app.roles.describe(catalog),
        "effective_settings": app.settings.current().as_dict(),
        "settings_revision": app.settings.current().revision,
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
    recent_model_calls = [
        event for event in app.trace.recent(500)
        if str(event.get("event", "")).startswith("model.")
    ][-50:]
    snapshot = app.settings.current()
    return {
        "inference": {**catalog.get("diagnostics", {}), "available": catalog["available"], "error": catalog["error"]},
        "llama_server": app.llama_cpp.diagnostics(),
        "ollama_available": catalog["available"] if snapshot.get("inference_provider") == "ollama" else None,
        "missing_models": [item for item in assignments if item["missing"]],
        "model_roles": assignments,
        "effective_settings": snapshot.as_dict(),
        "settings_revision": snapshot.revision,
        "recent_model_calls": recent_model_calls,
        "stt_available": app.stt.is_available(),
        "stt_error": app.stt.unavailable_reason(),
        "tts_available": app.tts.is_available(),
    }
