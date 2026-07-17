from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.models.presence import InGamePresenceSnapshot, InGamePresenceStatus

router = APIRouter(prefix="/api/v1/in-game-presence", tags=["in-game-presence"])


@router.get("/current", response_model=InGamePresenceSnapshot, responses={204: {"description": "No newer revision"}})
async def current_presence(
    request: Request,
    after_revision: int | None = Query(default=None, ge=0),
) -> InGamePresenceSnapshot | Response:
    services = request.app.state.services
    snapshot, status_update = services.presence.record_poll(after_revision)
    if status_update:
        await services.bus.publish("in_game_presence_status", status_update)
    if snapshot is None:
        return Response(status_code=204)
    remaining_ms = 0
    if snapshot.expires_at:
        remaining_ms = max(0, int((snapshot.expires_at - services.presence.clock()).total_seconds() * 1000))
    return JSONResponse(
        content=snapshot.model_dump(mode="json"),
        headers={"X-Siena-Presence-Remaining-Ms": str(remaining_ms), "Cache-Control": "no-store"},
    )


@router.get("/status", response_model=InGamePresenceStatus)
async def presence_status(request: Request) -> InGamePresenceStatus:
    return request.app.state.services.presence.status()
