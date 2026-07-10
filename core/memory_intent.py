"""Detects whether a user's message looks like an explicit request to save
something to long-term memory ("запомни", "сохрани", "добавь в память",
"добавь что...", "запиши").

Same spirit as core/model_router.py / core/image_intent.py: a lightweight
regex heuristic, not a decision. Runtime still doesn't decide WHAT to save or
whether to save it — the model does, by calling long_memory_save. This only
produces a diagnostic trace event (memory_save_intent_detected) and a soft
in-context reminder for that turn, making the cue harder for the model to
miss than plain system-prompt instructions alone (see api/server.py::chat).
"""

from __future__ import annotations

import re

_MEMORY_SAVE_PATTERNS = [
    r"запомни",
    r"сохрани",
    r"добавь\s+в\s+(долговрем\w*|памят\w*)",
    r"добавь\s+что",
    r"запиши",
    r"оставь\s+в\s+памят\w*",
]


def wants_long_memory_save(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _MEMORY_SAVE_PATTERNS)
