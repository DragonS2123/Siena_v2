from fastapi import APIRouter, HTTPException, Request, status

from app.models.game_state import GameState
from app.services.bridge_registry import BridgeUnavailable
from app.services.state_store import SequenceConflict

router = APIRouter(prefix="/api/v1/telemetry")


@router.post("/state", status_code=status.HTTP_202_ACCEPTED)
async def ingest_state(state: GameState, request: Request) -> dict:
    services = request.app.state.services
    if state.source == "cet":
        try:
            await services.bridge_registry.authorize_cet(state.bridge_version)
        except BridgeUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        previous = await services.store.accept(state)
    except SequenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await services.bridge_registry.telemetry(state)
    active_source = await services.bridge_registry.select_source()
    source_changed = await services.store.activate(active_source)
    if state.source != active_source:
        bridge_status = await services.bridge_registry.status()
        await services.bus.publish("bridge_status", bridge_status)
        return {"accepted": True, "active": False, "active_source": active_source, "session_id": state.session_id, "sequence": state.sequence, "events_published": 0, "commands_created": 0}

    if source_changed:
        previous = None
    changes = services.diff(previous, state)
    generated = services.synthesizer.synthesize(previous, state, changes)
    accepted = services.filter.process(generated)
    await services.bus.publish("state", services.state_payload(state))
    commands = []
    for event in accepted:
        await services.bus.publish("event", event)
        command = await services.scheduler.consider(event)
        if command:
            commands.append(command)
            await services.bus.publish("command", command)
    await services.bus.publish("bridge_status", await services.bridge_registry.status())
    return {"accepted": True, "active": True, "active_source": active_source, "session_id": state.session_id, "sequence": state.sequence, "events_published": len(accepted), "commands_created": len(commands)}


@router.get("/latest", response_model=GameState | None)
async def latest_state(request: Request) -> GameState | None:
    return await request.app.state.services.store.latest()
