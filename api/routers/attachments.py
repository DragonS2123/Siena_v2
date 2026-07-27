from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

import config
from api.dependencies import runtime
from api.errors import not_found
from core.runtime import Runtime

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{attachment_id}")
def metadata(attachment_id: str, app: Runtime = Depends(runtime)) -> dict:
    attachment = app.conversations.get_attachment(attachment_id)
    if attachment is None:
        raise not_found("attachment")
    return attachment


@router.get("/{attachment_id}/content")
def content(attachment_id: str, app: Runtime = Depends(runtime)) -> FileResponse:
    attachment = app.conversations.get_attachment(attachment_id)
    if attachment is None:
        raise not_found("attachment")
    root = config.ATTACHMENTS_STORAGE_ROOT.resolve()
    candidate = (root / attachment["stored_relative_path"]).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise not_found("attachment")
    return FileResponse(
        candidate,
        media_type=attachment.get("mime_type") or "application/octet-stream",
        filename=Path(attachment["original_name"]).name,
    )
