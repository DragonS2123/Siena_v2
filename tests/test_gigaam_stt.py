from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core.errors import SienaInfraError, SienaTimeoutError
from voice.gigaam_stt import GigaAMSTTProvider


@pytest.fixture
def stt(tmp_path, monkeypatch):
    library, model, icd = (tmp_path / n for n in ("libtranscribe.so", "gigaam.gguf", "radv.json"))
    for p in (library, model, icd):
        p.write_bytes(b"fixture")
    monkeypatch.setattr("voice.gigaam_stt.importlib.util.find_spec", lambda _: object())
    return GigaAMSTTProvider(library, model, "0000:03:00.0", "AMD Radeon RX 7900 XTX", icd)


def test_transcribe_exact_gpu_worker_and_result(stt, monkeypatch):
    def run(command, **kwargs):
        assert command[command.index("--device-id") + 1] == "0000:03:00.0"
        assert command[command.index("--expected-gpu") + 1] == "AMD Radeon RX 7900 XTX"
        assert kwargs["env"]["GGML_VK_VISIBLE_DEVICES"] == "0"
        assert "VK_ICD_FILENAMES" not in kwargs["env"]
        assert kwargs["timeout"] == 120
        return SimpleNamespace(returncode=0, stdout=json.dumps({"text": "Привет.", "segments": 1}), stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    result = stt.transcribe_wav("input.wav")
    assert result["text"] == "Привет." and result["backend"] == "vulkan"


@pytest.mark.parametrize("kind", ["timeout", "exit", "malformed", "empty"])
def test_transcription_failures_are_controlled(stt, monkeypatch, kind):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=2 if kind == "exit" else 0,
                               stdout="not json" if kind == "malformed" else '{"text":""}',
                               stderr="expected discrete GPU unavailable")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SienaTimeoutError if kind == "timeout" else SienaInfraError):
        stt.transcribe_wav("input.wav")
    assert len(calls) == 1  # No CPU or iGPU retry.


def test_missing_assets_and_wrong_language(stt):
    assert stt.is_available()
    stt.model_path.unlink()
    assert not stt.is_available() and "GGUF" in stt.unavailable_reason()
    with pytest.raises(SienaInfraError, match="Russian"):
        stt.transcribe_wav("input.wav", "en")
