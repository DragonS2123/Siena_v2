from fastapi import APIRouter, HTTPException, Request, Response, status

from app.models.embodiment import EmbodimentSettings
from app.models.npc import NpcCommand, NpcCommandAck, NpcCommandName, NpcCommandRequest, NpcCommandResult, NpcStatus
from app.services.npc_command_queue import NpcControllerDisabledError, NpcQueueFullError, NpcUnknownClaimError

router = APIRouter(prefix="/api/v1/npc", tags=["npc"])


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="NPC controller API is loopback-only")


@router.post("/commands", response_model=NpcCommand, status_code=status.HTTP_201_CREATED)
async def enqueue_npc_command(value: NpcCommandRequest, request: Request, response: Response) -> NpcCommand:
    require_loopback(request)
    try:
        item, coalesced = await request.app.state.services.embodiment.player_command(NpcCommandName(value.command.value))
    except NpcControllerDisabledError as exc:
        raise HTTPException(status_code=503, detail="NPC controller is disabled") from exc
    except NpcQueueFullError as exc:
        raise HTTPException(status_code=429, detail="NPC command queue is full") from exc
    if coalesced: response.status_code = status.HTTP_200_OK
    return item


@router.get("/commands/next", response_model=NpcCommand, responses={204: {"description": "No pending command"}})
async def next_npc_command(request: Request) -> NpcCommand | Response:
    require_loopback(request)
    item = await request.app.state.services.npc_commands.claim_next()
    return item if item is not None else Response(status_code=204)


@router.post("/commands/{command_id}/result", response_model=NpcCommandAck)
async def acknowledge_npc_command(command_id: str, value: NpcCommandResult, request: Request) -> NpcCommandAck:
    require_loopback(request)
    try:
        await request.app.state.services.npc_commands.acknowledge(command_id, value)
    except NpcUnknownClaimError as exc:
        raise HTTPException(status_code=404, detail="Unknown or already acknowledged command") from exc
    return NpcCommandAck(command_id=command_id, acknowledged=True)


@router.get("/status", response_model=NpcStatus)
async def npc_status(request: Request) -> NpcStatus:
    require_loopback(request)
    return await request.app.state.services.embodiment.status()


@router.get("/settings", response_model=EmbodimentSettings)
async def embodiment_settings(request: Request) -> EmbodimentSettings:
    require_loopback(request)
    return request.app.state.services.embodiment.settings.model_copy(deep=True)


@router.put("/settings", response_model=EmbodimentSettings)
async def update_embodiment_settings(value: EmbodimentSettings, request: Request) -> EmbodimentSettings:
    require_loopback(request)
    return await request.app.state.services.embodiment.update_settings(value)
