"""Автономный тест Faster Qwen3-TTS — ВНЕ основной интеграции Siena.

Не импортирует ничего из voice/* — проверяет напрямую пакет
`faster-qwen3-tts` (класс FasterQwen3TTS) в этом окружении. Если этот скрипт
работает, дальше используется voice/faster_qwen_tts.py (обёртка с тем же
контрактом, что и voice/tts.py::SileroTTSProvider / voice/qwen_tts.py).

Настройки читаются из config.py, если он импортируется (у config.py нет
тяжёлых зависимостей — только pathlib, безопасно даже из отдельного venv);
если импорт не удался — используются безопасные дефолты ниже.

Установка (отдельный venv, БЕЗ conda — см. README.md, раздел
"Faster Qwen3-TTS"):

    py -3.12 -m venv .venv-faster-qwen3-tts
    .venv-faster-qwen3-tts\\Scripts\\activate
    python -m pip install -U pip
    pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu128
    pip install faster-qwen3-tts qwen-tts soundfile

Запуск (из активированного .venv-faster-qwen3-tts):

    python scripts/test_faster_qwen3_tts.py

Результат: siena_faster_qwen3_test.wav в корне проекта, плюс elapsed/
duration/RTF в консоли. Первый вызов включает прогрев (CUDA graph capture на
CUDA) — секунды; второй вызов на том же процессе был бы намного быстрее, но
этот скрипт — одноразовая проверка, как отдельные вызовы synthesize_to_file()
в реальном backend'е (см. voice/faster_qwen_tts.py про то, почему модель
держится в памяти между вызовами вместо перезапуска процесса на каждый).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_FALLBACK_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
_FALLBACK_LANGUAGE = "russian"
_FALLBACK_SPEAKER = "serena"
_FALLBACK_INSTRUCT = (
    "Mature adult female Russian voice. Calm, warm, soft, emotionally "
    "grounded. Lower pitch, less cute, less anime, less childish. "
    "Natural close conversation. Not theatrical, not announcer-like, "
    "not cartoon-like."
)
_FALLBACK_DEVICE = "cuda"
_FALLBACK_DTYPE = "bf16"

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import config

    MODEL_REPO = config.FASTER_QWEN_TTS_MODEL_REPO
    LANGUAGE = config.FASTER_QWEN_TTS_LANGUAGE
    SPEAKER = config.FASTER_QWEN_TTS_SPEAKER
    INSTRUCT = config.FASTER_QWEN_TTS_INSTRUCT
    DEVICE_PREF = config.FASTER_QWEN_TTS_DEVICE
    DTYPE_NAME = config.FASTER_QWEN_TTS_DTYPE
except Exception:
    MODEL_REPO = _FALLBACK_MODEL_REPO
    LANGUAGE = _FALLBACK_LANGUAGE
    SPEAKER = _FALLBACK_SPEAKER
    INSTRUCT = _FALLBACK_INSTRUCT
    DEVICE_PREF = _FALLBACK_DEVICE
    DTYPE_NAME = _FALLBACK_DTYPE

OUTPUT_PATH = "siena_faster_qwen3_test.wav"

TEST_TEXT = (
    "Привет, Максим. Это быстрый тест голоса Siena. "
    "Я говорю спокойно, мягко и естественно. "
    "Давай проверим, подходит ли этот голос для меня."
)

_DTYPE_ALIASES = {
    "bf16": "bfloat16", "bfloat16": "bfloat16",
    "fp16": "float16", "float16": "float16",
    "fp32": "float32", "float32": "float32",
}


def main() -> int:
    try:
        import torch
        from faster_qwen3_tts import FasterQwen3TTS
    except ImportError as exc:
        print(f"ОШИБКА ИМПОРТА: {exc}")
        print("Убедись, что активирован .venv-faster-qwen3-tts и выполнен: pip install faster-qwen3-tts qwen-tts soundfile")
        return 1

    try:
        import soundfile as sf
    except ImportError as exc:
        print(f"ОШИБКА ИМПОРТА soundfile: {exc}")
        return 1

    device = DEVICE_PREF if (DEVICE_PREF == "cuda" and torch.cuda.is_available()) else "cpu"
    dtype = getattr(torch, _DTYPE_ALIASES.get(DTYPE_NAME.lower(), "bfloat16"))
    print(f"device={device!r}, dtype={dtype}, model={MODEL_REPO!r}")

    print("Загрузка модели + CUDA graph capture (может занять десятки секунд на первый раз)...")
    start = time.monotonic()
    try:
        model = FasterQwen3TTS.from_pretrained(MODEL_REPO, device=device, dtype=dtype)
    except Exception as exc:
        print(f"ОШИБКА ЗАГРУЗКИ МОДЕЛИ: {type(exc).__name__}: {exc}")
        return 1
    print(f"Модель загружена за {time.monotonic() - start:.1f}с")

    print(f"Синтез текста ({len(TEST_TEXT)} символов, language={LANGUAGE!r}, speaker={SPEAKER!r})...")
    start = time.monotonic()
    try:
        wavs, sr = model.generate_custom_voice(
            text=TEST_TEXT,
            speaker=SPEAKER,
            language=LANGUAGE,
            instruct=INSTRUCT,
        )
    except Exception as exc:
        print(f"ОШИБКА СИНТЕЗА: {type(exc).__name__}: {exc}")
        print(
            "Если ошибка про неизвестный speaker/language — проверь актуальный "
            "список в README (aiden/dylan/eric/ono_anna/ryan/serena/sohee/uncle_fu/vivian)."
        )
        return 1
    elapsed = time.monotonic() - start

    audio = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    sf.write(OUTPUT_PATH, audio, sr)
    duration_sec = len(audio) / sr
    rtf = elapsed / duration_sec if duration_sec > 0 else float("inf")
    print(
        f"Готово за {elapsed:.2f}с. Файл: {OUTPUT_PATH} "
        f"({duration_sec:.2f}с аудио, sample_rate={sr}, RTF={rtf:.2f})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
