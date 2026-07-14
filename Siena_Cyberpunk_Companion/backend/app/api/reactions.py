from fastapi import APIRouter, Query, Request

from app.models.game_event import EventType
from app.models.reaction import PlannerStatus, SienaReaction

router = APIRouter(prefix="/api/v1")


@router.get("/reactions", response_model=list[SienaReaction])
async def reactions(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    event_type: EventType | None = None,
) -> list[SienaReaction]:
    return request.app.state.services.bus.reactions(limit, event_type)


@router.get("/reactions/latest", response_model=SienaReaction | None)
async def latest_reaction(request: Request, event_type: EventType | None = None) -> SienaReaction | None:
    items = request.app.state.services.bus.reactions(1, event_type)
    return items[0] if items else None


@router.get("/planner/status", response_model=PlannerStatus)
async def planner_status(request: Request) -> PlannerStatus:
    return await request.app.state.services.reaction_planner.status()
