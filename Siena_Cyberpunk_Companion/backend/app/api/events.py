from fastapi import APIRouter, Query, Request

from app.models.game_event import GameEvent

router = APIRouter(prefix="/api/v1/events")


@router.get("")
async def events(request: Request, limit: int = Query(default=200, ge=1, le=1000)) -> list[GameEvent]:
    return request.app.state.services.bus.events(limit)
