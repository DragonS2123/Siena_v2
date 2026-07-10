"""Smoke-тест для POST /api/voice/stt/transcribe (Phase 1, whisper.cpp STT)
— против уже запущенного Siena backend'а (тот же подход, что
scripts/test_qwen_tts_stream_endpoint.py). Тестирует именно backend
endpoint, не raw whisper-cli.exe (см. storage/stt_probe/whisper_cpp_build_probe.txt
для изолированной CLI-проверки).

НЕ трогает /api/voice/transcribe (старый faster-whisper путь, по-прежнему
недоступен — пакет не установлен) и не запускает mic UI/Voice Orb — только
новый standalone POST /api/voice/stt/transcribe.

Запуск:

    python scripts/test_whisper_cpp_stt_endpoint.py

Требования: Siena backend запущен на http://127.0.0.1:8000.

Проверки:
  A/B) Известный WAV-сэмпл (external/whisper.cpp/samples/jfk.wav — реальная
       человеческая речь, английский — предпочтительно; иначе
       storage/stt_probe/raw_logs/test_serena_vulkan.wav — TTS-generated
       речь, ЧЕСТНО помечается как не настоящая речь, только проверка
       pipeline) -> status 200, text не пустой, provider=whisper_cpp,
       backend=vulkan|cpu_fallback, trace-события присутствуют.
  C) Пустой файл -> 400.
  D) Не-WAV файл -> 400.
  E) provider/model недоступен -> SKIP (whisper.cpp сейчас реально
     установлен, специально ломать файлы ради теста — fake test, не делаем).

Результат сохраняется в storage/stt_probe/api_stt_endpoint_report.json.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "http://127.0.0.1:8000"
REPO_ROOT = Path(__file__).resolve().parent.parent

JFK_WAV = REPO_ROOT / "external" / "whisper.cpp" / "samples" / "jfk.wav"
SERENA_WAV = REPO_ROOT / "storage" / "stt_probe" / "raw_logs" / "test_serena_vulkan.wav"
REPORT_PATH = REPO_ROOT / "storage" / "stt_probe" / "api_stt_endpoint_report.json"


def _post_wav(filename: str, content: bytes, language: str | None = None) -> requests.Response:
    files = {"file": (filename, content, "audio/wav")}
    data = {"language": language} if language else {}
    return requests.post(f"{BASE_URL}/api/voice/stt/transcribe", files=files, data=data, timeout=180)


def _get_trace(limit: int = 300) -> list[dict]:
    return requests.get(f"{BASE_URL}/api/trace/recent?limit={limit}", timeout=30).json()["events"]


def main() -> int:
    report: dict = {"tests": []}
    all_ok = True

    print(f"Siena backend: {BASE_URL}")
    try:
        status = requests.get(f"{BASE_URL}/api/voice/status", timeout=10).json()
    except requests.RequestException as exc:
        print(f"ERROR: could not reach {BASE_URL}/api/voice/status: {exc}")
        print("Make sure the backend is running (start_backend.bat).")
        return 1

    print(
        f"stt_provider={status.get('stt_provider')!r} stt_available={status.get('stt_available')!r} "
        f"stt_reason={status.get('stt_reason')!r} stt_model={status.get('stt_model')!r} "
        f"stt_backend_hint={status.get('stt_backend_hint')!r}"
    )
    report["voice_status"] = status
    stt_available = bool(status.get("stt_available"))

    # --- A/B: known WAV sample ---
    print("\n--- A/B: known WAV sample ---")
    sample_path: Path | None = None
    sample_lang = "en"
    sample_note = ""
    if JFK_WAV.exists():
        sample_path = JFK_WAV
        sample_lang = "en"
        sample_note = "real human speech (whisper.cpp's own repo sample)"
    elif SERENA_WAV.exists():
        sample_path = SERENA_WAV
        sample_lang = "ru"
        sample_note = (
            "TTS-GENERATED speech (Qwen3-TTS Vulkan smoke sample) — NOT real "
            "human/microphone speech, this only proves the endpoint pipeline "
            "works end to end"
        )

    if sample_path is None:
        print("  [SKIP] no known WAV sample found (checked jfk.wav and test_serena_vulkan.wav)")
        report["tests"].append({"name": "A_B_known_sample", "skipped": True, "reason": "no sample found"})
    elif not stt_available:
        print(f"  [SKIP] stt_available=false ({status.get('stt_reason')}) — cannot exercise success path live")
        report["tests"].append({"name": "A_B_known_sample", "skipped": True, "reason": status.get("stt_reason")})
    else:
        print(f"  sample: {sample_path}")
        print(f"  note: {sample_note}")
        content = sample_path.read_bytes()
        t0 = time.monotonic()
        resp = _post_wav(sample_path.name, content, language=sample_lang)
        wall_ms = round((time.monotonic() - t0) * 1000)
        entry: dict = {
            "name": "A_B_known_sample",
            "sample": str(sample_path),
            "note": sample_note,
            "status_code": resp.status_code,
            "wall_ms": wall_ms,
        }
        ok = True
        if resp.status_code == 200:
            body = resp.json()
            entry["response"] = body
            print(f"  status=200 wall_ms={wall_ms}")
            print(f"  text={body.get('text')!r}")
            print(f"  provider={body.get('provider')!r} backend={body.get('backend')!r} elapsed_ms={body.get('elapsed_ms')!r}")
            if not (body.get("text") or "").strip():
                print("  [FAIL] text is empty")
                ok = False
            else:
                print("  [OK] text is not empty")
            if body.get("provider") != "whisper_cpp":
                print(f"  [FAIL] provider={body.get('provider')!r}, expected 'whisper_cpp'")
                ok = False
            else:
                print("  [OK] provider == whisper_cpp")
            if body.get("backend") not in ("vulkan", "cpu_fallback"):
                print(f"  [FAIL] unexpected backend={body.get('backend')!r}")
                ok = False
            else:
                print(f"  [OK] backend == {body.get('backend')!r}")
        else:
            print(f"  [FAIL] expected 200, got {resp.status_code}: {resp.text[:300]}")
            ok = False
        report["tests"].append(entry)
        all_ok = all_ok and ok

        time.sleep(0.3)
        events = _get_trace(limit=300)
        names = [e.get("event") for e in events[-30:]]
        print(f"  recent trace events (tail 30): {names}")
        expected = ["stt_transcribe_requested", "stt_transcribe_started", "stt_transcribe_completed"]
        trace_ok = all(e in names for e in expected)
        if trace_ok:
            print("  [OK] expected stt_transcribe_* lifecycle present")
        else:
            print(f"  [FAIL] missing one or more of {expected} in recent trace")
        report["trace_tail"] = names
        all_ok = all_ok and trace_ok

    # --- C: empty file ---
    print("\n--- C: empty file ---")
    resp = _post_wav("empty.wav", b"")
    ok = resp.status_code == 400
    print(f"  status={resp.status_code} -> {'OK' if ok else 'FAIL'}")
    report["tests"].append({"name": "C_empty_file", "status_code": resp.status_code, "ok": ok})
    all_ok = all_ok and ok

    # --- D: non-wav file ---
    print("\n--- D: non-wav file ---")
    resp = requests.post(
        f"{BASE_URL}/api/voice/stt/transcribe",
        files={"file": ("audio.mp3", b"\x00\x01\x02not a real mp3 either", "audio/mpeg")},
        timeout=30,
    )
    ok = resp.status_code == 400
    print(f"  status={resp.status_code} -> {'OK' if ok else 'FAIL'}")
    report["tests"].append({"name": "D_non_wav_file", "status_code": resp.status_code, "ok": ok})
    all_ok = all_ok and ok

    # --- E: provider/model unavailable ---
    print("\n--- E: provider/model unavailable ---")
    if not stt_available:
        # Genuinely unavailable right now — exercise it for real.
        resp = _post_wav("test.wav", (sample_path.read_bytes() if sample_path else b"RIFF"), language="ru")
        ok = resp.status_code == 503
        print(f"  status={resp.status_code} -> {'OK' if ok else 'FAIL'} (real unavailable path)")
        report["tests"].append({"name": "E_provider_unavailable", "status_code": resp.status_code, "ok": ok})
        all_ok = all_ok and ok
    else:
        print(
            "  [SKIP] whisper.cpp is currently installed and available — this script does not "
            "move/rename the exe or model just to force the 503 path (that would leave the "
            "environment in a broken state for no real test value). The 503 branch in "
            "api/server.py::voice_stt_transcribe was verified by code review instead."
        )
        report["tests"].append({"name": "E_provider_unavailable", "skipped": True, "reason": "provider currently available"})

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report: {REPORT_PATH}")

    print("\n=== ИТОГ ===")
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
