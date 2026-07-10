"""Detects whether a user's message looks like a request to identify a named
entity or to describe what happened to it / its current status ("кто такие
X", "что произошло с X", "текущий статус X", a "с YYYY по YYYY" date range,
etc.).

Same spirit as core/model_router.py / core/image_intent.py /
core/memory_intent.py: a lightweight regex heuristic, not a decision. The
model still decides whether and what to search — this only produces a
diagnostic trace event (research_grounding_intent_detected) and a soft
in-context reminder for that turn.

Why this exists: a live regression test (2026-07-07) showed the model
answering "Кто такие Вагнеры?" and "Что произошло с ЧВК Вагнер с 2022 по 2026
год?" directly from its own (wrong) training knowledge, with ZERO web_search
calls in either turn — fabricating a founder name, a founding decade, and a
named "arrested" individual that don't exist. The existing "Research
discipline" / "Contradiction guard" rules in config.SYSTEM_PROMPT only
constrain what the model says AFTER a tool was called — they never fire if
the model never decides to call a tool in the first place. This detector
targets exactly that gap: identity/status questions are precisely where a
language model's frozen training data is most likely to be stale or wrong,
so nudging toward web_search here is cheap insurance regardless of how
confident the model feels — deliberately NOT hardcoded to specific entity
names (no "Вагнер"/"Пригожин" keywords), since that would only fix this one
case instead of the general question pattern.
"""

from __future__ import annotations

import re

_IDENTITY_QUESTION_RE = re.compile(r"кто\s+так(?:ой|ая|ие|ое)\b", re.IGNORECASE)

_STATUS_QUESTION_RE = re.compile(
    r"(что\s+(?:произошло|случилось|стало)\s+с\b|"
    r"текущ\w*\s+статус|"
    r"как\w*\s+обстоят\w*\s+дела\s+с|"
    r"что\s+(?:сейчас|сегодня|происходит)\s+с\b)",
    re.IGNORECASE,
)

_DATE_RANGE_RE = re.compile(r"с\s+\d{4}\s*(?:год\w*)?\s*по\s+\d{4}", re.IGNORECASE)


def wants_grounded_research(text: str) -> bool:
    return bool(
        _IDENTITY_QUESTION_RE.search(text)
        or _STATUS_QUESTION_RE.search(text)
        or _DATE_RANGE_RE.search(text)
    )
