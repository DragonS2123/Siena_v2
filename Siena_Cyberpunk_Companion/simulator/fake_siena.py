import argparse
import asyncio
import io
import wave

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="Fake Siena v0.7 test server")
MODE = "success"
DELAY = 0.0
CALLS = 0
TTS_CALLS = 0


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "Fake Siena"}


@app.get("/api/voice/status")
async def voice_status():
    return {
        "tts_available": MODE not in {"tts_unavailable"},
        "tts_provider": "fake_tts",
        "tts_voice": "serena",
        "tts_language": "ru",
    }


def fake_wav(seconds: float = 0.05) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * max(1, round(24000 * seconds)))
    return buffer.getvalue()


@app.post("/api/external/speech")
async def speech(request: Request):
    global TTS_CALLS
    TTS_CALLS += 1
    body = await request.json()
    if DELAY:
        await asyncio.sleep(DELAY)
    if MODE == "500" or (MODE == "recover" and TTS_CALLS <= 3):
        raise HTTPException(status_code=500, detail="fake TTS failure")
    if MODE in {"401", "403"}:
        raise HTTPException(status_code=int(MODE), detail="fake TTS auth failure")
    if MODE == "tts_wrong_content":
        return Response(b"not audio", media_type="text/plain")
    if MODE == "tts_empty":
        return Response(b"", media_type="audio/wav")
    if MODE == "tts_malformed":
        return Response(b"RIFF broken WAVE", media_type="audio/wav")
    if MODE == "tts_oversized":
        return Response(fake_wav(400), media_type="audio/wav")
    return Response(
        fake_wav(),
        media_type="audio/wav",
        headers={
            "X-Siena-TTS-Provider": "fake_tts",
            "X-Siena-TTS-Speaker": body.get("speaker") or "serena",
            "X-Siena-TTS-Language": body.get("language", "ru"),
        },
    )


@app.post("/api/external/game-reaction")
async def reaction(request: Request):
    global CALLS
    CALLS += 1
    body = await request.json()
    if DELAY:
        await asyncio.sleep(DELAY)
    if MODE == "500" or (MODE == "recover" and CALLS <= 3):
        raise HTTPException(status_code=500, detail="fake failure")
    if MODE in {"401", "403"}:
        raise HTTPException(status_code=int(MODE), detail="fake auth failure")
    if MODE == "malformed":
        return {"unexpected": True}
    text = {
        "empty": "",
        "reasoning": "<think>internal</think> Держись.",
        "long": "Это очень длинная естественная реакция " * 30,
        "unicode": "«Здоровье 8%. Осторожно: шанс выжить — 20%.»\nНайди укрытие.",
    }.get(MODE, "Здоровье критическое. Найди укрытие.")
    return {"text": text, "reasoning": "fake reasoning" if MODE == "reasoning" else None, "model": "fake-siena", "request_id": body["request_id"]}


def parse_args():
    parser = argparse.ArgumentParser(description="Local fake Siena server for v0.7 smoke tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--mode", choices=["success", "delay", "500", "401", "403", "malformed", "empty", "reasoning", "long", "unicode", "recover", "tts_unavailable", "tts_wrong_content", "tts_empty", "tts_malformed", "tts_oversized"], default="success")
    parser.add_argument("--delay", type=float, default=0.0)
    return parser.parse_args()


if __name__ == "__main__":
    options = parse_args()
    MODE, DELAY = options.mode, options.delay
    uvicorn.run(app, host=options.host, port=options.port, access_log=False)
