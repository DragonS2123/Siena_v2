import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    services = websocket.app.state.services
    queue = await services.bus.connect()
    try:
        latest = await services.store.latest()
        await websocket.send_json({"type": "status", "data": await services.status_payload()})
        if latest:
            await websocket.send_json({"type": "state", "data": services.state_payload(latest)})
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                message = {"type": "status", "data": await services.status_payload()}
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await services.bus.disconnect(queue)
