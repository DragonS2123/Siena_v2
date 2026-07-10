"""Автономный тест Qwen3-TTS — ВНЕ основной интеграции Siena.

Не импортирует ничего из проекта (config.py, voice/*) — это чистая проверка,
что пакет qwen-tts вообще работает в этом окружении и что реальный Python API
совпадает с тем, что описано в README (https://github.com/QwenLM/Qwen3-TTS).
Если этот скрипт работает, дальше используется voice/qwen_tts.py (обёртка
с тем же контрактом, что и voice/tts.py::SileroTTSProvider).

Установка (отдельный venv, БЕЗ conda — см. README.md, раздел
"Experimental: Qwen3-TTS"):

    py -3.12 -m venv .venv-qwen3-tts
    .venv-qwen3-tts\\Scripts\\activate
    python -m pip install -U pip
    pip install -U qwen-tts soundfile

flash-attn НЕ ставим на этом этапе — он опциональный (ускоряет/экономит
память на CUDA) и может не собраться на Windows. Без него всё работает,
просто без flash_attention_2.

Запуск (из активированного .venv-qwen3-tts):

    python scripts/test_qwen3_tts.py

Результат: siena_qwen3_test.wav в корне проекта.

По умолчанию используется модель 0.6B (быстрее скачать/загрузить для первой
проверки на CPU, чем 1.7B). Если есть CUDA — скрипт сам её использует.
"""

from __future__ import annotations

import sys
import time

MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
LANGUAGE = "Russian"
SPEAKER = "ono_anna"  # см. README проекта — список пресетных голосов; поэкспериментируй с другими

# instruct — техническая инструкция ТОЛЬКО для тембра голоса (не personality
# Siena). Скопировано из дефолтного voice profile "siena_default_adult" (см.
# voice/voice_profiles.py) — правит проблему "слишком аниме-девочка" у
# спикера Vivian без instruct. Поменяй текст здесь, чтобы услышать разницу
# ДО того как менять активный profile через API/UI.
INSTRUCT = (
    "Mature adult female Russian voice. Calm, warm, soft, emotionally "
    "grounded. Lower pitch, less cute, less anime, less childish. "
    "Natural close conversation. Not theatrical, not announcer-like, "
    "not cartoon-like."
)

OUTPUT_PATH = "siena_qwen3_test.wav"

TEST_TEXT = (
    "Привет, Максим. Это тест нового голоса Siena. "
    "Я говорю спокойно, мягко и естественно. "
    "Давай проверим, подходит ли этот голос для меня."
)


def main() -> int:
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        print(f"ОШИБКА ИМПОРТА: {exc}")
        print("Убедись, что активирован .venv-qwen3-tts и выполнен: pip install -U qwen-tts soundfile")
        return 1

    try:
        import soundfile as sf
    except ImportError as exc:
        print(f"ОШИБКА ИМПОРТА soundfile: {exc}")
        return 1

    device_map = "cuda:0" if torch.cuda.is_available() else None
    dtype = torch.bfloat16 if device_map else torch.float32
    print(f"device_map={device_map!r}, dtype={dtype}, model={MODEL_REPO!r}")

    load_kwargs: dict = {"dtype": dtype}
    if device_map:
        load_kwargs["device_map"] = device_map
        # flash_attention_2 сознательно не указываем на первом этапе — см. докстринг модуля.

    print("Загрузка модели (первый раз качает веса с HuggingFace — может занять время)...")
    start = time.monotonic()
    try:
        model = Qwen3TTSModel.from_pretrained(MODEL_REPO, **load_kwargs)
    except Exception as exc:
        print(f"ОШИБКА ЗАГРУЗКИ МОДЕЛИ: {type(exc).__name__}: {exc}")
        return 1
    print(f"Модель загружена за {time.monotonic() - start:.1f}с")

    print(f"Синтез текста ({len(TEST_TEXT)} символов, language={LANGUAGE!r}, speaker={SPEAKER!r}, instruct={INSTRUCT[:60]!r}...)...")
    start = time.monotonic()
    try:
        wavs, sr = model.generate_custom_voice(
            text=TEST_TEXT,
            language=LANGUAGE,
            speaker=SPEAKER,
            instruct=INSTRUCT,
        )
    except Exception as exc:
        print(f"ОШИБКА СИНТЕЗА: {type(exc).__name__}: {exc}")
        print(
            "Если ошибка про неизвестный speaker/language — проверь актуальный "
            "список голосов в README https://github.com/QwenLM/Qwen3-TTS "
            "(возможно, не все спикеры поддерживают все языки)."
        )
        return 1
    elapsed = time.monotonic() - start

    audio = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    sf.write(OUTPUT_PATH, audio, sr)
    duration_sec = len(audio) / sr
    print(f"Готово за {elapsed:.1f}с. Файл: {OUTPUT_PATH} ({duration_sec:.1f}с аудио, sample_rate={sr})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
