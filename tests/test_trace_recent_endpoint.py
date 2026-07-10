"""GET /api/trace/recent — Debug page (0.2.0 release readiness) needs a small
allowlist extension: voice_synthesize_start/result and candidate_memory_*
events already existed and were already logged, just never surfaced through
this endpoint before. Covers only the allowlist change, not the whole
endpoint (unrelated event filtering is exercised elsewhere already)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402


def _write_log(tmp_path: Path, events: list[dict]) -> None:
    log_path = tmp_path / "siena_20260101.jsonl"
    log_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")


def test_trace_recent_includes_voice_synthesize_events(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "LOG_DIR", tmp_path)
    _write_log(tmp_path, [
        {"ts": "2026-01-01T00:00:00+00:00", "event": "voice_synthesize_start", "provider": "qwen3_tts_ggml_vulkan"},
        {"ts": "2026-01-01T00:00:01+00:00", "event": "voice_synthesize_result", "duration_ms": 500},
        {"ts": "2026-01-01T00:00:02+00:00", "event": "some_unrelated_event"},
    ])

    response = TestClient(server.app).get("/api/trace/recent?limit=50")
    assert response.status_code == 200
    events = [e["event"] for e in response.json()["events"]]
    assert "voice_synthesize_start" in events
    assert "voice_synthesize_result" in events
    assert "some_unrelated_event" not in events


def test_trace_recent_includes_candidate_memory_events(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "LOG_DIR", tmp_path)
    _write_log(tmp_path, [
        {"ts": "2026-01-01T00:00:00+00:00", "event": "candidate_memory_created", "id": 1},
        {"ts": "2026-01-01T00:00:01+00:00", "event": "candidate_memory_promoted", "candidate_id": 1},
        {"ts": "2026-01-01T00:00:02+00:00", "event": "candidate_memory_rejected", "candidate_id": 2},
        {"ts": "2026-01-01T00:00:03+00:00", "event": "candidate_memory_deferred", "candidate_id": 3},
        {"ts": "2026-01-01T00:00:04+00:00", "event": "candidate_memory_deleted", "candidate_id": 4},
    ])

    response = TestClient(server.app).get("/api/trace/recent?limit=50")
    assert response.status_code == 200
    events = {e["event"] for e in response.json()["events"]}
    assert events == {
        "candidate_memory_created",
        "candidate_memory_promoted",
        "candidate_memory_rejected",
        "candidate_memory_deferred",
        "candidate_memory_deleted",
    }
