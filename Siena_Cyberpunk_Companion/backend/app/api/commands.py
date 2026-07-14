from datetime import datetime, timezone

from fastapi import APIRouter, Request, status

from app.models.companion_command import CompanionCommand

router = APIRouter(prefix="/api/v1/commands")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_command(command: CompanionCommand, request: Request) -> CompanionCommand:
    if command.source == "mock_scheduler":
        command = command.model_copy(update={"source": "manual"})
    stored = await request.app.state.services.scheduler.set_manual(command)
    await request.app.state.services.bus.publish("command", stored)
    return stored


@router.get("/current", response_model=CompanionCommand | None)
async def current_command(request: Request) -> CompanionCommand | None:
    return await request.app.state.services.scheduler.current(datetime.now(timezone.utc))
