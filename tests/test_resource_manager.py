"""Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — unit tests for the
pure/mockable pieces of core/resource_manager.py. Deliberately does NOT
import api.server (that module has heavy import-time side effects —
constructing the Ollama client, loading persisted settings, building the
tool registry — not designed for test isolation), matching every other test
in this directory: test the importable module directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import requests  # noqa: E402

from core import resource_manager as rm  # noqa: E402


# --- _processor_label -------------------------------------------------------

def test_processor_label_all_gpu():
    assert rm._processor_label(1000, 1000) == "100% GPU"
    assert rm._processor_label(1000, 1200) == "100% GPU"  # size_vram >= size, over-report tolerated


def test_processor_label_all_cpu():
    assert rm._processor_label(1000, 0) == "100% CPU"


def test_processor_label_split():
    assert rm._processor_label(1000, 250) == "75%/25% CPU/GPU"


def test_processor_label_unknown_when_size_zero():
    assert rm._processor_label(0, 0) == "unknown"


# --- ollama_process_status ---------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_ollama_process_status_unavailable(monkeypatch):
    def fake_get(url, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(rm.requests, "get", fake_get)
    result = rm.ollama_process_status("http://127.0.0.1:11434")
    assert result["available"] is False
    assert result["models"] == []
    assert "connection refused" in result["error"]


def test_ollama_process_status_available_with_models(monkeypatch):
    payload = {
        "models": [
            {
                "name": "qwen2.5vl:latest",
                "model": "qwen2.5vl:latest",
                "size": 1000,
                "size_vram": 1000,
                "digest": "abc123",
                "expires_at": "2026-07-08T10:00:00Z",
                "context_length": 32768,
            }
        ]
    }
    monkeypatch.setattr(rm.requests, "get", lambda url, timeout: _FakeResponse(payload))
    result = rm.ollama_process_status("http://127.0.0.1:11434")
    assert result["available"] is True
    assert result["error"] is None
    assert len(result["models"]) == 1
    model = result["models"][0]
    assert model["name"] == "qwen2.5vl:latest"
    assert model["processor"] == "100% GPU"
    assert model["context_length"] == 32768


def test_ollama_process_status_survives_malformed_json(monkeypatch):
    class _BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(rm.requests, "get", lambda url, timeout: _BadJsonResponse({}))
    result = rm.ollama_process_status("http://127.0.0.1:11434")
    assert result["available"] is False
    assert result["models"] == []
    assert result["error"] is not None


# --- resolve_unload_targets ---------------------------------------------------

_KNOWN = {"qwen3.5:9b", "qwen3.5:27b", "glm-ocr", "qwen2.5vl", "translategemma:4b", "qwen2.5-coder:7b", "ornith:9b"}
_TOOL_MODELS = ["glm-ocr", "qwen2.5vl", "translategemma:4b", "qwen2.5-coder:7b", "ornith:9b"]


def _resolve(target, model=None, active_chat_model="qwen3.5:9b"):
    return rm.resolve_unload_targets(
        target,
        model,
        known_models=_KNOWN,
        tool_model_names=_TOOL_MODELS,
        manual_heavy_model="qwen3.5:27b",
        active_chat_model=active_chat_model,
    )


def test_resolve_unload_targets_rejects_unknown_target():
    with pytest.raises(ValueError, match="target must be one of"):
        _resolve("everything")


def test_resolve_unload_targets_specific_requires_model():
    with pytest.raises(ValueError, match="model is required"):
        _resolve("specific", model=None)


def test_resolve_unload_targets_specific_rejects_unknown_model():
    with pytest.raises(ValueError, match="not a known model"):
        _resolve("specific", model="totally-made-up-model:1b")


def test_resolve_unload_targets_specific_accepts_known_model():
    assert _resolve("specific", model="glm-ocr") == ["glm-ocr"]


def test_resolve_unload_targets_specific_allows_active_chat_model_explicitly():
    # The one intentional exception: target="specific" naming the active
    # chat model directly IS allowed (explicit human action), unlike
    # tool_models/all_non_chat below which always exclude it.
    assert _resolve("specific", model="qwen3.5:9b", active_chat_model="qwen3.5:9b") == ["qwen3.5:9b"]


def test_resolve_unload_targets_tool_models_excludes_active_chat_model():
    # If someone's active chat model happened to be a "tool" model name,
    # tool_models must never include it.
    result = _resolve("tool_models", active_chat_model="glm-ocr")
    assert "glm-ocr" not in result
    assert set(result) == set(_TOOL_MODELS) - {"glm-ocr"}


def test_resolve_unload_targets_tool_models_normal_case():
    result = _resolve("tool_models", active_chat_model="qwen3.5:9b")
    assert set(result) == set(_TOOL_MODELS)


def test_resolve_unload_targets_all_non_chat_includes_manual_heavy_model():
    result = _resolve("all_non_chat", active_chat_model="qwen3.5:9b")
    assert "qwen3.5:27b" in result
    assert set(result) == set(_TOOL_MODELS) | {"qwen3.5:27b"}


def test_resolve_unload_targets_all_non_chat_excludes_active_chat_model_even_if_manual_heavy():
    result = _resolve("all_non_chat", active_chat_model="qwen3.5:27b")
    assert "qwen3.5:27b" not in result
    assert set(result) == set(_TOOL_MODELS)


def test_resolve_unload_targets_dedupes():
    # Degenerate but defensive: if config.py ever made a tool model equal to
    # MANUAL_HEAVY_MODEL, all_non_chat must not return duplicates.
    result = rm.resolve_unload_targets(
        "all_non_chat",
        None,
        known_models=_KNOWN,
        tool_model_names=["glm-ocr", "qwen3.5:27b"],
        manual_heavy_model="qwen3.5:27b",
        active_chat_model="qwen3.5:9b",
    )
    assert result == ["glm-ocr", "qwen3.5:27b"]


# --- process-detection helpers (psutil mocked) --------------------------------

class _FakeProcess:
    def __init__(self, pid, name, exe, raises=None):
        self.info = {"pid": pid, "name": name, "exe": exe}
        self._raises = raises

    def __getattribute__(self, item):
        if item == "info" and object.__getattribute__(self, "_raises"):
            raise object.__getattribute__(self, "_raises")
        return object.__getattribute__(self, item)


def test_find_processes_by_exe_name_matches_case_insensitively(monkeypatch):
    fake_procs = [
        _FakeProcess(111, "tts-server.exe", r"G:\Siena_v2\external\qwentts.cpp\build\Release\tts-server.exe"),
        _FakeProcess(222, "TTS-SERVER.EXE", r"C:\somewhere\else\tts-server.exe"),
        _FakeProcess(333, "notepad.exe", r"C:\Windows\notepad.exe"),
    ]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found = rm._find_processes_by_exe_name("tts-server.exe")
    assert {p["pid"] for p in found} == {111, 222}


def test_find_processes_by_exe_name_skips_processes_that_raise(monkeypatch):
    import psutil as real_psutil

    fake_procs = [
        _FakeProcess(111, "tts-server.exe", "some/path", raises=real_psutil.NoSuchProcess(111)),
        _FakeProcess(222, "tts-server.exe", "some/other/path"),
    ]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found = rm._find_processes_by_exe_name("tts-server.exe")
    assert [p["pid"] for p in found] == [222]


def test_find_tts_server_processes_wraps_the_generic_scan(monkeypatch):
    fake_procs = [_FakeProcess(555, "tts-server.exe", "path")]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found = rm.find_tts_server_processes(Path("expected/tts-server.exe"))
    assert [p["pid"] for p in found] == [555]


def test_whisper_cli_status_reports_ephemeral_note(monkeypatch):
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter([]))
    result = rm.whisper_cli_status(Path("expected/whisper-cli.exe"))
    assert result["running"] is False
    assert result["pids"] == []
    assert "ephemeral" in result["note"]


def test_whisper_cli_status_detects_lingering_process(monkeypatch):
    fake_procs = [_FakeProcess(777, "whisper-cli.exe", "some/path")]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    result = rm.whisper_cli_status(Path("expected/whisper-cli.exe"))
    assert result["running"] is True
    assert result["pids"] == [777]


# --- tts_server_status (path-matching logic) ----------------------------------

def test_tts_server_status_no_processes_no_port(monkeypatch):
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter([]))
    monkeypatch.setattr(rm, "_port_reachable", lambda host, port, timeout=0.5: False)
    result = rm.tts_server_status(
        managed_by_backend=False,
        expected_exe_path=Path("G:/Siena_v2/external/qwentts.cpp/build/Release/tts-server.exe"),
        host="127.0.0.1",
        port=8080,
    )
    assert result["running"] is False
    assert result["managed_by_backend"] is False
    assert result["pid"] is None
    assert result["expected_path_match"] is None


def test_tts_server_status_external_process_path_mismatch(monkeypatch, tmp_path):
    expected = tmp_path / "expected" / "tts-server.exe"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"")
    other = tmp_path / "elsewhere" / "tts-server.exe"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"")

    fake_procs = [_FakeProcess(999, "tts-server.exe", str(other))]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    monkeypatch.setattr(rm, "_port_reachable", lambda host, port, timeout=0.5: True)

    result = rm.tts_server_status(managed_by_backend=False, expected_exe_path=expected, host="127.0.0.1", port=8080)
    assert result["running"] is True
    assert result["expected_path_match"] is False
    assert result["pid"] == 999  # reported even though it's a mismatch — caller decides what to do


def test_tts_server_status_external_process_path_match(monkeypatch, tmp_path):
    expected = tmp_path / "expected" / "tts-server.exe"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"")

    fake_procs = [_FakeProcess(1000, "tts-server.exe", str(expected))]
    monkeypatch.setattr(rm.psutil, "process_iter", lambda attrs: iter(fake_procs))
    monkeypatch.setattr(rm, "_port_reachable", lambda host, port, timeout=0.5: True)

    result = rm.tts_server_status(managed_by_backend=False, expected_exe_path=expected, host="127.0.0.1", port=8080)
    assert result["expected_path_match"] is True
    assert result["pid"] == 1000
