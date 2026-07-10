"""Code routing correctness (HANDOFF_v2.md "image/code routing order" pass):
broadened _CODE_PATTERNS (a bare "Почему не работает?"/"Проверь код" fell
through to the plain chat model before this pass), plus the new
has_code_context-aware ambiguous patterns for a code/error screenshot whose
OCR text alone gives away that it's code (e.g. "Что за ошибка?" said about
an attached traceback screenshot).

Uses monkeypatch on the `config` module attributes the router reads live
(ENABLE_MODEL_ROUTER/ENABLE_CODE_SPECIALIST_AUTO/ENABLE_REVIEWER_EXPLICIT),
matching the pattern already used in tests/test_memory_search.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import config  # noqa: E402
from core import model_router  # noqa: E402


@pytest.fixture(autouse=True)
def _enable_router(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_MODEL_ROUTER", True)
    monkeypatch.setattr(config, "ENABLE_CODE_SPECIALIST_AUTO", True)
    monkeypatch.setattr(config, "ENABLE_REVIEWER_EXPLICIT", True)


# --- Plain code requests, no attachment needed -----------------------------

CODE_TRUE = [
    "Исправь этот код",
    "Проверь код",
    "Почему ошибка в этом stacktrace?",
    "Напиши функцию на питоне",
    "Рефакторинг",
    "Почему не работает?",
    "исправь код на скриншоте",
]


@pytest.mark.parametrize("text", CODE_TRUE)
def test_plain_code_requests_route_to_code_specialist(text):
    decision = model_router.route(text)
    assert decision.role == "code_specialist"
    assert decision.model == config.CODE_MODEL
    assert decision.reason == "code_specialist"
    assert decision.is_specialist is True


def test_vision_only_question_does_not_route_to_code():
    # item D: "что на изображении?" is a vision question, never a coder one.
    decision = model_router.route("Что на изображении?")
    assert decision.role != "code_specialist"


def test_pure_chit_chat_stays_on_main_chat():
    decision = model_router.route("Как твои дела сегодня?")
    assert decision.role == "main_chat"
    assert decision.reason == "default_main_chat"


# --- Ambiguous phrasing requires corroborating code context -----------------

def test_ambiguous_phrase_alone_does_not_route_to_code():
    decision = model_router.route("Что за ошибка?", has_code_context=False)
    assert decision.role != "code_specialist"


def test_ambiguous_phrase_with_code_context_routes_to_code():
    decision = model_router.route("Что за ошибка?", has_code_context=True)
    assert decision.role == "code_specialist"
    assert decision.model == config.CODE_MODEL


def test_ambiguous_phrase_variants_with_code_context():
    for text in ["Что не так?", "В чём проблема?", "Прочитай ошибку", "Почему ошибка?"]:
        decision = model_router.route(text, has_code_context=True)
        assert decision.role == "code_specialist", f"{text!r} should route to code specialist with code context"


# --- looks_like_code_or_error — the signal that justifies has_code_context -

def test_looks_like_code_or_error_detects_traceback():
    sample = "Traceback (most recent call last):\n  File \"x.py\", line 3\nTypeError: bad operand"
    assert model_router.looks_like_code_or_error(sample) is True


def test_looks_like_code_or_error_detects_function_definition():
    assert model_router.looks_like_code_or_error("def handle_click(event):\n    pass") is True


def test_looks_like_code_or_error_ignores_unrelated_ocr_text():
    # A photo of a restaurant menu OCR'd into plain text must never look like
    # code just because it has punctuation.
    assert model_router.looks_like_code_or_error("Меню ресторана: борщ 300р, плов 250р") is False


# --- Review/critic explicit routing (unchanged, sanity check) --------------

def test_explicit_review_request_routes_to_reviewer():
    decision = model_router.route("Проведи ревью этого решения")
    assert decision.role == "reviewer_critic"
    assert decision.model == config.REVIEWER_MODEL


def test_review_request_takes_precedence_over_code_pattern():
    # A message that could arguably match both — review wording should win
    # (matches the router's existing precedence: review checked before code).
    decision = model_router.route("Проведи ревью этого кода")
    assert decision.role == "reviewer_critic"


# --- Router disabled --------------------------------------------------------

def test_router_disabled_always_returns_main_chat(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_MODEL_ROUTER", False)
    decision = model_router.route("Исправь этот код")
    assert decision.role == "main_chat"
    assert decision.model == config.MAIN_CHAT_MODEL


def test_code_specialist_disabled_falls_back_to_main_chat(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_CODE_SPECIALIST_AUTO", False)
    decision = model_router.route("Исправь этот код")
    assert decision.role == "main_chat"
