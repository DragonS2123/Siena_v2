"""Тесты FasterQwen3TTSProvider: тот же voice-override контракт, что и у
обычного Qwen3TTSProvider (voice/qwen_tts.py) — неизвестный speaker override
игнорируется, не ломает синтез; плюс проверка, что speaker/language реально
нормализуются в lowercase перед вызовом движка (faster-qwen3-tts
case-sensitive), и что use_chunking=False отправляет текст одним вызовом."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from voice.faster_qwen_tts import FasterQwen3TTSProvider  # noqa: E402
from voice.voice_profiles import VoiceProfileStore  # noqa: E402


class _FakeModel:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_custom_voice(self, text, speaker, language=None, instruct=None, **kwargs):
        self.calls.append({"text": text, "speaker": speaker, "language": language, "instruct": instruct})
        return [np.zeros(100, dtype=np.float32)], 24000


def _make_provider(tmp_path, fake_model, use_chunking=False) -> FasterQwen3TTSProvider:
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = FasterQwen3TTSProvider(
        model_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        language="russian",
        speaker="serena",
        instruct="test instruct",
        device="cpu",
        dtype="bf16",
        output_dir=output_dir,
        sample_rate=24000,
        use_chunking=use_chunking,
        logger=None,
    )
    provider._model = fake_model
    provider._loaded_model_repo = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    return provider


def test_unknown_voice_override_is_ignored_not_forwarded(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    result = provider.synthesize_to_file("Привет.", voice="xenia")  # Silero speaker name, not a Qwen one

    assert result["voice"] == "serena"  # fell back to the profile's speaker, not "xenia"
    assert fake_model.calls[0]["speaker"] == "serena"


def test_known_speaker_override_is_honored_and_lowercased(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    result = provider.synthesize_to_file("Привет.", voice="Dylan")

    assert result["voice"] == "Dylan"  # returned value preserves what was requested
    assert fake_model.calls[0]["speaker"] == "dylan"  # but the engine call is normalized to lowercase


def test_no_override_uses_active_profile_speaker(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    store.update_profile("siena_default_adult", speaker="Uncle_Fu", language="Russian")
    fake_model = _FakeModel()
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)

    provider = FasterQwen3TTSProvider(
        model_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        language="russian",
        speaker="serena",
        instruct="fallback instruct",
        device="cpu",
        dtype="bf16",
        output_dir=output_dir,
        sample_rate=24000,
        voice_profile_store=store,
    )
    provider._model = fake_model
    provider._loaded_model_repo = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

    result = provider.synthesize_to_file("Привет.")

    assert result["voice"] == "Uncle_Fu"
    assert fake_model.calls[0]["speaker"] == "uncle_fu"
    assert fake_model.calls[0]["language"] == "russian"


def test_use_chunking_false_sends_whole_text_in_one_call(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model, use_chunking=False)

    text = "Привет, Максим. Как дела? Всё хорошо!"
    provider.synthesize_to_file(text)

    assert len(fake_model.calls) == 1
    assert fake_model.calls[0]["text"] == text


def test_use_chunking_true_splits_into_multiple_calls(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model, use_chunking=True)

    provider.synthesize_to_file("Привет, Максим. Как дела? Всё хорошо!")

    assert len(fake_model.calls) == 3  # one per sentence, per voice/text_chunking.py


def test_stage_directions_and_list_markers_are_sanitized_before_synthesis(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model, use_chunking=False)

    provider.synthesize_to_file("1. *шепчет* Привет.\n2. Пока.")

    sent_text = fake_model.calls[0]["text"]
    assert "*" not in sent_text
    assert "шепчет" not in sent_text
    assert "1." not in sent_text
    assert "2." not in sent_text
