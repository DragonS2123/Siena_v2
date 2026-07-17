from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.models.voice import VoiceClip, VoicePlaybackEvent, VoiceStatus
from app.services.voice_dispatch import VoicePlaybackConflict

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


@router.get("/status", response_model=VoiceStatus)
async def voice_status(request: Request) -> VoiceStatus:
    return await request.app.state.services.voice_dispatch.status()


@router.get("/clips", response_model=list[VoiceClip])
async def voice_clips(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    session_id: str | None = None,
) -> list[VoiceClip]:
    return request.app.state.services.voice_dispatch.store.list(limit, session_id)


@router.get("/clips/latest", response_model=VoiceClip | None)
async def latest_voice_clip(request: Request, session_id: str | None = None) -> VoiceClip | None:
    clips = request.app.state.services.voice_dispatch.store.list(1, session_id)
    return clips[0] if clips else None


@router.get("/audio/{voice_clip_id}")
async def voice_audio(voice_clip_id: str, request: Request) -> Response:
    result = request.app.state.services.voice_dispatch.audio(voice_clip_id)
    if not result:
        raise HTTPException(status_code=404, detail="voice clip not found or no longer playable")
    clip, audio = result
    return Response(
        content=audio,
        media_type=clip.content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Siena-Voice-Clip": clip.voice_clip_id,
        },
    )


@router.post("/playback-events", response_model=VoiceClip)
async def playback_event(payload: VoicePlaybackEvent, request: Request) -> VoiceClip:
    try:
        return await request.app.state.services.voice_dispatch.playback_event(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="voice clip not found") from exc
    except VoicePlaybackConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
