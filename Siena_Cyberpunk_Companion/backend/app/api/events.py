from fastapi import APIRouter, Query, Request

from app.models.game_event import EventSeverity, EventType, GameEvent

router = APIRouter(prefix="/api/v1/events")


@router.get("", response_model=list[GameEvent])
async def events(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    event_type: EventType | None = None,
    severity: EventSeverity | None = None,
    session_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> list[GameEvent]:
    return request.app.state.services.bus.events(limit, event_type, severity, session_id)


@router.get("/latest", response_model=GameEvent | None)
async def latest_event(
    request: Request,
    event_type: EventType | None = None,
    severity: EventSeverity | None = None,
    session_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> GameEvent | None:
    items = request.app.state.services.bus.events(1, event_type, severity, session_id)
    return items[0] if items else None
