"""Тесты voice/text_sanitize.py: удаление stage directions в *звёздочках* и
list-маркеров в начале строк, опциональное глобальное удаление чисел,
и что это не смысловая обработка (обычный текст без маркеров не меняется,
кроме нормализации пробелов)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.text_sanitize import sanitize_text_for_tts, sanitize_text_for_tts_detailed  # noqa: E402


def test_removes_stage_direction_with_asterisks():
    result = sanitize_text_for_tts_detailed("Как же здорово! *тихо вздыхает* Я рада за тебя.")
    assert "*" not in result.text
    assert "тихо вздыхает" not in result.text
    assert result.removed_stage_directions is True
    assert result.removed_list_numbers is False


def test_removes_leftover_unpaired_asterisk():
    result = sanitize_text_for_tts("Это *важно")
    assert "*" not in result


def test_removes_numbered_list_markers_at_line_start():
    text = "1. Первое.\n2) Второе.\n3. Третье."
    result = sanitize_text_for_tts_detailed(text)
    assert result.text == "Первое. Второе. Третье."
    assert result.removed_list_numbers is True


def test_removes_bullet_markers_at_line_start():
    text = "- Первое\n• Второе"
    result = sanitize_text_for_tts_detailed(text)
    assert result.text == "Первое Второе"
    assert result.removed_list_numbers is True


def test_does_not_strip_numbers_by_default():
    result = sanitize_text_for_tts_detailed("У меня 3 кота и 25 рыбок.")
    assert "3" in result.text
    assert "25" in result.text
    assert result.removed_list_numbers is False


def test_strip_all_numbers_flag_removes_every_digit():
    result = sanitize_text_for_tts("У меня 3 кота и 25 рыбок.", strip_all_numbers=True)
    assert "3" not in result
    assert "25" not in result
    assert "кота" in result and "рыбок" in result


def test_plain_text_without_markers_is_unchanged_besides_whitespace():
    text = "Обычный текст без маркеров, с пунктуацией!"
    result = sanitize_text_for_tts(text)
    assert result == text


def test_collapses_extra_whitespace():
    result = sanitize_text_for_tts("Привет,   Максим.\n\nКак дела?")
    assert result == "Привет, Максим. Как дела?"


def test_combined_stage_direction_and_list_markers():
    text = "1. *шепчет* Первый пункт.\n2. Второй пункт."
    result = sanitize_text_for_tts_detailed(text)
    assert "*" not in result.text
    assert "шепчет" not in result.text
    assert result.removed_stage_directions is True
    assert result.removed_list_numbers is True
    assert result.original_len == len(text)
    assert result.sanitized_len == len(result.text)
