"""Isolated investigation script — does qwentts.cpp's tts-server.exe reliably
serve streaming PCM/WAV over its raw OpenAI-compatible HTTP API
(POST /v1/audio/speech)?

Context (HANDOFF_v2.md): the current, stable, shipped TTS path is entirely
non-streaming — voice/qwen_tts_ggml_vulkan.py::QwenTTSGgmlVulkanProvider
always requests response_format="wav" without stream=True and writes the
whole response body to a file (synthesize_to_file()); a prior *manual* probe
against response_format="pcm" on a short Russian phrase ("Привет") crashed
the tts-server.exe subprocess outright. Streaming TTS (a Phase 2 UI feature)
must not be built on top of that raw path until it's been shown to survive
repeated real requests.

This script is intentionally standalone: it does NOT import
voice/qwen_tts_ggml_vulkan.py, does NOT call /api/voice/synthesize or any
api/server.py endpoint, does NOT add a new endpoint, and never touches the
frontend. It only reads path/host/port constants from config.py (read-only)
and drives tts-server.exe directly over HTTP with `requests`, exactly the
way a hypothetical future streaming client would have to. If tts-server
crashes, this script is meant to catch that plainly, not hide it.

Usage:

    python scripts/probe_qwen_tts_streaming.py

Requires: external/qwentts.cpp/build/Release/tts-server.exe and both GGUF
models present (same paths config.py already points production code at).
If a tts-server.exe is already running on config.QWEN_TTS_SERVER_URL, this
script uses it as-is (never restarts something it doesn't own) unless a
crash is detected mid-probe, in which case it starts its own instance to
test recovery.

Output:
    storage/voice_probe/stream_test.wav        — response_format=wav, stream=True (last successful text)
    storage/voice_probe/stream_test.pcm        — response_format=pcm, stream=True (last successful text)
    storage/voice_probe/probe_wav_nonstream_*.wav
    storage/voice_probe/probe_pcm_repeat_*.pcm — the 3 back-to-back PCM calls (test D)
    storage/voice_probe/server_stdout_<run>.log / server_stderr_<run>.log — captured
        subprocess output, ONLY for instances this script itself started
        (an externally-running server's output isn't ours to redirect)
    storage/voice_probe/probe_report.json      — full structured results
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402 — read-only: paths/host/port/timeout constants only

SERVER_URL = config.QWEN_TTS_SERVER_URL.rstrip("/")
_parsed = urlparse(SERVER_URL)
HOST = _parsed.hostname or "127.0.0.1"
PORT = _parsed.port or 8080

OUT_DIR = config.BASE_DIR / "storage" / "voice_probe"
STARTUP_TIMEOUT_SEC = 30
CHUNK_SIZE = 4096

# The exact short Russian phrase that crashed tts-server.exe in a prior manual
# probe (see HANDOFF_v2.md) — deliberately re-tested here as TEXT_SHORT so
# this script directly retests the known crash condition, not just "some
# short text".
TEXTS = {
    "short": "Привет",
    "medium": "Это тестовое сообщение средней длины для проверки стабильности потокового синтеза речи.",
    "russian": "Максим попросил проверить, может ли qwentts стабильно отдавать потоковый звук на русском языке без сбоев.",
}


def _is_port_open(timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _tail_file(path: Path, n: int = 60) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return [f"<failed to read {path.name}: {exc}>"]
    lines = text.splitlines()
    return lines[-n:]


class ServerHandle:
    """Tracks whichever tts-server.exe instance this script is currently
    responsible for (if any) — separate from one already running that we
    found and are just using as-is."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.externally_managed = False
        self.run_label = "run1"
        self.stdout_path: Path | None = None
        self.stderr_path: Path | None = None
        self._stdout_f = None
        self._stderr_f = None

    def ensure_running(self, run_label: str) -> tuple[bool, str]:
        """Returns (ok, detail). If the server is already reachable, uses it
        untouched. Otherwise starts a fresh tts-server.exe instance owned by
        this script, logging stdout/stderr to files tagged with run_label so
        multiple starts within one probe run don't overwrite each other's
        evidence."""
        if _is_port_open():
            self.externally_managed = True
            self.process = None
            return True, "already reachable (externally managed, left as-is)"

        exe, model, codec = config.QWEN_TTS_EXE, config.QWEN_TTS_MODEL_PATH, config.QWEN_TTS_CODEC_PATH
        for label, path in (("tts-server.exe", exe), ("talker GGUF", model), ("codec GGUF", codec)):
            if not path.exists():
                return False, f"{label} not found: {path}"

        self.run_label = run_label
        self.stdout_path = OUT_DIR / f"server_stdout_{run_label}.log"
        self.stderr_path = OUT_DIR / f"server_stderr_{run_label}.log"
        self._stdout_f = open(self.stdout_path, "wb")
        self._stderr_f = open(self.stderr_path, "wb")

        try:
            self.process = subprocess.Popen(
                [
                    str(exe),
                    "--model", str(model),
                    "--codec", str(codec),
                    "--host", HOST,
                    "--port", str(PORT),
                    "--lang", config.QWEN_TTS_DEFAULT_LANGUAGE,
                ],
                cwd=str(exe.parent),
                stdout=self._stdout_f,
                stderr=self._stderr_f,
            )
        except OSError as exc:
            return False, f"failed to launch tts-server.exe: {exc}"

        self.externally_managed = False
        start = time.monotonic()
        while time.monotonic() - start < STARTUP_TIMEOUT_SEC:
            if _is_port_open():
                return True, f"started fresh instance, ready in {time.monotonic() - start:.1f}s"
            if self.process.poll() is not None:
                return False, f"process exited during startup (code {self.process.returncode})"
            time.sleep(0.3)
        return False, f"did not become reachable within {STARTUP_TIMEOUT_SEC}s"

    def poll(self) -> int | None:
        """None if unknown (externally managed) or still running; exit code
        if our own process has died."""
        if self.process is None:
            return None
        return self.process.poll()

    def close_logs(self) -> None:
        for f in (self._stdout_f, self._stderr_f):
            if f is not None:
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
        self._stdout_f = None
        self._stderr_f = None

    def stop(self) -> None:
        """Only stops a process THIS script started — never touches a
        server it found already running (same discipline as
        QwenTTSGgmlVulkanProvider.stop_server(), re-implemented here so this
        script stays fully standalone)."""
        self.close_logs()
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def crash_report(self) -> dict:
        report = {
            "externally_managed": self.externally_managed,
            "our_process_returncode": self.process.returncode if self.process is not None else None,
        }
        if self.stdout_path is not None:
            report["stdout_tail"] = _tail_file(self.stdout_path)
        if self.stderr_path is not None:
            report["stderr_tail"] = _tail_file(self.stderr_path)
        if self.externally_managed:
            report["note"] = "server wasn't started by this script — no stdout/stderr captured for it."
        return report


def probe_request(label: str, text: str, response_format: str, use_stream: bool, out_path: Path) -> dict:
    """Fires one POST /v1/audio/speech and records exactly what happened —
    status, headers, time-to-first-byte, total bytes, elapsed, and any
    request-level exception (which on this API has previously meant
    "the subprocess crashed mid-response", not just a slow response)."""
    result: dict = {
        "label": label,
        "text_key": None,
        "text_len": len(text),
        "response_format": response_format,
        "stream_param": use_stream,
        "status_code": None,
        "headers": None,
        "first_chunk_time_sec": None,
        "total_bytes": 0,
        "elapsed_sec": None,
        "error": None,
        "output_file": str(out_path),
    }
    start = time.monotonic()
    first_chunk_time: float | None = None
    total_bytes = 0
    try:
        with requests.post(
            f"{SERVER_URL}/v1/audio/speech",
            json={
                "input": text,
                "voice": config.QWEN_TTS_DEFAULT_SPEAKER,
                "response_format": response_format,
            },
            stream=use_stream,
            timeout=config.QWEN_TTS_TIMEOUT_SECONDS,
        ) as response:
            result["status_code"] = response.status_code
            result["headers"] = dict(response.headers)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if use_stream:
                with open(out_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        if first_chunk_time is None:
                            first_chunk_time = time.monotonic() - start
                        total_bytes += len(chunk)
                        f.write(chunk)
            else:
                content = response.content
                first_chunk_time = time.monotonic() - start
                total_bytes = len(content)
                out_path.write_bytes(content)
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_sec"] = round(time.monotonic() - start, 3)
    result["first_chunk_time_sec"] = round(first_chunk_time, 3) if first_chunk_time is not None else None
    result["total_bytes"] = total_bytes
    result["ok"] = result["error"] is None and result["status_code"] == 200 and total_bytes > 0
    return result


def print_result(r: dict) -> None:
    status = "OK" if r["ok"] else "FAIL"
    print(
        f"  [{status}] {r['label']} fmt={r['response_format']} stream={r['stream_param']} "
        f"status={r['status_code']} first_chunk={r['first_chunk_time_sec']}s "
        f"bytes={r['total_bytes']} elapsed={r['elapsed_sec']}s"
        + (f" error={r['error']}" if r["error"] else "")
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"server_url": SERVER_URL, "tests": [], "crash_events": []}

    print(f"Target tts-server: {SERVER_URL}")
    print(f"Output dir: {OUT_DIR}")

    handle = ServerHandle()
    print("\n--- Ensuring tts-server is reachable ---")
    ok, detail = handle.ensure_running("run1")
    print(f"  {detail}")
    if not ok:
        report["fatal_error"] = detail
        (OUT_DIR / "probe_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\nRESULT: FAIL (could not reach or start tts-server.exe — see probe_report.json)")
        return 1

    server_died = False
    crash_text_key: str | None = None

    # --- A: response_format=wav, plain non-stream request ---
    print("\n--- A: response_format=wav, non-stream ---")
    for key, text in TEXTS.items():
        out_path = OUT_DIR / f"probe_wav_nonstream_{key}.wav"
        r = probe_request(f"A-{key}", text, "wav", False, out_path)
        r["text_key"] = key
        report["tests"].append(r)
        print_result(r)
        if not _is_port_open():
            server_died, crash_text_key = True, key
            break

    # --- B: response_format=wav, stream=True ---
    if not server_died:
        print("\n--- B: response_format=wav, stream=True ---")
        for key, text in TEXTS.items():
            out_path = OUT_DIR / "stream_test.wav"
            r = probe_request(f"B-{key}", text, "wav", True, out_path)
            r["text_key"] = key
            report["tests"].append(r)
            print_result(r)
            if not _is_port_open():
                server_died, crash_text_key = True, key
                break

    # --- C: response_format=pcm, stream=True ---
    if not server_died:
        print("\n--- C: response_format=pcm, stream=True ---")
        for key, text in TEXTS.items():
            out_path = OUT_DIR / "stream_test.pcm"
            r = probe_request(f"C-{key}", text, "pcm", True, out_path)
            r["text_key"] = key
            report["tests"].append(r)
            print_result(r)
            if not _is_port_open():
                server_died, crash_text_key = True, key
                break

    # --- D: 3 consecutive PCM requests back-to-back, using TEXT_SHORT (the
    # exact phrase that crashed the server in the original manual probe) ---
    if not server_died:
        print("\n--- D: 3x consecutive pcm requests (TEXT_SHORT, the known crash trigger) ---")
        for i in range(1, 4):
            out_path = OUT_DIR / f"probe_pcm_repeat_{i}.pcm"
            r = probe_request(f"D-repeat{i}", TEXTS["short"], "pcm", True, out_path)
            r["text_key"] = "short"
            report["tests"].append(r)
            print_result(r)
            if not _is_port_open():
                server_died, crash_text_key = True, "short"
                break

    if server_died:
        print(f"\n!!! tts-server appears to have died (port {PORT} no longer reachable), "
              f"triggered around text={crash_text_key!r} !!!")
        # Give the OS a moment to flush any last stdout/stderr before we read it.
        time.sleep(0.5)
        crash = handle.crash_report()
        crash["triggered_by_text_key"] = crash_text_key
        report["crash_events"].append(crash)
        print("Crash report:")
        print(json.dumps(crash, indent=2, ensure_ascii=False))

    # --- E: stop/start after crash (or a clean stop/start cycle if nothing
    # crashed, to also confirm the start/stop lifecycle itself is sound) ---
    print("\n--- E: stop/start recovery check ---")
    handle.stop()
    time.sleep(1.0)
    port_freed = not _is_port_open()
    print(f"  port freed after stop: {port_freed}")

    ok2, detail2 = handle.ensure_running("recovery")
    print(f"  restart: {detail2}")
    recovery_ok = False
    if ok2:
        r = probe_request("E-recovery-wav", TEXTS["medium"], "wav", False, OUT_DIR / "probe_recovery.wav")
        print_result(r)
        recovery_ok = r["ok"]
    report["recovery"] = {"port_freed_after_stop": port_freed, "restart_ok": ok2, "restart_detail": detail2, "post_restart_request_ok": recovery_ok}

    # Only stop the server at the end if THIS script started it — never kill
    # something we found already running externally.
    handle.stop()

    report_path = OUT_DIR / "probe_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Recommendation ---
    pcm_tests = [t for t in report["tests"] if t["response_format"] == "pcm"]
    wav_stream_tests = [t for t in report["tests"] if t["response_format"] == "wav" and t["stream_param"]]
    pcm_all_ok = bool(pcm_tests) and all(t["ok"] for t in pcm_tests) and not server_died
    wav_stream_all_ok = bool(wav_stream_tests) and all(t["ok"] for t in wav_stream_tests)

    print("\n=== SUMMARY ===")
    print(f"Total probes run: {len(report['tests'])}")
    print(f"Server crashed during probe: {server_died}" + (f" (around text={crash_text_key!r})" if server_died else ""))
    print(f"PCM streaming all-OK: {pcm_all_ok}")
    print(f"WAV stream=True all-OK: {wav_stream_all_ok}")
    print(f"Recovery after stop/start: {report['recovery']}")
    print(f"Full report: {report_path}")

    print("\n=== RECOMMENDATION ===")
    if server_died:
        print(
            "DEFER real PCM/streaming TTS (Phase 2). tts-server.exe crashed during this probe "
            f"(around text={crash_text_key!r}, response_format tested at crash time — see "
            "probe_report.json's crash_events for the exact request and stdout/stderr tail). "
            "Do not build /api/voice/tts/stream on top of the raw response_format=pcm path "
            "until this crash is root-caused upstream in qwentts.cpp. The production WAV path "
            "(non-stream, response_format=wav) is unaffected and remains the only supported path."
        )
    elif pcm_all_ok:
        print(
            "PCM streaming (response_format=pcm, stream=True) survived every probe in this run "
            "(short/medium/russian texts, plus 3 consecutive back-to-back calls on the "
            "previously-crashing short phrase) without the subprocess dying. This is enough "
            "evidence to prototype a real /api/voice/tts/stream endpoint in Phase 2 — but treat "
            "this as necessary, not sufficient: re-run this probe a few more times (including "
            "longer texts and rapid successive calls under load) before wiring it into the UI, "
            "since a single clean run doesn't rule out an intermittent crash condition."
        )
    elif wav_stream_all_ok:
        print(
            "response_format=pcm was NOT fully stable, but response_format=wav with stream=True "
            "did survive. A 'progressive WAV download' (stream the WAV response body as it "
            "arrives) could be considered for Phase 2 instead of true PCM streaming — but this "
            "is NOT low-latency PCM streaming: a WAV response still needs its header/framing, "
            "and chunked delivery of a WAV body does not give sample-accurate incremental "
            "playback the way raw PCM would. Document this limitation honestly to the UI team "
            "rather than presenting it as real streaming."
        )
    else:
        print(
            "Neither PCM nor WAV streaming produced a fully clean run (see probe_report.json for "
            "per-request detail) even though the server didn't outright crash. Streaming TTS "
            "should stay deferred until the specific failures are understood."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
