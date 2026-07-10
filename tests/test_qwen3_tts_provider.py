"""Регрессия: voice= override с именем чужого провайдера (например, Silero
speaker "xenia" с фронтенда) не должен ломать synthesize_to_file() — Qwen3-TTS
кидало ValueError на неизвестном speaker'е, и VoiceService откатывался на
Silero на КАЖДОМ вызове, то есть Qwen3-TTS фактически не использовался, если
во фронтенде оставался выбран Silero-голос (см. voice/qwen_tts.py, _KNOWN_SPEAKERS).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from voice.qwen_tts import Qwen3TTSProvider  # noqa: E402
from voice.voice_profiles import VoiceProfile, VoiceProfileStore  # noqa: E402


class _FakeModel:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_custom_voice(self, text, speaker, language=None, instruct=None, **kwargs):
        self.calls.append({"text": text, "speaker": speaker, "language": language, "instruct": instruct})
        return [np.zeros(100, dtype=np.float32)], 24000


def _make_provider(tmp_path, fake_model) -> Qwen3TTSProvider:
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = Qwen3TTSProvider(
        model_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        language="Russian",
        speaker="Vivian",
        instruct="test instruct",
        device="cpu",
        output_dir=output_dir,
        sample_rate=24000,
        logger=None,
    )
    provider._model = fake_model
    provider._loaded_model_repo = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    return provider


def test_unknown_voice_override_is_ignored_not_forwarded(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    result = provider.synthesize_to_file("Привет.", voice="xenia")  # Silero speaker name, not a Qwen one

    assert result["voice"] == "Vivian"  # fell back to the profile's speaker, not "xenia"
    assert fake_model.calls[0]["speaker"] == "Vivian"


def test_known_qwen_speaker_override_is_honored(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    result = provider.synthesize_to_file("Привет.", voice="Serena")

    assert result["voice"] == "Serena"
    assert fake_model.calls[0]["speaker"] == "Serena"


def test_no_override_uses_active_profile_speaker(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    store.update_profile("siena_default_adult", speaker="Dylan")
    fake_model = _FakeModel()
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)

    provider = Qwen3TTSProvider(
        model_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        language="Russian",
        speaker="Vivian",
        instruct="fallback instruct",
        device="cpu",
        output_dir=output_dir,
        sample_rate=24000,
        voice_profile_store=store,
    )
    provider._model = fake_model
    provider._loaded_model_repo = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

    result = provider.synthesize_to_file("Привет.")

    assert result["voice"] == "Dylan"
    assert fake_model.calls[0]["speaker"] == "Dylan"


def test_stage_directions_and_list_markers_are_sanitized_before_synthesis(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    provider.synthesize_to_file("1. *шепчет* Привет.\n2. Пока.")

    sent_text = " ".join(call["text"] for call in fake_model.calls)
    assert "*" not in sent_text
    assert "шепчет" not in sent_text
    assert "1." not in sent_text
    assert "2." not in sent_text
