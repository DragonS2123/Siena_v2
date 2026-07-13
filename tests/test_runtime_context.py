"""Runtime date/time bugfix.

Root cause: the model only ever learned "today's date" if it chose to call
the optional get_current_time tool. Nothing forced that before a
date-sensitive action, so a model relying on its own (frozen training-data)
sense of "now" could — and in production did — generate a stale date (e.g.
a web_search query for "26 сентября 2025" while the real system clock read
July 2026).

Fix: run_chat_turn() (api/server.py, the one shared pipeline for desktop
and remote) now builds a [RUNTIME_CONTEXT] block from datetime.now() fresh
on EVERY turn and injects it unconditionally into combined_context — no
tool call required, no way to opt out of it.

Uses a fake clock (monkeypatching api.server.datetime) so this test is not
sensitive to whatever the real wall-clock date happens to be when it runs.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from storage.conversation_store import ConversationStore  # noqa: E402


class _FixedDatetime(datetime):
    """Subclassing (not a MagicMock) so strftime/astimezone behave exactly
    like the real datetime.now() return value — server.py calls both."""

    _fixed = datetime(2026, 7, 13, 15, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def _install_temp_store(monkeypatch, tmp_path: Path) -> ConversationStore:
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    monkeypatch.setattr(server, "conversation_store", store)
    monkeypatch.setattr(server, "session_store", server.SessionStore(server.config.SYSTEM_PROMPT, store))
    return store


def _patch_fast_chat(monkeypatch, captured: dict):
    async def no_ocr(*args, **kwargs):
        return "", []

    async def no_vision(*args, **kwargs):
        return "", []

    def fake_agent_loop(*args, **kwargs):
        captured["content"] = kwargs["session"].messages[-1]["content"]
        return "ok"

    monkeypatch.setattr(server, "_run_image_ocr", no_ocr)
    monkeypatch.setattr(server, "_run_image_vision", no_vision)
    monkeypatch.setattr(server, "build_registry", lambda logger: ({}, None, None, None))
    monkeypatch.setattr(server, "run_agent_loop", fake_agent_loop)


def test_runtime_context_reflects_fake_clock_not_real_wallclock(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)
    monkeypatch.setattr(server, "datetime", _FixedDatetime)
    conversation_id = server.session_store.new_conversation("runtime date")

    response = TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "какая сегодня дата?", "attachments": []},
    )

    assert response.status_code == 200
    assert "[RUNTIME_CONTEXT]" in captured["content"]
    assert "current_date: 2026-07-13" in captured["content"]
    assert "current_time: 15:30:00" in captured["content"]
    assert "[/RUNTIME_CONTEXT]" in captured["content"]


def test_runtime_context_never_leaks_a_stale_hardcoded_year(monkeypatch, tmp_path):
    # The exact production symptom: a hallucinated "26 сентября 2025" web_search
    # query despite the real clock reading 2026. Locks in that the injected
    # context block itself never contains a year other than the fake "now".
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)
    monkeypatch.setattr(server, "datetime", _FixedDatetime)
    conversation_id = server.session_store.new_conversation("runtime date 2")

    TestClient(server.app).post(
        "/api/chat",
        json={"conversation_id": conversation_id, "message": "hi", "attachments": []},
    )

    runtime_block = captured["content"].split("[RUNTIME_CONTEXT]")[1].split("[/RUNTIME_CONTEXT]")[0]
    assert "2025" not in runtime_block
    assert "2026" in runtime_block


def test_runtime_context_present_on_every_distinct_turn(monkeypatch, tmp_path):
    # Guards against a "computed once at import/module load" regression —
    # each call must re-read datetime.now(), not cache a value across turns.
    _install_temp_store(monkeypatch, tmp_path)
    captured: dict = {}
    _patch_fast_chat(monkeypatch, captured)

    class _AdvancingDatetime(datetime):
        _calls = {"n": 0}

        @classmethod
        def now(cls, tz=None):
            cls._calls["n"] += 1
            # Strictly increasing with every call (not every request) so
            # this only proves "re-read every time", without depending on
            # exactly how many times run_chat_turn calls datetime.now().
            value = datetime(2026, 7, 13, 12, 0, 0) + timedelta(days=cls._calls["n"])
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(server, "datetime", _AdvancingDatetime)
    conversation_id = server.session_store.new_conversation("runtime date 3")
    client = TestClient(server.app)

    def _extract_current_date(content: str) -> str:
        return content.split("current_date: ")[1].split("\n")[0]

    client.post("/api/chat", json={"conversation_id": conversation_id, "message": "turn one", "attachments": []})
    first_date = _extract_current_date(captured["content"])
    client.post("/api/chat", json={"conversation_id": conversation_id, "message": "turn two", "attachments": []})
    second_date = _extract_current_date(captured["content"])

    assert first_date != second_date, "runtime context must be recomputed on every turn, not cached"
