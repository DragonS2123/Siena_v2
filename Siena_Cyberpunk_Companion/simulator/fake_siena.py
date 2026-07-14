import argparse
import asyncio

import uvicorn
from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Fake Siena v0.4 test server")
MODE = "success"
DELAY = 0.0
CALLS = 0


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "Fake Siena"}


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
        "reasoning": "<think>internal</think> Держись, Ви.",
        "long": "Это очень длинная естественная реакция " * 30,
    }.get(MODE, "Осторожнее, Ви. Найди укрытие.")
    return {"text": text, "reasoning": "fake reasoning" if MODE == "reasoning" else None, "model": "fake-siena", "request_id": body["request_id"]}


def parse_args():
    parser = argparse.ArgumentParser(description="Local fake Siena server for v0.4 smoke tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--mode", choices=["success", "delay", "500", "401", "403", "malformed", "empty", "reasoning", "long", "recover"], default="success")
    parser.add_argument("--delay", type=float, default=0.0)
    return parser.parse_args()


if __name__ == "__main__":
    options = parse_args()
    MODE, DELAY = options.mode, options.delay
    uvicorn.run(app, host=options.host, port=options.port, access_log=False)
