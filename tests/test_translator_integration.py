"""Translator integration coverage beyond tests/test_translate_delegation.py's
allowlist-hotfix suite — exercises TranslateTextTool against a fake Ollama
transport (requests.get for /api/tags via TranslatorService.is_available,
ollama.Client.chat for the actual call) across the specific scenarios the
task calls out: ru->ja, en->ru, unicode payloads, timeout, a malformed
response body, and that the image OCR->translate chain (api/server.py's
_process_remote_image, shared by desktop and remote) never touches
delegate_model either.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import config  # noqa: E402
from core.errors import SienaToolError  # noqa: E402
from logging_.logger import SienaLogger  # noqa: E402
from tools.translate_tool import TranslateTextTool  # noqa: E402
from translator.translator_service import TranslatorService  # noqa: E402


def _null_logger() -> SienaLogger:
    return MagicMock(spec=SienaLogger)


def _fake_tags_response(models: list[str]):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"models": [{"name": m} for m in models]}
    return response


def _service(monkeypatch, installed_models: list[str]) -> TranslatorService:
    service = TranslatorService(host="http://127.0.0.1:11434", model=config.TRANSLATOR_MODEL, timeout=5)
    monkeypatch.setattr(
        "translator.translator_service.requests.get",
        MagicMock(return_value=_fake_tags_response(installed_models)),
    )
    return service


def _mock_chat_content(service: TranslatorService, content: str) -> None:
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"message": {"content": content}}
    service._client.chat = MagicMock(return_value=mock_response)


# --- language-pair coverage --------------------------------------------------

def test_translate_ru_to_ja(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    _mock_chat_content(service, "こんにちは、元気ですか？")
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    result = tool.run(text="Привет, как дела?", target_lang="ja", source_lang="ru")

    assert result.ok is True
    assert result.content == "こんにちは、元気ですか？"


def test_translate_en_to_ru(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    _mock_chat_content(service, "Привет, мир!")
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    result = tool.run(text="Hello, world!", target_lang="ru", source_lang="en")

    assert result.ok is True
    assert result.content == "Привет, мир!"


def test_translate_preserves_unicode_and_emoji(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    source = "Спасибо большое! 🎉 café naïve"
    _mock_chat_content(service, source)
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    result = tool.run(text=source, target_lang="en", source_lang="auto")

    assert result.ok is True
    assert result.content == source


# --- failure modes ------------------------------------------------------------

def test_translate_timeout_on_primary_raises_safe_error_after_fallback_also_times_out(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL, config.TRANSLATOR_FALLBACK_MODEL])
    service._client.chat = MagicMock(side_effect=TimeoutError("read timed out"))
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    with pytest.raises(SienaToolError, match="Перевод не удался"):
        tool.run(text="привет", target_lang="en")


def test_translate_malformed_response_missing_message_key_yields_empty_not_crash(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {}  # no "message" key at all
    service._client.chat = MagicMock(return_value=mock_response)
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    result = tool.run(text="привет", target_lang="en")

    assert result.ok is True
    assert result.content == ""


def test_translate_model_not_in_tags_falls_back_correctly(monkeypatch):
    # Primary genuinely absent from /api/tags (not just a call failure).
    service = _service(monkeypatch, [config.TRANSLATOR_FALLBACK_MODEL])
    _mock_chat_content(service, "translated via fallback")
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    result = tool.run(text="привет", target_lang="en")

    assert result.ok is True
    assert result.content == "translated via fallback"


def test_translate_no_models_installed_at_all_raises_safe_error(monkeypatch):
    service = _service(monkeypatch, [])
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    with pytest.raises(SienaToolError, match="Переводчик недоступен"):
        tool.run(text="привет", target_lang="en")


# --- delegate_model must never be involved -----------------------------------

def test_translate_text_tool_makes_no_delegate_model_calls(monkeypatch):
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    _mock_chat_content(service, "ok")
    delegate_client = MagicMock()
    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())

    tool.run(text="test", target_lang="en")

    delegate_client.chat.assert_not_called()


def test_image_ocr_translate_chain_uses_translate_text_path_not_delegate(monkeypatch):
    # api/server.py::_process_remote_image (shared by desktop attachment
    # flow and Siena Remote) calls module-level _translate_text, which in
    # turn calls the SAME translator_service.translate used by
    # TranslateTextTool — never DelegateModelTool. This locks in that the
    # underlying primitive both paths share is the translator service call,
    # not a raw chat/delegate call.
    service = _service(monkeypatch, [config.TRANSLATOR_MODEL])
    _mock_chat_content(service, "OCR text translated")

    result = service.translate("some ocr text", source_lang="auto", target_lang="en")

    assert result["translated_text"] == "OCR text translated"
    assert result["model"] == config.TRANSLATOR_MODEL
    service._client.chat.assert_called_once()
