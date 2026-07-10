"""delegate_model(model, task) — делегировать узкую задачу другой модели через Ollama.

Runtime не решает, какая задача "нуждается" в делегировании — это решает только
PRIMARY_MODEL, вызывая этот tool явно. Единственная проверка здесь техническая:
запрошенное имя модели должно быть в config.DELEGATE_MODELS (модель существует
и сконфигурирована), а не смысловая оценка задачи (ARCHITECTURE.md, раздел 12).

Делегируемая модель никогда не отвечает пользователю напрямую — её текстовый
ответ возвращается PRIMARY_MODEL как обычный ToolResult, и именно PRIMARY_MODEL
формирует финальный ответ.
"""

from __future__ import annotations

import time

from core.errors import SienaInfraError, SienaToolError
from core.message import ToolResult
from core.ollama_client import OllamaClient
from logging_.logger import SienaLogger
from tools.base import Tool


class DelegateModelTool(Tool):
    name = "delegate_model"
    description = (
        "Делегировать узкую специализированную задачу другой модели (например, генерацию/анализ/"
        "рефакторинг кода) и получить её ответ как материал для собственного финального ответа. "
        "Ты никогда не пересылаешь результат этого вызова пользователю напрямую — сама проверяешь "
        "его и формируешь окончательный ответ."
    )
    parameters = {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "Имя модели-исполнителя, например qwen2.5-coder:7b"},
            "task": {"type": "string", "description": "Полная самодостаточная формулировка задачи для модели-исполнителя"},
        },
        "required": ["model", "task"],
    }

    def __init__(
        self,
        ollama_client: OllamaClient,
        allowed_models: dict[str, str],
        logger: SienaLogger,
        primary_model_name: str,
    ):
        self._ollama_client = ollama_client
        self._allowed_models = allowed_models
        self._logger = logger
        self._primary_model_name = primary_model_name

    def run(self, model: str, task: str) -> ToolResult:
        if model not in self._allowed_models:
            raise SienaToolError(
                f"Неизвестная модель для делегирования: {model!r}. "
                f"Доступны: {', '.join(self._allowed_models) or '(список пуст)'}"
            )

        self._logger.event(
            "model_delegate",
            **{"from": self._primary_model_name, "to": model, "task": task},
            console_message=f"[DELEGATE] {self._primary_model_name} -> {model}: {task}",
        )

        start = time.monotonic()
        try:
            raw = self._ollama_client.chat(messages=[{"role": "user", "content": task}], model=model)
        except SienaInfraError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._logger.event(
                "model_delegate_result",
                model=model,
                duration_ms=duration_ms,
                tokens=None,
                ok=False,
                console_message=f"[DELEGATE] {model} недоступна ({duration_ms} мс)",
            )
            # Недоступность delegate-модели — модель PRIMARY_MODEL остаётся доступной
            # и может сама решить, что делать дальше (повторить, ответить без
            # делегирования) — это не инфраструктурный сбой Runtime (раздел 7.1).
            raise SienaToolError(f"Делегируемая модель {model!r} недоступна: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        message = raw.get("message", {})
        content = message.get("content", "")
        tokens = raw.get("eval_count")

        self._logger.event(
            "model_delegate_result",
            model=model,
            duration_ms=duration_ms,
            tokens=tokens,
            ok=True,
            console_message=f"[DELEGATE] {model} ответила за {duration_ms} мс ({tokens} tokens)",
        )

        return ToolResult(ok=True, content=content)
