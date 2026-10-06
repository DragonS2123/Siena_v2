"""Explicit delegation to a user-assigned specialist model."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from core.errors import SienaToolError
from core.message import ToolResult
from core.ollama_client import OllamaClient
from tools.base import Tool


class DelegateModelTool(Tool):
    name = "delegate_model"
    description = "Delegate a task to the model assigned to coder, reviewer, or memory role."
    parameters = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": ["coder", "reviewer", "memory"]},
            "task": {"type": "string"},
        },
        "required": ["role", "task"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        client_factory: Callable[[str], OllamaClient],
        assignments: Callable[[], dict[str, str]],
        installed_models: Callable[[], set[str]],
        logger: Any | None = None,
    ):
        self._client_factory = client_factory
        self._assignments = assignments
        self._installed_models = installed_models
        self._logger = logger

    def run(self, role: str, task: str) -> ToolResult:
        if role not in {"coder", "reviewer", "memory"}:
            raise SienaToolError(f"unsupported specialist role: {role}")
        model = self._assignments().get(role)
        if not model:
            raise SienaToolError(f"no model assigned to role {role}")
        if model not in self._installed_models():
            raise SienaToolError(f"assigned {role} model is missing from Ollama: {model}")
        started = time.monotonic()
        if self._logger is not None:
            self._logger.event("model.request.started", requested_role=role, resolved_model=model, selection_reason="delegate_tool")
        raw = self._client_factory(model).chat([{"role": "user", "content": task}])
        if self._logger is not None:
            self._logger.event(
                "model.request.completed",
                requested_role=role,
                resolved_model=model,
                ollama_model=str(raw.get("model") or model),
                selection_reason="delegate_tool",
                done_reason=raw.get("done_reason"),
                generated_tokens=int(raw.get("eval_count") or 0),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
        content = (raw.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise SienaToolError(f"{role} model returned no text")
        return ToolResult(ok=True, content={"role": role, "model": model, "text": content})
