from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from game.nucleares_context import build_nucleares_context, nucleares_context_skip_reason, wants_nucleares_context  # noqa: E402
from storage.conversation_store import ConversationStore  # noqa: E402


def _status(ao_json: str | None = None) -> dict:
    normalized = {
        "ambient_temperature": {"value": "21", "raw_key": "AMBIENT_TEMPERATURE"},
        "alarms_active": {"value": "", "raw_key": "ALARMS_ACTIVE"},
        "condenser_temperature": {"value": "20", "raw_key": "CONDENSER_TEMPERATURE"},
        "condenser_pressure": {"value": "1", "raw_key": "CONDENSER_PRESSURE"},
        "condenser_circulation_pump_active": {"value": "False", "raw_key": "CONDENSER_CIRCULATION_PUMP_ACTIVE"},
        "condenser_circulation_pump_speed": {"value": "0", "raw_key": "CONDENSER_CIRCULATION_PUMP_SPEED"},
        "core_pressure": {"value": "1", "raw_key": "CORE_PRESSURE"},
        "pressurizer_pressure": {"value": "1", "raw_key": "PRESSURIZER_PRESSURE"},
        "pressurizer_temperature": {"value": "18.7", "raw_key": "PRESSURIZER_TEMPERATURE"},
        "ao_agent_status": {
            "value": '{"runtime_state":"Idle","language":"RU","response_mode":"heuristic","llm_reachable":false}',
            "raw_key": "AO_AGENT_STATUS",
        },
    }
    if ao_json is not None:
        normalized["ao_agent_diagnostics_json"] = {"value": ao_json, "raw_key": "AO_AGENT_DIAGNOSTICS_JSON"}
    return {
        "game": "nucleares",
        "connected": True,
        "base_url": "http://localhost:8785",
        "parameter_count": 332,
        "normalized": normalized,
        "raw_sample": {"UNUSED": "do not include"},
        "warnings": [],
    }


def _install_temp_store(monkeypatch, tmp_path: Path) -> ConversationStore:
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    monkeypatch.setattr(server, "conversation_store", store)
    monkeypatch.setattr(server, "session_store", server.SessionStore(server.config.SYSTEM_PROMPT, store))
    return store


def _patch_fast_chat(monkeypatch, answer: str = "ok"):
    async def no_ocr(*args, **kwargs):
        return "", []

    async def no_vision(*args, **kwargs):
        return "", []

    monkeypatch.setattr(server, "_run_image_ocr", no_ocr)
    monkeypatch.setattr(server, "_run_image_vision", no_vision)
    monkeypatch.setattr(server, "build_registry", lambda logger: ({}, None, None, None))
    monkeypatch.setattr(server, "run_agent_loop", lambda *args, **kwargs: answer)


def test_intent_triggers_on_station_question():
    assert wants_nucleares_context("что сейчас со станцией?")


def test_intent_triggers_on_reactor_in_nucleares_question():
    assert wants_nucleares_context("что с реактором в Nucleares?")


def test_intent_does_not_trigger_on_unrelated_chat():
    assert not wants_nucleares_context("Привет, как дела?")
    assert not wants_nucleares_context("Какая температура на улице?")
    assert nucleares_context_skip_reason("Какая температура на улице?") == "generic_telemetry_without_game_context"
    assert nucleares_context_skip_reason("Привет, как дела?") is None


def test_connected_telemetry_produces_compact_context_block():
    context = build_nucleares_context(
        _status('{"reactor_overview":{"operation_mode":"SHUTDOWN","core_temperature_c":17.85},"active_alarms":{"alarms":[]}}')
    )

    assert "[NUCLEARES_GAME_CONTEXT]" in context
    assert "connected: true" in context
    assert "operation_mode: SHUTDOWN" in context
    assert "core_temperature_c: 17.85" in context
    assert "alarms_active: none" in context
    assert "ambient_temperature: 21" in context
    assert "raw_sample" not in context
    assert "UNUSED" not in context
    assert len(context) < 1200


def test_unavailable_bridge_produces_connected_false_context():
    context = build_nucleares_context({"game": "nucleares", "connected": False, "error": "connection refused"})

    assert "connected: false" in context
    assert "error: connection refused" in context
    assert "not a real-world nuclear facility" in context


def test_ao_json_parsing_failure_does_not_crash():
    context = build_nucleares_context(_status("{not json"))

    assert "connected: true" in context
    assert "ambient_temperature: 21" in context
    assert "operation_mode:" not in context


def test_chat_path_injects_unavailable_context_when_nucleares_is_not_running(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    _patch_fast_chat(monkeypatch)
    conversation_id = server.session_store.new_conversation("nucleares unavailable")
    captured = {}

    class _Client:
        def status(self):
            return {"game": "nucleares", "connected": False, "error": "not reachable", "attempted": []}

    def fake_agent_loop(*args, **kwargs):
        captured["content"] = kwargs["session"].messages[-1]["content"]
        return "Нуклеарес недоступен."

    monkeypatch.setattr(server, "nucleares_client", _Client())
    monkeypatch.setattr(server, "run_agent_loop", fake_agent_loop)

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "Сиена, что сейчас со станцией?", "attachments": []},
    )

    assert response.status_code == 200
    assert "connected: false" in captured["content"]
    assert "error: not reachable" in captured["content"]


def test_chat_path_skips_nucleares_context_for_unrelated_chat(monkeypatch, tmp_path):
    store = _install_temp_store(monkeypatch, tmp_path)
    _patch_fast_chat(monkeypatch)
    conversation_id = server.session_store.new_conversation("plain chat")
    captured = {}

    class _Client:
        def status(self):
            raise AssertionError("Nucleares bridge should not be called")

    def fake_agent_loop(*args, **kwargs):
        captured["content"] = kwargs["session"].messages[-1]["content"]
        return "Привет!"

    monkeypatch.setattr(server, "nucleares_client", _Client())
    monkeypatch.setattr(server, "run_agent_loop", fake_agent_loop)

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "Привет, как дела?", "attachments": []},
    )

    assert response.status_code == 200
    assert "[NUCLEARES_GAME_CONTEXT]" not in captured["content"]
    conversation = store.get_conversation(conversation_id)
    assert not [event for event in conversation["events"] if event["event"].startswith("nucleares_context_")]
