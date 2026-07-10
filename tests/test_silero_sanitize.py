"""Проверяет, что SileroTTSProvider.synthesize_to_file() применяет
voice/text_sanitize.py перед синтезом (см. tests/test_qwen3_tts_provider.py,
tests/test_faster_qwen3_tts_provider.py за тем же для остальных провайдеров)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from voice.tts import SileroTTSProvider  # noqa: E402


class _FakeModel:
    def __init__(self):
        self.calls: list[dict] = []

    def apply_tts(self, text, speaker, sample_rate):
        self.calls.append({"text": text, "speaker": speaker, "sample_rate": sample_rate})
        return np.zeros(100, dtype=np.float32)


def _make_provider(tmp_path, fake_model) -> SileroTTSProvider:
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = SileroTTSProvider(
        language="ru",
        model_id="v3_1_ru",
        speaker="baya",
        device="cpu",
        output_dir=output_dir,
        sample_rate=48000,
        models_dir=tmp_path / "models",
        logger=None,
    )
    provider._model = fake_model
    return provider


def test_stage_directions_and_list_markers_are_sanitized_before_synthesis(tmp_path):
    fake_model = _FakeModel()
    provider = _make_provider(tmp_path, fake_model)

    provider.synthesize_to_file("1. *шепчет* Привет.\n2. Пока.")

    sent_text = " ".join(call["text"] for call in fake_model.calls)
    assert "*" not in sent_text
    assert "шепчет" not in sent_text
    assert "1." not in sent_text
    assert "2." not in sent_text


def test_strip_all_numbers_flag_applies_to_silero(tmp_path):
    fake_model = _FakeModel()
    output_dir = tmp_path / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = SileroTTSProvider(
        language="ru", model_id="v3_1_ru", speaker="baya", device="cpu",
        output_dir=output_dir, sample_rate=48000, models_dir=tmp_path / "models",
        strip_all_numbers=True, logger=None,
    )
    provider._model = fake_model

    provider.synthesize_to_file("У меня 3 кота.")

    sent_text = " ".join(call["text"] for call in fake_model.calls)
    assert "3" not in sent_text
