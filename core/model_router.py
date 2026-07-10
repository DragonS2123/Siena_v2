"""Specialist model router — picks which model handles a single /api/chat
turn, BEFORE the main agent_loop call runs (Phase 4D + 4E).

This is a SEPARATE mechanism from tools/delegate_model.py: that tool lets the
model itself (PRIMARY_MODEL, mid-turn) call another model for a sub-task and
must synthesize the final answer itself. This router instead decides, once,
up front, which model's weights answer the ENTIRE turn — same Session, same
system prompt, same tool registry — nothing about the conversation
architecture changes, only which model is temporarily "being" Siena for
this one turn.

Hard rule, not just a default: config.MANUAL_HEAVY_MODEL (qwen3.5:27b) is
never AUTOMATICALLY selected by this module. The only way it can be returned
at all is via the `active_chat_model` parameter — set exclusively through the
human action POST /api/models/active (api/server.py), never inferred from
message content. There is no code path here that reads
config.ENABLE_HEAVY_REASONING_AUTO to turn that on — the flag exists purely
as a documented assertion that auto heavy-reasoning routing is absent, not a
switch this module checks.

Runtime's role stays the same as everywhere else in this codebase: this is a
technical classification (regex match on the user's own words, or a
human-set piece of state) — not a judgment about the user's intent, the
quality of their request, or which model is "better".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import config

# Explicit code-task patterns — deliberately narrow (RU/EN), not "any message
# that happens to mention code". A generic question that merely touches on
# programming concepts stays on the chat model; only messages that look like
# an actual request to write/fix/review code as the main task route away.
#
# Bugfix (HANDOFF_v2.md, "image/code routing order" pass): added
# "проверь.*код"/"почему.*не работает"/stacktrace-style phrasing that were
# previously missing entirely, so a bare "Почему не работает?" or "Проверь
# код" fell through to the plain chat model instead of the code specialist.
_CODE_PATTERNS = [
    r"напиши.*(функци|код|скрипт|класс|программ)",
    r"write.*(function|code|script|class|program)",
    r"исправь.*(код|баг|ошибк)",
    r"fix.*(code|bug)",
    r"проверь.*(код|скрипт|функци)",
    r"check.*(code|script|function)",
    r"рефактор",
    r"\brefactor\b",
    r"объясни.*(код|функци|скрипт)",
    r"explain.*(code|function|script)",
    r"ошибк.*(код|скрипт|программ)",
    r"почему.*не\s+работ",
    r"why.*(doesn'?t|isn'?t|won'?t)\s+work",
    r"\b(stack\s*trace|traceback)\b",
    r"```",
]

# Weaker/ambiguous phrasings ("что за ошибка", "что не так") that only
# justify code routing when there's separate corroborating code context this
# turn — an attached code/text file, or OCR text extracted from an attached
# screenshot that itself looks code/error-shaped (see
# `looks_like_code_or_error()` below). Kept apart from _CODE_PATTERNS so
# these never fire on a bare chat message with no code context at all (a
# photo of a menu with "что не так с этим?" must not route to the code
# specialist just because OCR happened to run on it).
_AMBIGUOUS_CODE_PATTERNS = [
    r"что\s+за\s+ошибк",
    r"что\s+не\s+так",
    r"в\s+чём\s+(проблема|ошибка)",
    r"прочитай\s+ошибку",
    r"почему\s+ошибк",
]

# Heuristic signals that a block of text (typically OCR output from an
# attached screenshot) is itself code or an error/traceback — deliberately
# permissive but still a real signal, not "any text with punctuation". Used
# only to decide whether `_AMBIGUOUS_CODE_PATTERNS` above should be trusted
# this turn (see `route()`'s `has_code_context` parameter) — never used to
# gate the already-explicit `_CODE_PATTERNS`.
_CODE_LIKE_SIGNALS = re.compile(
    r"(traceback \(most recent call last\)|syntaxerror|typeerror|valueerror|"
    r"nullreferenceexception|exception in thread|null pointer|"
    r"\bdef\s+\w+\(|\bfunction\s+\w+\(|\bclass\s+\w+|\bimport\s+\w+|"
    r"#include|public\s+(?:class|static)|=>|\};|\$\w+\s*=|\bnpm\s+err|"
    r"error.*:\d+:\d+|at\s+\w+(?:\.\w+)+\()",
    re.IGNORECASE,
)


def looks_like_code_or_error(text: str) -> bool:
    """True when `text` (usually OCR output from an attached screenshot)
    itself looks like source code or an error/traceback — see
    `_AMBIGUOUS_CODE_PATTERNS`'s docstring for why this exists."""
    return bool(_CODE_LIKE_SIGNALS.search(text))

# Explicit review/critique request patterns — matches the exact trigger
# phrases named in the Phase 4C/4D spec, kept narrow on purpose so ornith:9b
# never fires on an ordinary answer.
_REVIEW_PATTERNS = [
    r"проведи ревью",
    r"code review",
    r"покритикуй",
    r"найди ошибки",
    r"проверь архитектур",
    r"дай второе мнение",
    r"пусть reviewer проверит",
    r"\breview this\b",
    r"\bcritique\b",
    r"second opinion",
]


@dataclass(frozen=True)
class RoutingDecision:
    model: str
    role: str
    mode: str  # "auto" | "manual_active_chat_model" | "auto_for_code" | "explicit_only"
    # Exactly one of these four canonical values (Phase 4E spec) — not a
    # free-text sentence, so the frontend/logs can branch on it reliably.
    reason: str  # "default_main_chat" | "manual_active_chat_model" | "code_specialist" | "explicit_reviewer"
    is_specialist: bool  # False for main_chat (default or manual-active) — no model_specialist_* events emitted for those turns


def _matches_any(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def route(
    message: str,
    active_chat_model: str | None = None,
    *,
    has_code_context: bool = False,
) -> RoutingDecision:
    """Returns the routing decision for a single /api/chat turn.

    `active_chat_model` — the human-selected "normal chat" model (see
    api/server.py's `_active_chat_model`, set only via POST
    /api/models/active). If it's None, unset, or not one of
    config.ALLOWED_MANUAL_CHAT_MODELS, this function falls back to
    config.MAIN_CHAT_MODEL — it never trusts an unvalidated value, even
    though the only caller today already validates before storing it.

    `has_code_context` — True when this turn already has independent
    evidence of code (an attached code/text file, or OCR text from an
    attached screenshot that itself looks code/error-shaped — see
    `looks_like_code_or_error()`). Only widens matching to
    `_AMBIGUOUS_CODE_PATTERNS` (image/code routing pass, HANDOFF_v2.md) —
    `_CODE_PATTERNS` alone is always enough on its own regardless of this
    flag, so a plain "исправь этот код" still routes correctly with no
    attachment at all.

    This is the ONLY way config.MANUAL_HEAVY_MODEL (qwen3.5:27b) can ever be
    returned from this function — there is no message-content path to it."""
    if active_chat_model in config.ALLOWED_MANUAL_CHAT_MODELS:
        chat_model = active_chat_model
    else:
        chat_model = config.MAIN_CHAT_MODEL

    if not config.ENABLE_MODEL_ROUTER:
        return RoutingDecision(
            model=config.MAIN_CHAT_MODEL,
            role="main_chat",
            mode="auto",
            reason="default_main_chat",
            is_specialist=False,
        )

    if config.ENABLE_REVIEWER_EXPLICIT and _matches_any(message, _REVIEW_PATTERNS):
        return RoutingDecision(
            model=config.REVIEWER_MODEL,
            role="reviewer_critic",
            mode="explicit_only",
            reason="explicit_reviewer",
            is_specialist=True,
        )

    code_intent = _matches_any(message, _CODE_PATTERNS) or (
        has_code_context and _matches_any(message, _AMBIGUOUS_CODE_PATTERNS)
    )
    if config.ENABLE_CODE_SPECIALIST_AUTO and code_intent:
        return RoutingDecision(
            model=config.CODE_MODEL,
            role="code_specialist",
            mode="auto_for_code",
            reason="code_specialist",
            is_specialist=True,
        )

    # Normal chat — either the default main chat model, or a model the human
    # manually activated via POST /api/models/active (which may be
    # MANUAL_HEAVY_MODEL). Either way this is NOT specialist routing.
    if chat_model == config.MAIN_CHAT_MODEL:
        return RoutingDecision(
            model=chat_model, role="main_chat", mode="auto",
            reason="default_main_chat", is_specialist=False,
        )
    return RoutingDecision(
        model=chat_model, role="main_chat", mode="manual_active_chat_model",
        reason="manual_active_chat_model", is_specialist=False,
    )
