from fastapi import APIRouter, Query, Request

from app.models.scene import SceneContext, SceneCurrentResponse, ScenePhase

router = APIRouter(prefix="/api/v1/scenes", tags=["scenes"])


@router.get("/current", response_model=SceneCurrentResponse)
async def current_scene(request: Request) -> SceneCurrentResponse:
    scene = request.app.state.services.scene_builder.current()
    return SceneCurrentResponse(active=scene is not None, scene=scene)


@router.get("", response_model=list[SceneContext])
async def scene_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    session_id: str | None = None,
    phase: ScenePhase | None = None,
) -> list[SceneContext]:
    return request.app.state.services.scene_builder.history(limit, session_id, phase)
