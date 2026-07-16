from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app.models.game_state import GameState
from app.domain.priorities import PRIORITY_RANK
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
    bridge_status = await services.bridge_registry.status()
    capabilities = bridge_status.capabilities if state.source == "cet" else None
    event_clock = state.captured_at
    if event_clock.tzinfo is None:
        event_clock = event_clock.replace(tzinfo=timezone.utc)
    generated = services.synthesizer.synthesize(
        previous,
        state,
        changes,
        now=event_clock,
        capabilities=capabilities,
        observed_at=datetime.now(timezone.utc),
    )
    accepted = services.filter.process(generated)
    scene_state_changed = services.scene_builder.observe_state(state, capabilities)
    await services.bus.publish("state", services.state_payload(state))
    commands = []
    reactions = []
    for event in sorted(accepted, key=lambda item: PRIORITY_RANK[item.priority]):
        reaction, command = await services.publish_event(event, capabilities)
        if reaction:
            reactions.append(reaction)
        if command:
            commands.append(command)
    scene_state_changed = services.scene_builder.observe_state(state, capabilities) or scene_state_changed
    if scene_state_changed:
        scene = services.scene_builder.current()
        if scene is not None:
            await services.bus.publish("scene_context_updated", scene)
    await services.bus.publish("bridge_status", await services.bridge_registry.status())
    return {"accepted": True, "active": True, "active_source": active_source, "session_id": state.session_id, "sequence": state.sequence, "events_published": len(accepted), "reactions_created": len(reactions), "commands_created": len(commands)}


@router.get("/latest", response_model=GameState | None)
async def latest_state(request: Request) -> GameState | None:
    return await request.app.state.services.store.latest()
