"""Механическая очистка текста перед TTS — убирает визуальные/технические
маркеры (stage directions в *звёздочках*, нумерация/буллеты списков), которые
не предназначены для озвучки вслух. Это НЕ смысловая обработка: сами слова,
факты и формулировки не меняются — только markup-символы, добавленные для
чтения глазами (Siena сама решает, что сказать; Runtime только убирает то,
что явно не текст для произнесения).

Общий для всех TTS-провайдеров (Silero, Qwen3-TTS, Faster Qwen3-TTS) — единая
точка, чтобы поведение не расходилось между ними.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# *текст* — stage direction / ремарка (например "*тихо вздыхает*") — вырезаем
# целиком вместе со звёздочками. Не смысловая правка: это НЕ то же самое, что
# markdown-emphasis в обычном разговорном тексте, но Qwen3-TTS всё равно
# озвучивает содержимое дословно, включая звёздочки, поэтому мы его снимаем
# независимо от того, было ли это задумано как ремарка или как выделение.
_STAGE_DIRECTION_RE = re.compile(r"\*[^*]*\*")

# Маркеры списков в начале строки: "1. ", "2) ", "- ", "• ".
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:\d+[.)]|[-•])[ \t]+", re.MULTILINE)

# Глобальное удаление ЛЮБЫХ чисел — отдельный, по умолчанию выключенный флаг
# (config.TTS_STRIP_ALL_NUMBERS). Список-маркеры убираются всегда, это отдельно.
_ALL_NUMBERS_RE = re.compile(r"\d+")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class SanitizeResult:
    text: str
    original_len: int
    sanitized_len: int
    removed_stage_directions: bool
    removed_list_numbers: bool


def sanitize_text_for_tts_detailed(text: str, strip_all_numbers: bool = False) -> SanitizeResult:
    original_len = len(text)
    working = text

    removed_stage_directions = bool(_STAGE_DIRECTION_RE.search(working))
    working = _STAGE_DIRECTION_RE.sub("", working)
    working = working.replace("*", "")  # оставшиеся непарные звёздочки

    removed_list_numbers = bool(_LIST_MARKER_RE.search(working))
    working = _LIST_MARKER_RE.sub("", working)

    if strip_all_numbers:
        working = _ALL_NUMBERS_RE.sub("", working)

    working = _WHITESPACE_RE.sub(" ", working).strip()

    return SanitizeResult(
        text=working,
        original_len=original_len,
        sanitized_len=len(working),
        removed_stage_directions=removed_stage_directions,
        removed_list_numbers=removed_list_numbers,
    )


def sanitize_text_for_tts(text: str, strip_all_numbers: bool = False) -> str:
    return sanitize_text_for_tts_detailed(text, strip_all_numbers=strip_all_numbers).text
