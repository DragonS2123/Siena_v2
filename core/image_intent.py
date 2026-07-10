"""Detects whether a user's message about an attached image is asking Siena
to read text out of it (OCR) or to describe what it visually shows (vision
— scene/object understanding), or both.

This is a pure text-classification heuristic, same spirit as
core/model_router.py's code/review pattern matching — Runtime doesn't decide
what's "true" about the image, it only signals intent so api/server.py can
route to the right technical service: ocr/glm_ocr_service.py for text
extraction, vision/qwen_vision_service.py for visual description. Neither
service is a substitute for the other, and this module never calls either
one itself.

Bugfix (HANDOFF_v2.md, "image routing order" pass): the original patterns
required near-exact adjacency ("что на картинке" but NOT "что на ЭТОЙ
картинке"), were missing "скриншот"/"скрин" as an image noun entirely, and
were missing common verbs ("посмотри"/"взгляни"/"глянь"/"разбери"). Live
trace showed a real user asking "Что на этом изображении?" — OCR ran,
vision did not — confirmed reproducible: the old
`что\\s+(тут|там|здесь)?\\s*изображ` pattern cannot match through the
demonstrative "этом". Patterns below tolerate a short filler gap instead of
requiring rigid adjacency. Also fixed: `decide_vision()` no longer suppresses
vision just because OCR intent was ALSO detected in the same message (the
old inline `wants_image_understanding(x) and not wants_ocr(x)` in
api/server.py silently broke the "read the text AND describe the picture"
case, since asking for both makes both conditions True and `and not` forces
the whole expression False).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Shared noun alternation — every place that says "the picture"/"the photo"/
# "the screenshot" etc. Kept as one constant so OCR and vision patterns never
# silently drift apart on which image nouns they recognize (this is exactly
# how "скриншот" got missed from vision originally: it existed nowhere in
# this file, not just one list).
_IMAGE_NOUN = r"(картинк|фото|изображени|снимк|скрин)"

# Demonstrative/filler words tolerated between a question word and the image
# noun ("что НА ЭТОЙ картинке", "что тут изображено") — a closed, curated
# set rather than "any characters", specifically so this can't also swallow
# an unrelated verb like "написано" and cross-match into OCR territory (see
# module docstring: that cross-contamination was caught and fixed during
# this pass — an earlier draft used a raw `.{0,12}` gap here and it made
# "что написано на изображении" — an OCR-only request — match as vision
# too).
_FILLER = r"(?:этот|эта|это|этом|этой|эти|этих|тот|та|то|тут|там|здесь)"

_OCR_PATTERNS = [
    r"что\s+(тут\s+|там\s+)?написан",
    r"прочит(ай|ать|ай-ка)",
    r"распозна(й|ть)\s*текст",
    rf"текст\s+.{{0,15}}{_IMAGE_NOUN}",
    r"\bocr\b",
    r"read (the )?text",
    r"what does (it|this|the (image|picture|photo|screenshot)) say",
    r"extract (the )?text",
]

_IMAGE_UNDERSTANDING_PATTERNS = [
    # "что (тут/там/на этом/...) изображ(ено|ает)" — bugfix: was rigid
    # adjacency before this pass, so a demonstrative pronoun ("этом",
    # "этой") in between broke the match entirely (the exact bug reported
    # live: "Что на этом изображении?" ran OCR but never vision).
    rf"что\s+({_FILLER}\s+){{0,2}}изображ",
    rf"что\s+на\s+({_FILLER}\s+){{0,2}}{_IMAGE_NOUN}",
    rf"опиши\s+.{{0,12}}{_IMAGE_NOUN}",
    r"что\s+ты\s+вид(ишь|ел|ела)",
    r"что\s+за\s+(объект|предмет|штука|вещь)",
    rf"что\s+происходит\s+на\s+.{{0,12}}{_IMAGE_NOUN}",
    r"как(ой|ая|ие)\s+объект",
    rf"(расскажи|разбери|проанализируй|анализ)\s*.{{0,15}}{_IMAGE_NOUN}",
    rf"(посмотри|взгляни|глянь)\s+(на\s+)?{_IMAGE_NOUN}",
    r"what('?s| is) in (this|the) (image|picture|photo|screenshot)",
    r"describe (this|the) (image|picture|photo|screenshot)",
    r"what do you see",
    r"what('?s| is) (this|that)( a| an)? (picture|image|photo|screenshot) of",
    r"take a look at (this|the) (image|picture|photo|screenshot)",
]

# Ambiguous short questions with NO explicit OCR/vision keyword at all — only
# meaningful when we already know an image is attached (see decide_vision()
# below). A bare "что это?" in a text-only conversation says nothing about
# images and must never trigger this on its own.
_AMBIGUOUS_IMAGE_QUESTION_PATTERNS = [
    r"^что\s+это\??$",
    r"^что\s+тут\??$",
    r"^что\s+там\??$",
    r"^посмотри\??$",
    r"^взгляни\??$",
    r"^глянь\??$",
    r"^что\s+думаешь\??$",
    r"^what('?s| is) this\??$",
    r"^look at (this|it)\??$",
]


def wants_ocr(text: str) -> bool:
    """True when the user is explicitly asking to read/extract text from an
    attached image (glm-ocr's job)."""
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _OCR_PATTERNS)


def wants_image_understanding(text: str) -> bool:
    """True when the user is asking what an attached image visually shows —
    scene/object description (qwen2.5vl's job), not text reading."""
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _IMAGE_UNDERSTANDING_PATTERNS)


def _is_ambiguous_image_question(text: str) -> bool:
    stripped = text.strip().lower()
    return any(re.fullmatch(pattern, stripped) for pattern in _AMBIGUOUS_IMAGE_QUESTION_PATTERNS)


@dataclass(frozen=True)
class VisionDecision:
    run_vision: bool
    # Informational only (not shown to the model as an excuse-generator —
    # see api/server.py's use of this: it only ever drives whether the
    # honest "vision unavailable" note is attached, never a running
    # "here's why I skipped it" commentary for the ordinary case).
    reason: str  # "no_image" | "explicit_vision" | "explicit_both" | "ocr_only" | "ambiguous_fallback" | "no_intent"


def decide_vision(text: str, has_image_attachment: bool) -> VisionDecision:
    """Single source of truth for "should qwen2.5vl run this turn", replacing
    the old ad-hoc `wants_image_understanding(x) and not wants_ocr(x)` that
    used to live inline in api/server.py in two slightly different places.

    OCR precedence rule: an OCR-only request never triggers vision. But if
    BOTH are explicitly requested ("прочитай текст и опиши картинку"), both
    must run — vision is only suppressed when OCR was asked for and vision
    was NOT (the old code's `and not wants_ocr(text)` incorrectly suppressed
    vision even when both were explicitly requested, since both conditions
    being True made the `and not` collapse to False).

    Ambiguous fallback: a short question with no explicit OCR/vision keyword
    at all ("что это?", "посмотри") defaults to vision when an image is
    actually attached — it's a visual question by construction; there is
    nothing else it could reasonably mean given an attached photo.
    """
    if not has_image_attachment:
        return VisionDecision(False, "no_image")

    ocr = wants_ocr(text)
    vision = wants_image_understanding(text)

    if vision:
        return VisionDecision(True, "explicit_both" if ocr else "explicit_vision")
    if ocr:
        return VisionDecision(False, "ocr_only")
    if _is_ambiguous_image_question(text):
        return VisionDecision(True, "ambiguous_fallback")
    return VisionDecision(False, "no_intent")
