from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "siena-cyberpunk-observer", "version": "0.2.0"}


@router.get("/api/v1/status")
async def status(request: Request) -> dict:
    services = request.app.state.services
    observer_status = await services.status_payload()
    latest = await services.store.latest()
    return {
        "status": "ready",
        "backend": "online",
        "active_session": latest.session_id if latest else None,
        "last_sequence": latest.sequence if latest else None,
        "last_packet_at": latest.captured_at if latest else None,
        "event_count": len(services.bus.events(services.settings.event_buffer_size)),
        "scheduler": await services.scheduler.status(),
        "active_source": observer_status["active_source"],
        "bridge": observer_status["bridge"],
        "integrations": {"siena_core": "disabled", "cyberpunk_bridge": "simulator_only"},
    }
