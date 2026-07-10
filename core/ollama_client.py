"""Обёртка над Ollama Chat API. Только транспорт — никакой бизнес-логики.

Runtime не решает, вызывать ли модель с tools или без — agent_loop всегда
передаёт полный список доступных инструментов, а решение об их использовании
целиком остаётся за моделью.
"""

from __future__ import annotations

import ollama

from core.errors import SienaInfraError


class OllamaClient:
    def __init__(
        self,
        host: str,
        model: str,
        timeout: int,
        think: bool = False,
        num_ctx: int | None = None,
        num_predict: int | None = None,
    ):
        self._client = ollama.Client(host=host, timeout=timeout)
        self._host = host
        self._model = model
        self._think = think
        self._num_ctx = num_ctx
        self._num_predict = num_predict

    @property
    def num_ctx(self) -> int | None:
        return self._num_ctx

    @property
    def num_predict(self) -> int | None:
        return self._num_predict

    def chat(self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
        """Возвращает ПОЛНЫЙ сырой ответ Ollama как plain dict: model, created_at, done,
        done_reason, timing-поля и message (role/content/tool_calls/thinking).

        Полный ответ, а не только message, нужен для диагностики: например, чтобы
        отличить "модель осознанно ответила пустой строкой" от "ответ был обрезан"
        (done_reason != "stop") — это видно только в сырых полях (ARCHITECTURE.md,
        раздел 8 — полная трассировка каждого шага).

        `model` — необязательный override модели на этот конкретный вызов (используется
        только tools/delegate_model.py; agent_loop.py его никогда не передаёт и всегда
        говорит с моделью, заданной при конструировании клиента — ARCHITECTURE.md §6/§12).

        `num_ctx`/`num_predict` — технические параметры транспорта (окно контекста и
        предел генерации), заданные при конструировании клиента (см. config.py,
        DIAGNOSIS_CONTEXT_OVERFLOW.md). Без них Ollama использует свой дефолт num_ctx=4096,
        что и было причиной пустых ответов при разросшейся истории.

        Поднимает SienaInfraError при недоступности Ollama — это инфраструктурный
        сбой Runtime, спрашивать модель не у кого (ARCHITECTURE.md, раздел 7.3).
        """
        target_model = model or self._model
        options: dict = {}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict

        try:
            response = self._client.chat(
                model=target_model,
                messages=messages,
                tools=tools or None,
                think=self._think,
                options=options or None,
            )
        except Exception as exc:
            raise SienaInfraError(
                f"Ollama недоступен (host={self._host}, model={target_model}): {exc}"
            ) from exc

        return response.model_dump(exclude_none=True)
