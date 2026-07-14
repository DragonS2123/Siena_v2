import json
import re


class InvalidReactionResponse(ValueError):
    pass


class ReactionResponseSanitizer:
    _THINK = re.compile(r"<think>.*?</think>|<reasoning>.*?</reasoning>", re.IGNORECASE | re.DOTALL)
    _PREFIX = re.compile(r"^(?:final answer|answer|ответ|assistant|siena)\s*:\s*", re.IGNORECASE)

    def __init__(self, max_chars: int = 320) -> None:
        self.max_chars = max_chars

    def sanitize(self, text: str, reasoning: str | None = None) -> str:
        value = self._THINK.sub(" ", text or "")
        value = re.sub(r"```(?:\w+)?\s*", "", value).replace("```", "")
        value = self._PREFIX.sub("", value.strip())
        value = re.sub(r"[*_#>`]", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise InvalidReactionResponse("Siena Core returned empty reaction text")
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            raise InvalidReactionResponse("Siena Core returned technical JSON instead of reaction text")
        if len(value) > self.max_chars:
            cut = value[: self.max_chars + 1]
            boundary = cut.rfind(" ", 0, self.max_chars + 1)
            value = cut[: boundary if boundary > self.max_chars // 2 else self.max_chars].rstrip(" ,;:-")
        if not value:
            raise InvalidReactionResponse("Siena Core reaction became empty after sanitization")
        return value
