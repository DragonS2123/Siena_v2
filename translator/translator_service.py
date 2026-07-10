"""Translator — перевод текста через Ollama-модель translategemma:4b (или
резервную qwen3.5:9b). Тот же технический паттерн, что ocr/glm_ocr_service.py
и voice/ (stt.py/tts.py): сервис ТОЛЬКО переводит текст, не решает, нужно ли
это делать — это решение принимает вызывающая сторона (явный флаг
translate=true на attachment/OCR-результате, или явное действие пользователя
через кнопку "Translate"/эндпоинт POST /api/translate — см. api/server.py).

Модель не проверяется при импорте/конструировании — is_available() дёшево
смотрит, числится ли она в Ollama (тот же подход, что в GlmOcrService),
без реального вызова перевода.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import ollama
import requests


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class TranslatorUnavailableError(Exception):
    """Базовый класс сбоя переводчика (инфраструктурный, не смысловой) —
    модель не установлена ИЛИ сам вызов упал. Ловится вызывающей стороной,
    чтобы не ронять chat (см. api/server.py, тот же принцип, что OCR)."""


class TranslatorModelNotInstalledError(TranslatorUnavailableError):
    """Модель не числится в Ollama — вызывающая сторона (api/server.py)
    использует это, чтобы попробовать TRANSLATOR_FALLBACK_MODEL."""


class TranslatorCallFailedError(TranslatorUnavailableError):
    """Модель установлена, но сам вызов перевода упал (таймаут, ошибка
    инференса и т.п.)."""


_LANG_NAMES = {"ru": "Russian", "en": "English"}


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get(code, code)


def _build_prompt(text: str, source_lang: str, target_lang: str, preserve_formatting: bool) -> str:
    target_name = _lang_name(target_lang)
    source_clause = (
        "Detect the source language automatically."
        if source_lang == "auto"
        else f"The source language is {_lang_name(source_lang)}."
    )
    formatting_clause = (
        "Preserve all Markdown/code formatting, line breaks, and structure exactly."
        if preserve_formatting
        else "Formatting does not need to be preserved."
    )
    return (
        f"Translate the following text into {target_name}. {source_clause} {formatting_clause} "
        "Return ONLY the translated text — no commentary, no explanations, no surrounding quotes.\n\n"
        f"{text}"
    )


class TranslatorService:
    def __init__(self, host: str, model: str, timeout: int, logger: _LoggerLike | None = None):
        self._host = host
        self._model = model
        self._timeout = timeout
        self._logger = logger
        self._client = ollama.Client(host=host, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    def is_available(self, model: str | None = None) -> bool:
        """Дёшево смотрит, числится ли модель (по умолчанию self._model,
        либо явный override — нужен, чтобы также проверять fallback-модель)
        среди моделей, которые Ollama реально видит через /api/tags. Не
        запускает реальный перевод ради проверки статуса."""
        target_model = model or self._model
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=2)
            response.raise_for_status()
            names = {m.get("name") for m in response.json().get("models", [])}
        except Exception:
            return False
        return any(n == target_model or (n or "").startswith(f"{target_model}:") for n in names)

    def translate(
        self,
        text: str,
        source_lang: str = "auto",
        target_lang: str = "ru",
        preserve_formatting: bool = True,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Переводит text с source_lang на target_lang. `model` — override на
        этот конкретный вызов (используется api/server.py для fallback:
        сначала config.TRANSLATOR_MODEL, при OcrModelNotInstalledError-аналоге
        — config.TRANSLATOR_FALLBACK_MODEL), аналогично `model` в
        core/ollama_client.py.

        Поднимает TranslatorModelNotInstalledError, если модель не числится в
        Ollama, или TranslatorCallFailedError, если сам вызов упал."""
        target_model = model or self._model
        if not self.is_available(target_model):
            raise TranslatorModelNotInstalledError(
                f"Модель переводчика {target_model!r} не найдена в Ollama ({self._host}). "
                f"Выполните: ollama pull {target_model}"
            )

        prompt = _build_prompt(text, source_lang, target_lang, preserve_formatting)
        start = time.monotonic()
        try:
            response = self._client.chat(
                model=target_model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise TranslatorCallFailedError(f"Перевод через {target_model} не удался: {exc}") from exc

        elapsed_sec = round(time.monotonic() - start, 3)
        result = response.model_dump(exclude_none=True)
        translated = (result.get("message") or {}).get("content", "") or ""
        return {"translated_text": translated.strip(), "elapsed_sec": elapsed_sec, "model": target_model}
