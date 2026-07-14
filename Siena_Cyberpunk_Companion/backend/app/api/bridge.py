from fastapi import APIRouter, HTTPException, Request

from app.models.bridge import BridgeDisconnect, BridgeHeartbeat, BridgeHello, BridgeStatus
from app.services.bridge_registry import BridgeUnavailable, ProtocolMismatch

router = APIRouter(prefix="/api/v1/bridge")


async def publish_status(request: Request) -> BridgeStatus:
    services = request.app.state.services
    bridge_status = await services.bridge_registry.status()
    await services.store.activate(bridge_status.active_source)
    await services.bus.publish("bridge_status", bridge_status)
    return bridge_status


@router.post("/hello")
async def bridge_hello(hello: BridgeHello, request: Request) -> BridgeStatus:
    try:
        await request.app.state.services.bridge_registry.hello(hello)
    except ProtocolMismatch as exc:
        await publish_status(request)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await publish_status(request)


@router.post("/heartbeat")
async def bridge_heartbeat(heartbeat: BridgeHeartbeat, request: Request) -> BridgeStatus:
    try:
        await request.app.state.services.bridge_registry.heartbeat(heartbeat)
    except BridgeUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await publish_status(request)


@router.get("/status")
async def bridge_status(request: Request) -> BridgeStatus:
    return await publish_status(request)


@router.post("/disconnect")
async def bridge_disconnect(disconnect: BridgeDisconnect, request: Request) -> BridgeStatus:
    try:
        await request.app.state.services.bridge_registry.disconnect(disconnect.bridge_id, disconnect.reason)
    except BridgeUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await publish_status(request)
