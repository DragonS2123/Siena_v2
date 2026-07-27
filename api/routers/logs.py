from __future__ import annotations

import json

from fastapi import APIRouter

import config

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/recent")
def recent_logs(limit: int = 100) -> dict:
    files = sorted(config.LOG_DIR.glob("siena_*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not files:
        return {"events": []}
    lines = files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    for line in lines[-max(1, min(limit, 500)):]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"events": events}
