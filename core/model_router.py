"""Deterministic model-role routing with explicit-mode precedence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html", ".htm",
    ".java", ".js", ".jsx", ".json", ".kt", ".lua", ".md", ".php", ".ps1",
    ".py", ".rb", ".rs", ".sh", ".sql", ".svelte", ".swift", ".ts", ".tsx",
    ".vue", ".xml", ".yaml", ".yml",
}

CODE_PATTERNS = (
    r"\b(?:write|create|generate|fix|debug|refactor|review|analy[sz]e|add)\b.{0,40}"
    r"\b(?:code|script|function|class|html|css|javascript|typescript|python|c#|file|bug|error)\b",
    r"\b(?:напиши|создай|сгенерируй|исправь|почини|отрефактори|проверь|проанализируй|добавь)\b"
    r".{0,50}\b(?:код|скрипт|функци|класс|html|css|javascript|typescript|python|c#|файл|ошибк|калькулятор)\b",
    r"\b(?:stack trace|traceback|syntaxerror|typeerror|compile error|исключение|стектрейс)\b",
)


@dataclass(frozen=True)
class ModelSelection:
    requested_role: str
    resolved_model: str
    explicit_mode: str
    selection_reason: str
    override_source: str | None
    coding_intent: bool
    settings_revision: int

    def metadata(self) -> dict[str, Any]:
        return {
            "requested_role": self.requested_role,
            "resolved_model": self.resolved_model,
            "explicit_mode": self.explicit_mode,
            "selection_reason": self.selection_reason,
            "override_source": self.override_source,
            "coding_intent": self.coding_intent,
            "settings_revision": self.settings_revision,
        }


def is_coding_intent(
    text: str,
    attachments: Iterable[dict[str, Any]] = (),
) -> bool:
    lowered = text.casefold()
    if "```" in text:
        return True
    if any(re.search(pattern, lowered, re.IGNORECASE | re.DOTALL) for pattern in CODE_PATTERNS):
        return True
    for attachment in attachments:
        kind = str(attachment.get("type") or "").casefold()
        name = str(attachment.get("name") or "")
        if kind in {"code", "json", "markdown", "log"} or Path(name).suffix.casefold() in CODE_EXTENSIONS:
            return True
    return False


def resolve_model(
    *,
    text: str,
    attachments: Iterable[dict[str, Any]],
    explicit_mode: str,
    request_override: str | None,
    conversation_override: str | None,
    roles: dict[str, str],
    code_auto_enabled: bool,
    settings_revision: int,
) -> ModelSelection:
    mode = explicit_mode if explicit_mode in {"auto", "chat", "code", "deep"} else "auto"
    coding = is_coding_intent(text, attachments)
    if request_override:
        role = "deep" if mode == "deep" else "coder" if mode == "code" else "chat"
        return ModelSelection(
            role, request_override, mode, "request_override", "request", coding, settings_revision
        )
    if conversation_override:
        role = "deep" if mode == "deep" else "coder" if mode == "code" or (mode == "auto" and coding) else "chat"
        return ModelSelection(
            role, conversation_override, mode, "conversation_override", "conversation", coding, settings_revision
        )
    if mode == "deep":
        role, reason = "deep", "explicit_deep_mode"
    elif mode == "code":
        role, reason = "coder", "explicit_code_mode"
    elif mode == "chat":
        role, reason = "chat", "explicit_chat_mode"
    elif coding and code_auto_enabled:
        role, reason = "coder", "coding_intent"
    else:
        role, reason = "chat", "default_chat"
    return ModelSelection(role, roles[role], mode, reason, None, coding, settings_revision)
