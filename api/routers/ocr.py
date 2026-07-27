from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config
from api.dependencies import runtime
from api.errors import unavailable
from core.runtime import Runtime
from ocr.glm_ocr_service import GlmOcrService, clean_ocr_text

router = APIRouter(prefix="/api/ocr", tags=["ocr"])


class ImagePayload(BaseModel):
    image_base64: str = Field(min_length=1)


@router.get("/status")
def status(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    model = app.roles.assignments()["ocr"]
    installed = {item["name"] for item in catalog.get("models", [])}
    return {"model": model, "available": catalog["available"] and model in installed, "missing": model not in installed}


@router.post("")
def extract(payload: ImagePayload, app: Runtime = Depends(runtime)) -> dict:
    encoded = payload.image_base64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid base64 image") from exc
    if len(raw) > config.MAX_IMAGE_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="image is too large")
    service = GlmOcrService(
        config.OLLAMA_HOST, app.roles.assignments()["ocr"], config.OCR_TIMEOUT_SECONDS, app.logger
    )
    try:
        result = service.extract_text(encoded)
    except Exception as exc:
        raise unavailable("ocr", str(exc)) from exc
    result["text"] = clean_ocr_text(result["text"])[: config.OCR_MAX_EXTRACTED_CHARS]
    return result
