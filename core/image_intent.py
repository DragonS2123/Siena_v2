"""Pure OCR-versus-vision intent classification for image attachments."""

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
    reason: str  # "no_image" | "explicit_vision" | "explicit_both" | "ocr_only" | "ambiguous_fallback" | "no_intent"


def decide_vision(text: str, has_image_attachment: bool) -> VisionDecision:
    """Choose vision for explicit or ambiguous visual requests with an image."""
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
