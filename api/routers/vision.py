from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import config
from api.dependencies import runtime
from api.errors import unavailable
from core.runtime import Runtime
from vision.qwen_vision_service import QwenVisionService

router = APIRouter(prefix="/api/vision", tags=["vision"])


class VisionPayload(BaseModel):
    image_base64: str = Field(min_length=1)
    prompt: str = Field(default="", max_length=2000)


@router.get("/status")
def status(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    model = app.roles.assignments()["vision"]
    installed = {item["name"] for item in catalog.get("models", [])}
    return {"model": model, "available": catalog["available"] and model in installed, "missing": model not in installed}


@router.post("")
def describe(payload: VisionPayload, app: Runtime = Depends(runtime)) -> dict:
    encoded = payload.image_base64.split(",", 1)[-1]
    service = QwenVisionService(
        config.OLLAMA_HOST,
        app.roles.assignments()["vision"],
        config.IMAGE_UNDERSTANDING_TIMEOUT_SECONDS,
        app.logger,
    )
    try:
        result = service.describe_image(encoded, payload.prompt)
    except Exception as exc:
        raise unavailable("vision", str(exc)) from exc
    result["text"] = result["text"][: config.IMAGE_UNDERSTANDING_MAX_OUTPUT_CHARS]
    return result
