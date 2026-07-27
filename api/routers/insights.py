from fastapi import APIRouter, Depends

from api.dependencies import runtime
from api.errors import not_found
from core.runtime import Runtime
from tools.candidate_memory_tools import promote_candidate

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("")
def list_insights(status: str | None = None, limit: int = 50, app: Runtime = Depends(runtime)) -> dict:
    return {"items": app.candidates.list(status, min(limit, 200))}


@router.post("/{candidate_id}/promote")
def promote(candidate_id: int, app: Runtime = Depends(runtime)) -> dict:
    candidate = app.candidates.get(candidate_id)
    if candidate is None:
        raise not_found("insight")
    return promote_candidate(app.candidates, app.long_memory, candidate_id)


@router.post("/{candidate_id}/{action}")
def set_insight_status(candidate_id: int, action: str, app: Runtime = Depends(runtime)) -> dict:
    status = {"reject": "rejected", "later": "later"}.get(action)
    if status is None:
        raise not_found("action")
    result = app.candidates.set_status(candidate_id, status)
    if result is None:
        raise not_found("insight")
    return result


@router.delete("/{candidate_id}")
def delete_insight(candidate_id: int, app: Runtime = Depends(runtime)) -> dict:
    return {"deleted": app.candidates.delete(candidate_id)}
