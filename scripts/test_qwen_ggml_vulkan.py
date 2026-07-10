"""Smoke-тест Qwen3-TTS (GGML/Vulkan) — ПРОТИВ уже запущенного Siena backend'а,
в отличие от scripts/test_faster_qwen3_tts.py (который бьёт напрямую в пакет
faster-qwen3-tts в изоляции, без backend'а).

Здесь провайдер (voice/qwen_tts_ggml_vulkan.py::QwenTTSGgmlVulkanProvider)
уже интегрирован в api/server.py, поэтому и проверять его нужно через
реальные HTTP-эндпоинты — так же, как его будет дёргать UI/voice pipeline.

Требования:
- Siena backend должен быть запущен на http://127.0.0.1:8000 (start_backend.bat).
- config.VOICE_TTS_PROVIDER == "qwen3_tts_ggml_vulkan" (иначе эндпоинты
  /api/voice/tts/* вернут 400 — тест сообщит об этом явно).

Запуск:

    python scripts/test_qwen_ggml_vulkan.py

Проверяет: GET /api/voice/status -> POST .../tts/stop (чистое состояние) ->
POST .../tts/start -> POST .../tts/test -> WAV существует/читается (mono,
24000 Hz, duration > 1s) -> POST .../tts/stop -> порт 8080 действительно
освобождён. Ничего не трогает в STT/OCR/model routing/research/Wagner-флоу —
только эти четыре voice-эндпоинта.
"""

from __future__ import annotations

import socket
import sys
import time
import wave
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
TEST_TEXT = "Привет, Максим. Это smoke test Qwen TTS Vulkan через Siena backend."
EXPECTED_SAMPLE_RATE = 24000
EXPECTED_CHANNELS = 1
MIN_DURATION_SEC = 1.0
TTS_SERVER_HOST = "127.0.0.1"
TTS_SERVER_PORT = 8080
PORT_RELEASE_TIMEOUT_SEC = 10


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run() -> int:
    print(f"Siena backend: {BASE_URL}")

    # 1. GET /api/voice/status
    try:
        status = requests.get(f"{BASE_URL}/api/voice/status", timeout=10).json()
    except requests.RequestException as exc:
        print(f"ОШИБКА: не удалось достучаться до {BASE_URL}/api/voice/status: {exc}")
        print("Убедись, что backend запущен (start_backend.bat).")
        return 1

    provider = status.get("tts_provider")
    available = status.get("tts_available")
    fallback_provider = status.get("tts_fallback_provider")
    print(f"provider={provider!r} available={available!r} fallback_provider={fallback_provider!r}")

    if provider != "qwen3_tts_ggml_vulkan":
        print(
            f"ОШИБКА: активный TTS-провайдер {provider!r}, а не 'qwen3_tts_ggml_vulkan'. "
            "Проверь config.VOICE_TTS_PROVIDER и перезапусти backend."
        )
        return 1

    # 2. POST /api/voice/tts/stop — чистое стартовое состояние (best-effort:
    # если сервер и так не был поднят, ответ всё равно ok=true, running=false).
    try:
        requests.post(f"{BASE_URL}/api/voice/tts/stop", timeout=10)
    except requests.RequestException as exc:
        print(f"ПРЕДУПРЕЖДЕНИЕ: /api/voice/tts/stop (сброс состояния) не удался: {exc}")

    # 3. POST /api/voice/tts/start
    print("Запускаю tts-server.exe...")
    try:
        start_resp = requests.post(f"{BASE_URL}/api/voice/tts/start", timeout=40)
        start_resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"ОШИБКА: /api/voice/tts/start не удался: {exc}")
        if exc.response is not None:
            print(f"  ответ сервера: {exc.response.text}")
        return 1
    print(f"start -> {start_resp.json()}")

    # 4. POST /api/voice/tts/test
    print(f"Синтез тестовой фразы ({len(TEST_TEXT)} символов)...")
    start = time.monotonic()
    try:
        test_resp = requests.post(
            f"{BASE_URL}/api/voice/tts/test", json={"text": TEST_TEXT}, timeout=60
        )
        test_resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"ОШИБКА: /api/voice/tts/test не удался: {exc}")
        if exc.response is not None:
            print(f"  ответ сервера: {exc.response.text}")
        return 1
    request_elapsed = time.monotonic() - start
    result = test_resp.json()

    audio_path = result.get("audio_path")
    duration_sec = result.get("duration_sec")
    elapsed_sec = result.get("elapsed_sec")
    voice = result.get("voice")

    if not audio_path:
        print(f"ОШИБКА: ответ /api/voice/tts/test не содержит audio_path: {result}")
        return 1

    # 5. WAV существует и читается, sample_rate/channels/duration корректны.
    wav_path = Path(audio_path)
    if not wav_path.exists():
        print(f"ОШИБКА: WAV-файл не найден на диске: {wav_path}")
        return 1

    try:
        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            frames = wf.getnframes()
            wav_duration = frames / sample_rate if sample_rate else 0.0
    except wave.Error as exc:
        print(f"ОШИБКА: не удалось прочитать WAV через wave.open: {exc}")
        return 1

    checks = [
        ("sample_rate == 24000", sample_rate == EXPECTED_SAMPLE_RATE, sample_rate),
        ("mono (channels == 1)", channels == EXPECTED_CHANNELS, channels),
        ("duration > 1s", wav_duration > MIN_DURATION_SEC, round(wav_duration, 3)),
    ]
    ok = True
    for label, passed, actual in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {label} (actual={actual})")
        ok = ok and passed

    if not ok:
        print("ОШИБКА: WAV не прошёл проверку формата/длительности.")
        return 1

    rtf = elapsed_sec / duration_sec if duration_sec else None

    # 6. POST /api/voice/tts/stop
    try:
        stop_resp = requests.post(f"{BASE_URL}/api/voice/tts/stop", timeout=10)
        stop_resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"ОШИБКА: /api/voice/tts/stop (финальная остановка) не удался: {exc}")
        return 1
    print(f"stop -> {stop_resp.json()}")

    # 7. Порт 8080 действительно освобождён.
    port_released = False
    deadline = time.monotonic() + PORT_RELEASE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if not _is_port_open(TTS_SERVER_HOST, TTS_SERVER_PORT):
            port_released = True
            break
        time.sleep(0.5)

    print(f"  [{'OK' if port_released else 'FAIL'}] порт {TTS_SERVER_PORT} освобождён после stop")
    if not port_released:
        print(f"ОШИБКА: tts-server всё ещё отвечает на {TTS_SERVER_HOST}:{TTS_SERVER_PORT} после stop.")
        return 1

    print()
    print("=== ИТОГ ===")
    print(f"provider:          {provider}")
    print(f"available:         {available}")
    print(f"fallback_provider: {fallback_provider}")
    print(f"voice:             {voice}")
    print(f"output wav path:   {audio_path}")
    print(f"duration:          {duration_sec}s")
    print(f"elapsed (server):  {elapsed_sec}s")
    print(f"elapsed (request): {round(request_elapsed, 3)}s")
    print(f"approx RTF:        {round(rtf, 3) if rtf is not None else 'n/a'}")
    print()
    print("Все проверки пройдены.")
    return 0


def main() -> int:
    code = _run()
    print()
    print(f"RESULT: {'PASS' if code == 0 else 'FAIL'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
