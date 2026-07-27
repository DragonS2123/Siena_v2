from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import config
from api.dependencies import runtime
from api.errors import not_found, unavailable
from core.runtime import Runtime

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = None


@router.get("/status")
def status(app: Runtime = Depends(runtime)) -> dict:
    return {
        "stt": {
            "provider": app.stt.PROVIDER_NAME,
            "available": app.stt.is_available(),
            "reason": app.stt.unavailable_reason(),
            "model": app.stt.model_path.name,
        },
        "tts": {
            "provider": app.tts.PROVIDER_NAME,
            "available": app.tts.is_available(),
            "voice": app.tts.voice,
            "language": app.tts.language,
        },
    }


@router.post("/stt/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    app: Runtime = Depends(runtime),
) -> dict:
    raw = await file.read()
    if not raw or len(raw) > config.WHISPER_CPP_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="invalid upload size")
    if Path(file.filename or "").suffix.lower() != ".wav":
        raise HTTPException(status_code=400, detail="only WAV audio is accepted")
    temp_dir = config.BASE_DIR / "storage" / "voice_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".wav", delete=False)
    try:
        temporary.write(raw)
        temporary.close()
        try:
            with wave.open(temporary.name, "rb") as stream:
                duration = stream.getnframes() / max(stream.getframerate(), 1)
        except (wave.Error, EOFError) as exc:
            raise HTTPException(status_code=400, detail="invalid WAV") from exc
        if duration > config.WHISPER_CPP_MAX_AUDIO_SECONDS:
            raise HTTPException(status_code=400, detail="audio is too long")
        try:
            result = await asyncio.to_thread(
                app.stt.transcribe_wav, temporary.name, language or config.WHISPER_CPP_LANGUAGE
            )
        except Exception as exc:
            raise unavailable("stt", str(exc)) from exc
        return {**result, "confidence": None}
    finally:
        Path(temporary.name).unlink(missing_ok=True)


@router.post("/synthesize")
async def synthesize(payload: SynthesisRequest, app: Runtime = Depends(runtime)) -> dict:
    try:
        result = await asyncio.to_thread(app.tts.synthesize_to_file, payload.text, payload.voice)
    except Exception as exc:
        raise unavailable("tts", str(exc)) from exc
    filename = Path(result["audio_path"]).name
    return {**result, "audio_url": f"/api/voice/audio/{filename}"}


@router.get("/audio/{filename}")
def audio(filename: str) -> FileResponse:
    if filename != Path(filename).name:
        raise not_found("audio")
    root = config.TTS_OUTPUT_DIR.resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise not_found("audio")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@router.get("/profiles")
def profiles(app: Runtime = Depends(runtime)) -> dict:
    return {
        "profiles": [profile.to_dict() for profile in app.voice_profiles.list_profiles()],
        "active_profile_id": app.voice_profiles.get_active_profile().id,
    }


@router.post("/profiles/active/{profile_id}")
def activate_profile(profile_id: str, app: Runtime = Depends(runtime)) -> dict:
    try:
        return app.voice_profiles.set_active_profile(profile_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
