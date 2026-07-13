"""translate_text(text, target_lang, source_lang) — переводит текст через
translator/translator_service.py (config.TRANSLATOR_MODEL, с fallback на
config.TRANSLATOR_FALLBACK_MODEL, если основной переводчик не установлен в
Ollama). Тот же fallback-паттерн, что и api/server.py::_translate_text
(используемый POST /api/translate и attachment translate=True), но
самостоятельный — не переиспользует ту функцию напрямую, т.к. она принимает
per-request BroadcastLogger и живёт в api/server.py, тогда как этот tool
конструируется в main.py::build_registry() с обычным SienaLogger-совместимым
логгером, как остальные tools.

Существует именно для того, чтобы PRIMARY_MODEL никогда не переводила текст
через delegate_model (сырой chat-вызов без языковых промптов/preserve-
formatting/fallback) — см. SYSTEM_PROMPT, раздел "Translation": явный запрос
на перевод должен вызывать этот tool, а не delegate_model.
"""

from __future__ import annotations

import time

from core.errors import SienaToolError
from core.message import ToolResult
from logging_.logger import SienaLogger
from translator.translator_service import (
    TranslatorCallFailedError,
    TranslatorModelNotInstalledError,
    TranslatorService,
)
from tools.base import Tool


class TranslateTextTool(Tool):
    name = "translate_text"
    description = (
        "Перевести текст через специализированный сервис перевода (не через себя и не через "
        "delegate_model). Используй этот tool для ЛЮБОГО явного запроса пользователя на перевод "
        "(«переведи», «translate», «на английский» и т.п.) — никогда не переводи текст сама и "
        "никогда не вызывай delegate_model для перевода."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Текст для перевода"},
            "target_lang": {
                "type": "string",
                "description": "Язык назначения перевода, например 'en' или 'ru'",
            },
            "source_lang": {
                "type": "string",
                "description": "Язык исходного текста, или 'auto' для автоопределения (по умолчанию)",
            },
        },
        "required": ["text", "target_lang"],
    }

    def __init__(self, translator_service: TranslatorService, fallback_model: str, logger: SienaLogger):
        self._translator_service = translator_service
        self._fallback_model = fallback_model
        self._logger = logger

    def run(self, text: str, target_lang: str, source_lang: str = "auto") -> ToolResult:
        primary_model = self._translator_service.model
        # Safe diagnostics only: char counts and language codes, never the
        # source/translated text itself (see module docstring + task's
        # translator_failed safety requirement).
        self._logger.event(
            "translator_requested",
            source_lang=source_lang,
            target_lang=target_lang,
            chars=len(text),
        )
        self._logger.event("translator_model_resolved", model=primary_model)

        start = time.monotonic()
        self._logger.event("translator_started", model=primary_model)
        try:
            result = self._translator_service.translate(text, source_lang=source_lang, target_lang=target_lang)
        except TranslatorModelNotInstalledError:
            self._logger.event(
                "translator_tool_fallback",
                primary=primary_model,
                fallback=self._fallback_model,
                console_message=f"[TRANSLATE] {primary_model} недоступна, пробую {self._fallback_model}",
            )
            self._logger.event("translator_started", model=self._fallback_model)
            try:
                result = self._translator_service.translate(
                    text, source_lang=source_lang, target_lang=target_lang, model=self._fallback_model,
                )
            except TranslatorModelNotInstalledError as fallback_exc:
                elapsed_sec = round(time.monotonic() - start, 3)
                self._logger.error(
                    "translator_failed",
                    safe_error_code="translator_unavailable",
                    model=self._fallback_model,
                    elapsed_sec=elapsed_sec,
                    console_message=f"[TRANSLATE] both models unavailable ({elapsed_sec}s)",
                )
                raise SienaToolError(f"Переводчик недоступен: {fallback_exc}") from fallback_exc
            except TranslatorCallFailedError as fallback_exc:
                elapsed_sec = round(time.monotonic() - start, 3)
                self._logger.error(
                    "translator_failed",
                    safe_error_code="translator_call_failed",
                    model=self._fallback_model,
                    elapsed_sec=elapsed_sec,
                    console_message=f"[TRANSLATE] fallback call failed ({elapsed_sec}s)",
                )
                raise SienaToolError(f"Перевод не удался: {fallback_exc}") from fallback_exc
        except TranslatorCallFailedError as exc:
            elapsed_sec = round(time.monotonic() - start, 3)
            self._logger.error(
                "translator_failed",
                safe_error_code="translator_call_failed",
                model=primary_model,
                elapsed_sec=elapsed_sec,
                console_message=f"[TRANSLATE] call failed ({elapsed_sec}s)",
            )
            raise SienaToolError(f"Перевод не удался: {exc}") from exc

        elapsed_sec = round(time.monotonic() - start, 3)
        self._logger.event(
            "translator_completed",
            model=result["model"],
            elapsed_sec=elapsed_sec,
            chars=len(result["translated_text"]),
            console_message=f"[TRANSLATE] {result['model']} за {elapsed_sec}с",
        )
        return ToolResult(ok=True, content=result["translated_text"])
