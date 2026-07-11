"""Computer Awareness Layer (0.2.3, Phase 1) — read-only computer state.

Covers the full task checklist: endpoint behavior (status/summary/warnings,
disabled-safe payloads, no-500-on-collection-crash), privacy gates
(active_window_title default-off, process list / disks / network respect
their allow_* settings), warning thresholds with mocked metrics, chat
context injection intent (positive + negative fixtures +
allow_computer_context_in_chat=false), settings defaults/persistence, and
the structural read-only guarantee (no non-GET /api/computer/* routes, no
subprocess usage in the computer/ package beyond the pre-existing
nvidia-smi reuse in core/system_metrics.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from computer.computer_context import build_computer_context, wants_computer_context  # noqa: E402
from computer.computer_service import ComputerService, ComputerSettings  # noqa: E402
from storage.settings_store import SettingsStore  # noqa: E402


@pytest.fixture(autouse=True)
def _computer_defaults(monkeypatch):
    monkeypatch.setattr(server.config, "ENABLE_COMPUTER_AWARENESS", True)
    monkeypatch.setattr(server.config, "SHOW_COMPUTER_STATUS_CARD", True)
    monkeypatch.setattr(server.config, "COMPUTER_STATUS_POLL_SECONDS", 10)
    monkeypatch.setattr(server.config, "ALLOW_ACTIVE_WINDOW_TITLE", False)
    monkeypatch.setattr(server.config, "ALLOW_PROCESS_LIST", True)
    monkeypatch.setattr(server.config, "ALLOW_DISK_STATUS", True)
    monkeypatch.setattr(server.config, "ALLOW_NETWORK_STATUS", True)
    monkeypatch.setattr(server.config, "ALLOW_COMPUTER_CONTEXT_IN_CHAT", True)
    monkeypatch.setattr(server.config, "COMPUTER_WARNING_CPU_PERCENT", 90)
    monkeypatch.setattr(server.config, "COMPUTER_WARNING_RAM_PERCENT", 85)
    monkeypatch.setattr(server.config, "COMPUTER_WARNING_VRAM_PERCENT", 90)
    monkeypatch.setattr(server.config, "COMPUTER_WARNING_DISK_FREE_GB", 10)
    # Endpoint tests must not depend on live Ollama/TTS/STT on this machine.
    monkeypatch.setattr(
        server, "computer_service",
        ComputerService(
            ollama_status_provider=lambda: {"connected": True, "models": []},
            tts_status_provider=lambda: {"provider": "test_tts", "status": "online"},
            stt_status_provider=lambda: {"provider": "test_stt", "available": True, "reason": None},
        ),
    )


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(server, "settings_store", SettingsStore(tmp_path / "settings.json"))
    return TestClient(server.app)


def _settings(**overrides) -> ComputerSettings:
    return ComputerSettings(**overrides)


def _fake_service(**provider_overrides) -> ComputerService:
    providers = dict(
        ollama_status_provider=lambda: {"connected": True, "models": []},
        tts_status_provider=lambda: {"provider": "test_tts", "status": "online"},
        stt_status_provider=lambda: {"provider": "test_stt", "available": True, "reason": None},
    )
    providers.update(provider_overrides)
    return ComputerService(**providers)


# --- endpoints -----------------------------------------------------------------

def test_computer_status_endpoint_works(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/computer/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["status"] == "ok"
    assert body["backend_status"] == "online"
    assert isinstance(body["cpu_percent"], (int, float))
    assert isinstance(body["ram_percent"], (int, float))
    assert isinstance(body["warnings"], list)
    assert body["collected_at"]


def test_computer_summary_endpoint_works(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/computer/summary").json()
    assert body["enabled"] is True
    assert isinstance(body["summary"], str) and body["summary"]
    assert "code" in body and "warning_count" in body


def test_computer_warnings_endpoint_works(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/computer/warnings").json()
    assert body["enabled"] is True
    assert isinstance(body["warnings"], list)


def test_disabled_layer_returns_safe_status(monkeypatch, tmp_path):
    monkeypatch.setattr(server.config, "ENABLE_COMPUTER_AWARENESS", False)
    client = _client(monkeypatch, tmp_path)

    status = client.get("/api/computer/status").json()
    assert status == {"enabled": False, "status": "disabled", "warnings": []}

    summary = client.get("/api/computer/summary").json()
    assert summary["enabled"] is False
    assert summary["code"] == "disabled"

    warnings = client.get("/api/computer/warnings").json()
    assert warnings["enabled"] is False
    assert warnings["warnings"] == []


def test_collection_crash_returns_safe_error_not_500(monkeypatch, tmp_path):
    def _boom(_settings):
        raise RuntimeError("simulated collection crash")

    monkeypatch.setattr(server.computer_service, "collect", _boom)
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/computer/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["warnings"] == []


def test_no_command_execution_endpoints_exist():
    # Structural read-only guarantee: every /api/computer/* route is GET-only,
    # and there is no execute/run/kill/repair route anywhere near it.
    for route in server.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/computer"):
            assert getattr(route, "methods", set()) <= {"GET", "HEAD"}, path
        assert not any(word in path for word in ("/execute", "/run_command", "/kill", "/repair")), path


def test_computer_package_contains_no_command_execution():
    # The whole computer/ package must not spawn anything itself — its only
    # subprocess is the pre-existing read-only nvidia-smi query it reuses
    # from core/system_metrics.py. Checked against actual import/call
    # patterns (docstrings legitimately mention the word "subprocess" while
    # documenting this exact guarantee).
    forbidden = (
        "import subprocess", "from subprocess", "os.system(",
        "Popen(", "check_output(", "check_call(", "spawn(",
    )
    package_dir = Path(__file__).resolve().parent.parent / "computer"
    for source_file in package_dir.glob("*.py"):
        source = source_file.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in source, f"{source_file}: {pattern}"


# --- privacy gates ---------------------------------------------------------------

def test_active_window_title_hidden_by_default():
    service = _fake_service()
    state = service.collect(_settings())  # defaults: allow_active_window_title=False
    assert state.active_window_title is None


def test_active_window_title_only_when_allowed(monkeypatch):
    import computer.computer_service as mod

    monkeypatch.setattr(mod, "_read_active_window_title", lambda: "Secret Document — Editor")
    service = _fake_service()

    assert service.collect(_settings(allow_active_window_title=False)).active_window_title is None
    assert service.collect(_settings(allow_active_window_title=True)).active_window_title == "Secret Document — Editor"


def test_process_list_respects_allow_setting():
    service = _fake_service()
    assert service.collect(_settings(allow_process_list=False)).important_processes is None
    allowed = service.collect(_settings(allow_process_list=True)).important_processes
    assert isinstance(allowed, list)  # at minimum the backend's own process


def test_disk_status_respects_allow_setting():
    service = _fake_service()
    assert service.collect(_settings(allow_disk_status=False)).disks is None
    allowed = service.collect(_settings(allow_disk_status=True)).disks
    assert isinstance(allowed, list) and len(allowed) >= 1


def test_network_status_respects_allow_setting():
    service = _fake_service()
    assert service.collect(_settings(allow_network_status=False)).network_available is None
    assert service.collect(_settings(allow_network_status=True)).network_available in (True, False)


# --- warnings with mocked metrics -----------------------------------------------

def test_warnings_for_high_cpu_and_ram(monkeypatch):
    import computer.computer_service as mod

    monkeypatch.setattr(mod, "cpu_ram_metrics", lambda: {
        "cpu_percent": 97.0, "ram_total_gb": 32.0, "ram_used_gb": 30.0,
        "ram_available_gb": 2.0, "ram_percent": 93.0,
    })
    service = _fake_service()
    state = service.collect(_settings())
    codes = {w.code for w in state.warnings}
    assert "high_cpu" in codes
    assert "high_ram" in codes


def test_warning_for_offline_services():
    service = _fake_service(
        ollama_status_provider=lambda: {"connected": False, "error": "refused"},
        tts_status_provider=lambda: {"provider": "test_tts", "status": "offline"},
        stt_status_provider=lambda: {"provider": "test_stt", "available": False, "reason": "exe missing"},
    )
    state = service.collect(_settings())
    codes = {w.code for w in state.warnings}
    assert {"ollama_offline", "tts_offline", "stt_unavailable"} <= codes


def test_vram_unavailable_is_graceful(monkeypatch):
    import computer.computer_service as mod

    monkeypatch.setattr(mod, "vram_metrics", lambda: {
        "vram_supported": False, "vram_reason": "nvidia-smi not found — AMD GPU",
        "vram_total_gb": None, "vram_used_gb": None, "vram_percent": None,
    })
    service = _fake_service()
    state = service.collect(_settings())
    assert state.vram_percent is None
    assert state.vram_unavailable_reason == "nvidia-smi not found — AMD GPU"
    assert "high_vram" not in {w.code for w in state.warnings}


def test_high_vram_warning_when_supported(monkeypatch):
    import computer.computer_service as mod

    monkeypatch.setattr(mod, "vram_metrics", lambda: {
        "vram_supported": True, "vram_reason": None,
        "vram_total_gb": 24.0, "vram_used_gb": 23.0, "vram_percent": 95.8,
    })
    service = _fake_service()
    state = service.collect(_settings())
    assert "high_vram" in {w.code for w in state.warnings}


def test_summary_reflects_first_warning():
    service = _fake_service(ollama_status_provider=lambda: {"connected": False})
    state = service.collect(_settings())
    summary = ComputerService.summarize(state)
    assert summary["code"] in {w.code for w in state.warnings}
    assert summary["warning_count"] == len(state.warnings)


def test_provider_crash_degrades_to_unknown_status():
    def _boom():
        raise RuntimeError("provider exploded")

    service = _fake_service(tts_status_provider=_boom)
    state = service.collect(_settings())
    assert state.tts_status["status"] == "unknown"  # never raises out of collect()


# --- chat context intent ---------------------------------------------------------

@pytest.mark.parametrize("message", [
    "что сейчас с компьютером?",
    "Сиена, что с компьютером?",
    "почему всё тормозит?",
    "что жрёт память?",
    "почему ты не отвечаешь?",
    "почему Siena не отвечает",
    "почему не работает голос?",
    "почему не работает TTS?",
    "почему не работает микрофон?",
    "почему Ollama не отвечает?",
    "сколько RAM занято?",
    "сколько VRAM занято",
    "что с backend?",
    "что с ресурсами?",
    "работает ли ollama",
    "какие есть предупреждения по системе?",
    "what's with the computer?",
    "why is everything so slow?",
    "how much vram is used",
    "is ollama running",
])
def test_computer_context_wanted_for_computer_questions(message):
    assert wants_computer_context(message) is True


@pytest.mark.parametrize("message", [
    "привет",
    "расскажи шутку",
    "переведи текст на английский",
    "напиши код функции сортировки",
    "кто такой Римуру?",
    "как дела?",
    "расскажи про звёзды",
    "что сейчас со станцией в Nucleares?",
])
def test_computer_context_skipped_for_normal_chat(message):
    assert wants_computer_context(message) is False


def test_context_block_respects_privacy_gates(monkeypatch):
    import computer.computer_service as mod

    monkeypatch.setattr(mod, "_read_active_window_title", lambda: "Private Tab — Bank")
    service = _fake_service()

    open_settings = _settings(allow_active_window_title=True, allow_process_list=True)
    state = service.collect(open_settings)
    block = build_computer_context(state, open_settings)
    assert "[COMPUTER_CONTEXT]" in block and "[/COMPUTER_CONTEXT]" in block
    assert "Private Tab — Bank" in block

    closed_settings = _settings(allow_active_window_title=False, allow_process_list=False)
    state = service.collect(closed_settings)
    block = build_computer_context(state, closed_settings)
    assert "Private Tab" not in block
    assert "Siena runtime processes" not in block


def test_context_block_mentions_read_only():
    service = _fake_service()
    settings = _settings()
    block = build_computer_context(service.collect(settings), settings)
    assert "cannot run commands" in block


# --- settings ----------------------------------------------------------------------

def test_computer_settings_defaults(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    assert body["enable_computer_awareness"] is True
    assert body["show_computer_status_card"] is True
    assert body["computer_status_poll_seconds"] == 10
    assert body["allow_active_window_title"] is False  # privacy default
    assert body["allow_process_list"] is True
    assert body["allow_disk_status"] is True
    assert body["allow_network_status"] is True
    assert body["allow_computer_context_in_chat"] is True
    assert body["computer_warning_cpu_percent"] == 90
    assert body["computer_warning_ram_percent"] == 85
    assert body["computer_warning_vram_percent"] == 90
    assert body["computer_warning_disk_free_gb"] == 10


def test_computer_settings_roundtrip_and_persist(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/settings",
        json={
            "enable_computer_awareness": False,
            "show_computer_status_card": False,
            "computer_status_poll_seconds": 30,
            "allow_active_window_title": True,
            "allow_process_list": False,
            "allow_computer_context_in_chat": False,
            "computer_warning_ram_percent": 70,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enable_computer_awareness"] is False
    assert body["computer_status_poll_seconds"] == 30
    assert body["computer_warning_ram_percent"] == 70

    assert server.config.ENABLE_COMPUTER_AWARENESS is False
    assert server.config.ALLOW_ACTIVE_WINDOW_TITLE is True

    values, error = server.settings_store.load()
    assert error is None
    assert values["enable_computer_awareness"] is False
    assert values["computer_status_poll_seconds"] == 30
    assert values["allow_process_list"] is False


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("computer_status_poll_seconds", 1),
        ("computer_status_poll_seconds", 5000),
        ("computer_warning_cpu_percent", 0),
        ("computer_warning_ram_percent", 101),
        ("computer_warning_vram_percent", -5),
        ("computer_warning_disk_free_gb", 0),
    ],
)
def test_invalid_computer_settings_rejected(monkeypatch, tmp_path, field, bad_value):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/api/settings", json={field: bad_value})
    assert response.status_code == 400
