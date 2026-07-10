"""Общая механическая подготовка текста для TTS-провайдеров (Silero, Qwen3-TTS,
любой будущий). Не смысловая обработка — текст не меняется по содержанию,
только режется по уже существующей пунктуации на фразы + паузы между ними,
чтобы движок озвучки не читал длинный текст одним "пулемётным" потоком.

Изначально написано для voice/tts.py (Silero), вынесено сюда, чтобы
voice/qwen_tts.py мог использовать ровно ту же логику паузы/дробления, не
дублируя код и не завися от SileroTTSProvider напрямую.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

LETTER_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]")


def normalize_for_speech(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("...", "…")
    return text


def split_for_speech(text: str) -> list[tuple[str, float]]:
    """Разбивает текст на фразы для отдельных вызовов синтеза, с паузой
    (в секундах) после каждой."""
    text = normalize_for_speech(text)

    chunks: list[tuple[str, float]] = []
    buf: list[str] = []

    def flush(pause: float) -> None:
        chunk = "".join(buf).strip()
        buf.clear()
        if chunk:
            chunks.append((chunk, pause))

    for ch in text:
        buf.append(ch)
        current = "".join(buf).strip()

        if ch in ".!?…":
            flush(0.32)
        elif ch in ",;:" and len(current) >= 35:
            flush(0.14)

    flush(0.0)

    # Некоторые TTS-движки (например, Silero) падают с непонятной ошибкой на
    # чанке без единой буквы — например, дата "07."/"2026!", вырезанная из
    # "01.07.2026!" циклом выше, или одиночный emoji. Склеиваем такие чанки с
    # соседним, у которого буквы есть, вместо того чтобы отправлять их в
    # синтез поодиночке.
    merged: list[tuple[str, float]] = []
    leading_pending = ""
    for chunk, pause in chunks:
        if LETTER_RE.search(chunk):
            if leading_pending:
                chunk = f"{leading_pending} {chunk}".strip()
                leading_pending = ""
            merged.append((chunk, pause))
        elif merged:
            prev_chunk, _ = merged[-1]
            merged[-1] = (f"{prev_chunk} {chunk}".strip(), pause)
        else:
            leading_pending = f"{leading_pending} {chunk}".strip()

    if leading_pending:
        if merged:
            prev_chunk, prev_pause = merged[-1]
            merged[-1] = (f"{prev_chunk} {leading_pending}".strip(), prev_pause)
        else:
            # весь текст без единой буквы (только цифры/символы/emoji) —
            # некуда склеивать; пропускаем как есть, чтобы вызывающий получил
            # понятную ошибку от самого движка, а не тихо промолчал.
            merged.append((leading_pending, 0.0))

    return merged


def to_numpy_audio(audio: Any) -> np.ndarray:
    if hasattr(audio, "detach"):
        audio = audio.detach()
    if hasattr(audio, "cpu"):
        audio = audio.cpu()
    if hasattr(audio, "numpy"):
        return audio.numpy()
    return np.asarray(audio)
