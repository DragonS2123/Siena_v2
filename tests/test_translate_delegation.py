"""Hotfix: translator delegation allowlist.

Root cause — Siena's SYSTEM_PROMPT told the model to "send the task to
translategemma-strict:4b" for any translation request, but the only
model-callable mechanism that accepted an arbitrary model name was
tools/delegate_model.py, whose allowlist (config.DELEGATE_MODELS) only ever
contained config.CODE_MODEL. Every translation attempt therefore hit
"Неизвестная модель для делегирования: 'translategemma-strict:4b'" even
though the model is installed and working in Ollama directly.

Fix: a dedicated `translate_text` tool (tools/translate_tool.py) that routes
through the existing translator/translator_service.py (proper prompt
building, source/target language handling, and its own primary -> fallback
model dance) instead of a raw delegate_model chat call. delegate_model's
allowlist is deliberately left untouched (still only config.CODE_MODEL) —
this suite locks that in, plus that config.MANUAL_HEAVY_MODEL never sneaks
into it.

Both test modules are imported directly (no api.server/main import) — same
"test the importable module directly" convention as
tests/test_resource_manager.py, since importing api.server has heavy
import-time side effects not designed for test isolation.
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
from tools.delegate_model import DelegateModelTool  # noqa: E402
from tools.translate_tool import TranslateTextTool  # noqa: E402
from translator.translator_service import TranslatorCallFailedError, TranslatorService  # noqa: E402


def _null_logger() -> SienaLogger:
    return MagicMock(spec=SienaLogger)


# --- config.DELEGATE_MODELS allowlist ---------------------------------------

def test_delegate_models_still_contains_code_model():
    assert config.CODE_MODEL in config.DELEGATE_MODELS


def test_delegate_models_does_not_contain_translator_model():
    # The hotfix's whole point: translation is NOT reachable through
    # delegate_model's raw chat call — it goes through translate_text
    # instead (see TranslatorService below), which uses the real prompt/
    # fallback logic instead of a bare chat message.
    assert config.TRANSLATOR_MODEL not in config.DELEGATE_MODELS


def test_delegate_models_never_contains_manual_heavy_model():
    # qwen3.5:27b must never become reachable via automatic/model-initiated
    # delegation — the only path to it is the human-only
    # POST /api/models/active (see core/model_router.py's own docstring).
    assert config.MANUAL_HEAVY_MODEL not in config.DELEGATE_MODELS


# --- DelegateModelTool (unchanged behavior) ---------------------------------

def test_delegate_model_code_model_still_works():
    fake_client = MagicMock()
    fake_client.chat.return_value = {"message": {"content": "def foo(): pass"}, "eval_count": 12}
    tool = DelegateModelTool(fake_client, config.DELEGATE_MODELS, _null_logger(), config.PRIMARY_MODEL)

    result = tool.run(model=config.CODE_MODEL, task="write a no-op function")

    assert result.ok is True
    assert result.content == "def foo(): pass"
    fake_client.chat.assert_called_once_with(
        messages=[{"role": "user", "content": "write a no-op function"}], model=config.CODE_MODEL,
    )


def test_delegate_model_unknown_model_returns_safe_error():
    fake_client = MagicMock()
    tool = DelegateModelTool(fake_client, config.DELEGATE_MODELS, _null_logger(), config.PRIMARY_MODEL)

    with pytest.raises(SienaToolError, match="Неизвестная модель для делегирования"):
        tool.run(model="translategemma-strict:4b", task="переведи текст")
    fake_client.chat.assert_not_called()


def test_delegate_model_rejects_manual_heavy_model():
    fake_client = MagicMock()
    tool = DelegateModelTool(fake_client, config.DELEGATE_MODELS, _null_logger(), config.PRIMARY_MODEL)

    with pytest.raises(SienaToolError, match="Неизвестная модель"):
        tool.run(model=config.MANUAL_HEAVY_MODEL, task="think really hard")
    fake_client.chat.assert_not_called()


# --- TranslateTextTool (the fix) --------------------------------------------

def _service_with_mocked_ollama(monkeypatch) -> TranslatorService:
    service = TranslatorService(host="http://127.0.0.1:11434", model=config.TRANSLATOR_MODEL, timeout=5)
    return service


def test_translate_text_success_with_mocked_ollama(monkeypatch):
    service = _service_with_mocked_ollama(monkeypatch)
    monkeypatch.setattr(service, "is_available", lambda model=None: True)
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"message": {"content": "Guys, please help."}}
    monkeypatch.setattr(service._client, "chat", MagicMock(return_value=mock_response))

    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())
    result = tool.run(text="Ребят помогите пожалуйста", target_lang="en", source_lang="auto")

    assert result.ok is True
    assert result.content == "Guys, please help."


def test_translate_text_falls_back_when_primary_not_installed(monkeypatch):
    service = _service_with_mocked_ollama(monkeypatch)

    def fake_is_available(model=None):
        # Primary model not installed; fallback is.
        return model == config.TRANSLATOR_FALLBACK_MODEL

    monkeypatch.setattr(service, "is_available", fake_is_available)
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"message": {"content": "Fallback translation"}}
    monkeypatch.setattr(service._client, "chat", MagicMock(return_value=mock_response))

    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())
    result = tool.run(text="привет", target_lang="en")

    assert result.ok is True
    assert result.content == "Fallback translation"


def test_translate_text_raises_safe_error_when_both_models_unavailable(monkeypatch):
    service = _service_with_mocked_ollama(monkeypatch)
    monkeypatch.setattr(service, "is_available", lambda model=None: False)

    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())
    with pytest.raises(SienaToolError, match="Переводчик недоступен"):
        tool.run(text="привет", target_lang="en")


def test_translate_text_raises_safe_error_on_call_failure(monkeypatch):
    service = _service_with_mocked_ollama(monkeypatch)
    monkeypatch.setattr(service, "is_available", lambda model=None: True)
    monkeypatch.setattr(service._client, "chat", MagicMock(side_effect=RuntimeError("boom")))

    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())
    with pytest.raises(SienaToolError, match="Перевод не удался"):
        tool.run(text="привет", target_lang="en")


def test_translate_text_tool_never_touches_delegate_allowlist(monkeypatch):
    # Defense in depth: the translate tool must not accept/consult
    # config.DELEGATE_MODELS at all — it's a fully separate code path.
    service = _service_with_mocked_ollama(monkeypatch)
    monkeypatch.setattr(service, "is_available", lambda model=None: True)
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"message": {"content": "ok"}}
    monkeypatch.setattr(service._client, "chat", MagicMock(return_value=mock_response))

    tool = TranslateTextTool(service, config.TRANSLATOR_FALLBACK_MODEL, _null_logger())
    assert not hasattr(tool, "_allowed_models")
    result = tool.run(text="x", target_lang="en")
    assert result.ok is True


# --- registration in main.py::build_registry --------------------------------

def test_build_registry_registers_translate_text_when_enabled(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(config, "ENABLE_TRANSLATOR", True)
    logger = SienaLogger(tmp_path)
    registry, _, _, _ = main.build_registry(logger)
    assert "translate_text" in registry.names()
    assert "delegate_model" in registry.names()  # unaffected by this change


def test_build_registry_omits_translate_text_when_disabled(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(config, "ENABLE_TRANSLATOR", False)
    logger = SienaLogger(tmp_path)
    registry, _, _, _ = main.build_registry(logger)
    assert "translate_text" not in registry.names()
    assert "delegate_model" in registry.names()
