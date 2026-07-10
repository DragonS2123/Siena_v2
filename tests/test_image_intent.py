"""Image routing order bugfix (HANDOFF_v2.md): a live trace showed a user
attaching an image and asking "Что на этом изображении?" — OCR ran, vision
never did, and the main model wrongly claimed qwen2.5vl was unavailable.
Root cause reproduced and fixed here: the old vision patterns required
near-rigid adjacency ("что на картинке" but not "что на ЭТОЙ картинке") and
were missing "скриншот" as an image noun and several common verbs entirely.
Also fixed: the old inline `wants_image_understanding(x) and not
wants_ocr(x)` in api/server.py silently broke the "read the text AND
describe the picture" case, since both conditions being True collapsed the
`and not` to False.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.image_intent import decide_vision, wants_image_understanding, wants_ocr  # noqa: E402


# --- Vision: the exact live-log bug + the rest of the task's required set ---

VISION_TRUE = [
    "Что на этом изображении?",  # the exact phrase from the live bug report
    "Что на изображении?",
    "Что на этой картинке?",
    "Что на скриншоте?",
    "Опиши картинку",
    "Посмотри изображение",
    "Разбери изображение",
]


@pytest.mark.parametrize("text", VISION_TRUE)
def test_wants_image_understanding_true(text):
    assert wants_image_understanding(text), f"expected vision intent for {text!r}"


# --- OCR wins when the request is explicitly about reading text -----------

OCR_TRUE = [
    "Что написано на изображении?",
    "Прочитай текст на картинке",
    "Распознай текст на скриншоте",
    "OCR",
]


@pytest.mark.parametrize("text", OCR_TRUE)
def test_wants_ocr_true(text):
    assert wants_ocr(text), f"expected OCR intent for {text!r}"


@pytest.mark.parametrize("text", OCR_TRUE)
def test_ocr_only_requests_do_not_trigger_vision(text):
    # OCR precedence rule: a plain "read the text" request must not also run
    # vision — but see test_both_intents_run_vision_too below for the case
    # where the user explicitly asks for both.
    decision = decide_vision(text, has_image_attachment=True)
    assert decision.run_vision is False
    assert decision.reason == "ocr_only"


# --- Both explicitly requested — the bug where vision got silently --------
# --- suppressed just because OCR was ALSO requested -------------------------

BOTH = [
    "Прочитай текст и опиши картинку",
    "Что написано и что изображено?",
]


@pytest.mark.parametrize("text", BOTH)
def test_both_intents_detected_by_raw_functions(text):
    assert wants_ocr(text) and wants_image_understanding(text)


@pytest.mark.parametrize("text", BOTH)
def test_both_intents_run_vision_too(text):
    # This is the exact bug: the old `wants_image_understanding(x) and not
    # wants_ocr(x)` forced vision off whenever OCR was ALSO detected, so
    # asking for both silently only ever got OCR.
    decision = decide_vision(text, has_image_attachment=True)
    assert decision.run_vision is True
    assert decision.reason == "explicit_both"


# --- Ambiguous short question + image attached -> defaults to vision ------

AMBIGUOUS = ["Что это?", "Что тут?", "Посмотри", "Что думаешь?"]


@pytest.mark.parametrize("text", AMBIGUOUS)
def test_ambiguous_question_with_image_defaults_to_vision(text):
    decision = decide_vision(text, has_image_attachment=True)
    assert decision.run_vision is True
    assert decision.reason == "ambiguous_fallback"


@pytest.mark.parametrize("text", AMBIGUOUS)
def test_ambiguous_question_without_image_does_nothing(text):
    # The exact same short question in a text-only conversation (no image
    # attached at all) must never be treated as a vision request.
    decision = decide_vision(text, has_image_attachment=False)
    assert decision.run_vision is False
    assert decision.reason == "no_image"


def test_ambiguous_pattern_does_not_leak_into_wants_image_understanding():
    # "Что это?" alone is deliberately NOT in the vision keyword list itself
    # — it's only ever treated as a vision question via decide_vision's
    # attachment-aware fallback, never as a standalone "explicit" intent.
    assert wants_image_understanding("Что это?") is False


# --- Precision regression: the loose-gap draft of this fix accidentally ---
# --- made "что написано на изображении" (OCR) also match as vision --------

def test_ocr_phrasing_does_not_falsely_match_vision():
    text = "Что написано на изображении?"
    assert wants_ocr(text) is True
    assert wants_image_understanding(text) is False


def test_no_intent_no_image_related_text():
    decision = decide_vision("Расскажи анекдот", has_image_attachment=True)
    assert decision.run_vision is False
    assert decision.reason == "no_intent"
