"""Smoke-тест для the new experimental Phase 2 streaming endpoint —
POST /api/voice/tts/stream — against a REAL, already-running Siena backend
(same convention as scripts/test_qwen_ggml_vulkan.py). Unlike
scripts/probe_qwen_tts_streaming.py (which hits tts-server.exe's raw
/v1/audio/speech directly, in isolation), this script tests the Siena
backend endpoint itself: request validation, headers, trace events, and
honest error handling — not the upstream server's own behavior.

Requires: Siena backend running on http://127.0.0.1:8000
(start_backend.bat), config.VOICE_TTS_PROVIDER == "qwen3_tts_ggml_vulkan".

Run:
    python scripts/test_qwen_tts_stream_endpoint.py

Checks:
  A) Short Russian text ("Привет") — status 200, headers carry
     provider/format/sample-rate/channels, first chunk arrives quickly,
     total bytes > 0, output saved to storage/voice_probe/api_stream_test.pcm.
  B) 3 back-to-back repeats of A — all succeed, tts-server still reachable
     afterward (GET /api/voice/status).
  C) A medium-length Russian text — succeeds, bytes > 0.
  D) Empty text — honest 400/422, not a crash.
  E) Provider-unavailable path — only exercised if the backend is NOT
     currently configured for qwen3_tts_ggml_vulkan; otherwise honestly
     reported as SKIP (this script does not mutate config.py/restart the
     backend just to force that condition).
  F) Trace events for one successful request: tts_stream_requested →
     tts_stream_server_ready → tts_stream_started → tts_stream_first_chunk →
     tts_stream_completed, in order, via GET /api/trace/recent.

Never touches /api/voice/synthesize, useSpeech.ts, Speak/Stop/AutoSpeak, or
STT/OCR/vision/research/Insights/Settings/Runtime — only
/api/voice/tts/stream, /api/voice/status, and /api/trace/recent.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"
OUT_DIR = Path(__file__).resolve().parent.parent / "storage" / "voice_probe"

TEXT_SHORT = "Привет"
TEXT_MEDIUM = "Это тестовое сообщение средней длины для проверки endpoint потоковой генерации речи."

EXPECTED_HEADERS = {
    "x-siena-tts-provider": "qwen3_tts_ggml_vulkan",
    "x-siena-tts-format": "pcm",
    "x-siena-tts-sample-rate": "24000",
    "x-siena-tts-channels": "1",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stream_request(text: str, out_path: Path | None) -> dict:
    result = {
        "status_code": None,
        "headers": None,
        "first_chunk_ms": None,
        "total_bytes": 0,
        "elapsed_ms": None,
        "error": None,
        "body_text": None,
    }
    start = time.monotonic()
    first_chunk_time = None
    total_bytes = 0
    try:
        with requests.post(
            f"{BASE_URL}/api/voice/tts/stream",
            json={"text": text},
            stream=True,
            timeout=120,
        ) as response:
            result["status_code"] = response.status_code
            result["headers"] = dict(response.headers)
            if response.status_code != 200:
                result["body_text"] = response.text[:500]
            else:
                writer = open(out_path, "wb") if out_path is not None else None
                try:
                    for chunk in response.iter_content(chunk_size=4096):
                        if not chunk:
                            continue
                        if first_chunk_time is None:
                            first_chunk_time = time.monotonic() - start
                        total_bytes += len(chunk)
                        if writer is not None:
                            writer.write(chunk)
                finally:
                    if writer is not None:
                        writer.close()
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    result["first_chunk_ms"] = round(first_chunk_time * 1000) if first_chunk_time is not None else None
    result["total_bytes"] = total_bytes
    return result


def _get_trace(limit: int = 200) -> list[dict]:
    return requests.get(f"{BASE_URL}/api/trace/recent?limit={limit}", timeout=30).json()["events"]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Siena backend: {BASE_URL}")
    all_ok = True

    try:
        status = requests.get(f"{BASE_URL}/api/voice/status", timeout=10).json()
    except requests.RequestException as exc:
        print(f"ERROR: could not reach {BASE_URL}/api/voice/status: {exc}")
        print("Make sure the backend is running (start_backend.bat).")
        return 1

    provider = status.get("tts_provider")
    print(f"active tts_provider={provider!r}")
    provider_is_ggml_vulkan = provider == "qwen3_tts_ggml_vulkan"

    # --- A: short Russian text ---
    print(f"\n--- A: short text {TEXT_SHORT!r} ---")
    t0 = _now_iso()
    r = _stream_request(TEXT_SHORT, OUT_DIR / "api_stream_test.pcm")
    print(f"  status={r['status_code']} first_chunk_ms={r['first_chunk_ms']} "
          f"total_bytes={r['total_bytes']} elapsed_ms={r['elapsed_ms']} error={r['error']}")

    if provider_is_ggml_vulkan:
        if r["status_code"] != 200:
            print(f"  [FAIL] expected 200, got {r['status_code']} (body: {r['body_text']})")
            all_ok = False
        else:
            print("  [OK] status 200")
            headers_lower = {k.lower(): v for k, v in (r["headers"] or {}).items()}
            missing = [k for k, v in EXPECTED_HEADERS.items() if headers_lower.get(k) != v]
            if missing:
                print(f"  [FAIL] header mismatch/missing: {missing} (got {headers_lower})")
                all_ok = False
            else:
                print("  [OK] all X-Siena-TTS-* headers present and correct")
            if r["total_bytes"] > 0:
                print(f"  [OK] total_bytes > 0 ({r['total_bytes']})")
            else:
                print("  [FAIL] total_bytes == 0")
                all_ok = False
            if r["first_chunk_ms"] is not None and r["first_chunk_ms"] < 5000:
                print(f"  [OK] first chunk arrived quickly ({r['first_chunk_ms']}ms)")
            else:
                print(f"  [WARN] first chunk slow or missing ({r['first_chunk_ms']}ms) — inspect manually")
    else:
        print("  [SKIP] active provider isn't qwen3_tts_ggml_vulkan — see test E below")

    # --- B: 3 back-to-back repeats ---
    print("\n--- B: 3x consecutive requests ---")
    if provider_is_ggml_vulkan:
        for i in range(1, 4):
            r = _stream_request(TEXT_SHORT, OUT_DIR / f"api_stream_repeat_{i}.pcm")
            ok = r["status_code"] == 200 and r["total_bytes"] > 0
            print(f"  repeat {i}: status={r['status_code']} bytes={r['total_bytes']} "
                  f"elapsed_ms={r['elapsed_ms']} error={r['error']} -> {'OK' if ok else 'FAIL'}")
            all_ok = all_ok and ok
        try:
            status_after = requests.get(f"{BASE_URL}/api/voice/status", timeout=10).json()
            print(f"  tts-server reachable after repeats: tts_available={status_after.get('tts_available')}")
            if not status_after.get("tts_available"):
                print("  [FAIL] tts_available is false after repeated stream requests")
                all_ok = False
        except requests.RequestException as exc:
            print(f"  [FAIL] backend unreachable after repeats: {exc}")
            all_ok = False
    else:
        print("  [SKIP] provider not qwen3_tts_ggml_vulkan")

    # --- C: medium text ---
    print(f"\n--- C: medium text ({len(TEXT_MEDIUM)} chars) ---")
    if provider_is_ggml_vulkan:
        r = _stream_request(TEXT_MEDIUM, OUT_DIR / "api_stream_test_medium.pcm")
        ok = r["status_code"] == 200 and r["total_bytes"] > 0
        print(f"  status={r['status_code']} bytes={r['total_bytes']} elapsed_ms={r['elapsed_ms']} "
              f"error={r['error']} -> {'OK' if ok else 'FAIL'}")
        all_ok = all_ok and ok
    else:
        print("  [SKIP] provider not qwen3_tts_ggml_vulkan")

    # --- D: empty text ---
    print("\n--- D: empty text ---")
    try:
        resp = requests.post(f"{BASE_URL}/api/voice/tts/stream", json={"text": ""}, timeout=10)
        if resp.status_code in (400, 422):
            print(f"  [OK] empty text rejected honestly (status {resp.status_code})")
        else:
            print(f"  [FAIL] expected 400/422 for empty text, got {resp.status_code}: {resp.text[:200]}")
            all_ok = False
    except requests.RequestException as exc:
        print(f"  [FAIL] request errored instead of a clean 400/422: {exc}")
        all_ok = False

    # --- E: provider unavailable path ---
    print("\n--- E: provider-unavailable path ---")
    if not provider_is_ggml_vulkan:
        r = _stream_request(TEXT_SHORT, None)
        if r["status_code"] == 501:
            print("  [OK] non-ggml_vulkan provider honestly returns 501 (no fake stream)")
        else:
            print(f"  [FAIL] expected 501, got {r['status_code']}: {r['body_text']}")
            all_ok = False
    else:
        print(
            "  [SKIP] backend is currently configured for qwen3_tts_ggml_vulkan "
            "(config.VOICE_TTS_PROVIDER) — this script does not flip config/restart "
            "the backend just to force the unavailable-provider path. The 501 branch "
            "in api/server.py::voice_tts_stream was verified by code review instead."
        )

    # --- F: trace events for one successful request ---
    print("\n--- F: trace events (tts_stream_*) ---")
    if provider_is_ggml_vulkan:
        t_before = _now_iso()
        r = _stream_request(TEXT_SHORT, OUT_DIR / "api_stream_test_trace.pcm")
        time.sleep(0.3)  # let JSONL writes land
        events = _get_trace(limit=300)
        window = [e for e in events if (e.get("ts") or "") >= t_before and str(e.get("event", "")).startswith("tts_stream")]
        names = [e["event"] for e in window]
        print(f"  events since request: {names}")
        expected_order = [
            "tts_stream_requested",
            "tts_stream_server_ready",
            "tts_stream_started",
            "tts_stream_first_chunk",
            "tts_stream_completed",
        ]
        if r["status_code"] == 200:
            positions = []
            ok = True
            last_idx = -1
            for ev in expected_order:
                if ev not in names:
                    print(f"  [FAIL] missing expected event: {ev}")
                    ok = False
                    continue
                idx = names.index(ev)
                if idx <= last_idx:
                    print(f"  [FAIL] event {ev} out of order")
                    ok = False
                last_idx = idx
            if ok:
                print("  [OK] full tts_stream_* lifecycle present and in order")
            all_ok = all_ok and ok
        else:
            if "tts_stream_failed" in names:
                print("  [OK] tts_stream_failed logged for a failed request")
            else:
                print("  [FAIL] request failed but no tts_stream_failed event found")
                all_ok = False
    else:
        print("  [SKIP] provider not qwen3_tts_ggml_vulkan")

    print("\n=== ИТОГ ===")
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
