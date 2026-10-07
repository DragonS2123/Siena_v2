from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from api.dependencies import runtime
from core.runtime import Runtime

router = APIRouter(tags=["trace"])


@router.get("/api/trace/recent")
def recent_trace(limit: int = 100, app: Runtime = Depends(runtime)) -> dict:
    return {"events": app.trace.recent(limit)}


@router.post("/api/trace/client-event")
def client_event(payload: dict, app: Runtime = Depends(runtime)) -> dict:
    safe = {
        "event": "client_event",
        "type": str(payload.get("type") or "unknown")[:80],
        "detail": str(payload.get("detail") or "")[:200],
    }
    app.trace.publish(safe)
    return {"accepted": True}


@router.websocket("/ws/trace")
async def trace_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    app: Runtime = websocket.app.state.runtime
    queue = app.trace.subscribe()
    async def wait_disconnect():
        while (await websocket.receive())['type'] != 'websocket.disconnect':
            pass
    disconnected = asyncio.create_task(wait_disconnect())
    next_event = None
    try:
        for event in app.trace.recent(100):
            await websocket.send_json(event)
        while True:
            # A quiet trace queue must not hide renderer disconnect from Uvicorn.
            next_event = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait((next_event, disconnected), return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                break
            await websocket.send_json(next_event.result())
    except WebSocketDisconnect:
        pass
    finally:
        tasks = [task for task in (next_event, disconnected) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        app.trace.unsubscribe(queue)
