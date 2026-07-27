from __future__ import annotations

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
    try:
        for event in app.trace.recent(100):
            await websocket.send_json(event)
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        app.trace.unsubscribe(queue)
