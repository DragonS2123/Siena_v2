"""Живой regression-тест research grounding — против уже запущенного Siena
backend'а (аналогично scripts/test_qwen_ggml_vulkan.py), не unit-стаб.

История: до 2026-07-07 модель отвечала на вопросы про ЧВК «Вагнер» ПРЯМО из
своих (устаревших/неверных) внутренних знаний, ни разу не вызывая web_search,
и придумывала несуществующие детали — например версию "ареста" Пригожина
вместо реальной гибели в авиакатастрофе 23 августа 2023 года, и полностью
вымышленного "арестованного" человека. Причина: core/model_router.py и
config.SYSTEM_PROMPT ограничивали только ЧТО модель говорит ПОСЛЕ вызова
инструмента — но модель часто не вызывала web_search вообще, и эти
ограничения просто не успевали сработать.

Исправление (core/research_intent.py + config.SYSTEM_PROMPT): диагностический
regex-нюдж для вопросов вида "кто такой/такие X" / "что произошло с X" /
диапазон лет — не решение Runtime за модель, а более настойчивая подсказка +
trace-событие research_grounding_intent_detected, плюс усиленный
Contradiction guard (явный запрет называть погибшего/арестованного человека
новым руководителем; обязательные 2-3 более узких web_search-запроса для
многолетних вопросов).

ВАЖНО: LLM недетерминирована — этот скрипт проверяет ключевые факты/маркеры,
а не точный текст ответа. Он не гарантирует 100% отсутствие галлюцинаций
(9B-модель — известное ограничение, см. NEXTDO.md), но ловит регресс по
основным пунктам: (1) web_search вообще вызывается для таких вопросов,
(2) в ответе нет явных запрещённых утверждений (арест, Белгородская область),
(3) для прямого вопроса про Пригожина и Уткина в ответе есть правильные
дата/место/причина смерти.

Запуск:

    python scripts/test_wagner_regression.py

Требования: Siena backend запущен на http://127.0.0.1:8000, есть доступ в
интернет (реальный web_search через DDGS). Ничего не трогает в
STT/OCR/model routing/Insights/Settings persistence/Runtime meters — только
/api/chat, /api/conversations, /api/trace/recent.
"""

from __future__ import annotations

import sys
import time

import requests

BASE_URL = "http://127.0.0.1:8000"

PROMPTS = [
    "Кто такие Вагнеры?",
    "Что произошло с ЧВК Вагнер с 2022 по 2026 год?",
    "Что произошло с Пригожиным и Уткиным?",
]

# Substrings that must NEVER appear in any answer in this conversation —
# каждый из них был реальной галлюцинацией в исходном баг-репорте.
FORBIDDEN_SUBSTRINGS = [
    "белгород",  # выдуманный "инцидент с грузовиком в Белгородской области"
]

# Прямые ложные комбинации "арест/задержание" рядом с реальными именами —
# проверяются отдельно (см. _check_no_false_arrest), т.к. само слово "арест"
# может законно появиться в контексте других людей/тем.
ARREST_WORDS = ("арестова", "задержа")
PEOPLE_WHO_WERE_NOT_ARRESTED = ("пригожин", "уткин")

# Для прямого вопроса про Пригожина и Уткина ответ должен содержать эти
# маркеры реального события (авиакатастрофа 23 августа 2023, Тверская обл.).
EXPECTED_DEATH_MARKERS = ["23 август", "твер", "пригожин", "уткин"]
DEATH_WORDS = ("погиб", "разби", "катастроф", "крушен")


def _create_and_activate_conversation() -> str:
    r = requests.post(f"{BASE_URL}/api/conversations", json={"title": "Wagner regression check"}, timeout=30)
    r.raise_for_status()
    conversation_id = r.json()["conversation_id"]
    r = requests.post(f"{BASE_URL}/api/conversations/{conversation_id}/activate", timeout=30)
    r.raise_for_status()
    return conversation_id


def _send_chat(message: str) -> str:
    r = requests.post(f"{BASE_URL}/api/chat", json={"message": message}, timeout=280)
    r.raise_for_status()
    return r.json()["answer"]


def _tool_names_since(events: list[dict], start_idx: int) -> list[str]:
    names = []
    for e in events[start_idx:]:
        if e.get("event") == "tool_dispatch":
            names.append(e.get("name"))
        if e.get("event") == "final_answer":
            break
    return names


def _find_user_message_index(events: list[dict], content: str) -> int | None:
    """Returns the LAST matching index, not the first — /api/trace/recent
    accumulates across every run of this script within the same JSONL log
    file (and across conversations), so an identical prompt text can appear
    multiple times. Picking the first match would silently analyze a stale
    prior run's tool calls instead of the one just sent."""
    last = None
    for i, e in enumerate(events):
        if e.get("event") == "user_message" and e.get("content") == content:
            last = i
    return last


def _check_no_false_arrest(answer_lower: str) -> str | None:
    for person in PEOPLE_WHO_WERE_NOT_ARRESTED:
        if person not in answer_lower:
            continue
        # ищем упоминание слова "арест"/"задержа" в пределах ~120 символов
        # вокруг имени — грубая, но достаточная эвристика близости.
        for idx in _all_indexes(answer_lower, person):
            window = answer_lower[max(0, idx - 120): idx + 120]
            for arrest_word in ARREST_WORDS:
                if arrest_word in window:
                    return f"'{arrest_word}' упомянуто рядом с '{person}' — ложное утверждение об аресте"
    return None


def _all_indexes(haystack: str, needle: str) -> list[int]:
    out = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        out.append(idx)
        start = idx + 1
    return out


def main() -> int:
    print(f"Siena backend: {BASE_URL}")
    conversation_id = _create_and_activate_conversation()
    print(f"conversation_id: {conversation_id}")

    all_ok = True
    answers: list[str] = []

    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"\n--- [{i}/3] {prompt}")
        t0 = time.monotonic()
        answer = _send_chat(prompt)
        elapsed = round(time.monotonic() - t0, 1)
        answers.append(answer)
        print(f"  elapsed: {elapsed}s")
        print(f"  answer: {answer[:200]}{'...' if len(answer) > 200 else ''}")

        events = requests.get(f"{BASE_URL}/api/trace/recent?limit=300", timeout=30).json()["events"]
        start_idx = _find_user_message_index(events, prompt)
        tools_called = _tool_names_since(events, start_idx) if start_idx is not None else []
        print(f"  tools called: {tools_called or '(none)'}")

        answer_lower = answer.lower()

        if not tools_called:
            print("  [FAIL] web_search/open_url was not called for this turn")
            all_ok = False
        else:
            print("  [OK] a tool was called")

        forbidden_hit = next((s for s in FORBIDDEN_SUBSTRINGS if s in answer_lower), None)
        if forbidden_hit:
            print(f"  [FAIL] forbidden substring found: {forbidden_hit!r}")
            all_ok = False
        else:
            print("  [OK] no forbidden substrings")

        arrest_issue = _check_no_false_arrest(answer_lower)
        if arrest_issue:
            print(f"  [FAIL] {arrest_issue}")
            all_ok = False
        else:
            print("  [OK] no false arrest claim near Prigozhin/Utkin")

        if i == 3:
            missing_markers = [m for m in EXPECTED_DEATH_MARKERS if m not in answer_lower]
            has_death_word = any(w in answer_lower for w in DEATH_WORDS)
            if missing_markers or not has_death_word:
                print(f"  [FAIL] expected death markers missing: {missing_markers}, death_word_present={has_death_word}")
                all_ok = False
            else:
                print("  [OK] correct death circumstances present (23 Aug 2023, Tver oblast, plane crash)")

    print("\n=== ИТОГ ===")
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
