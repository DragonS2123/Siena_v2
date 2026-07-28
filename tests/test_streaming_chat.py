from __future__ import annotations

import asyncio
import json

import pytest

from core.errors import SienaTimeoutError
from core.streaming_chat import (
    PrematureHtmlFenceFilter,
    response_missing_requested_structure,
    continuation_messages,
    seam_merge,
    with_stream_timeouts,
)


def _chunk(*, thinking="", content="", done=False, reason=None, eval_count=0):
    payload = {
        "message": {"role": "assistant", "thinking": thinking, "content": content},
        "done": done,
    }
    if reason is not None:
        payload.update({
            "done_reason": reason,
            "eval_count": eval_count,
            "prompt_eval_count": 11,
            "total_duration": 100,
            "eval_duration": 80,
        })
    return payload


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _prepare(client, monkeypatch):
    runtime = client.app.state.runtime
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    monkeypatch.setattr(
        runtime.catalog,
        "refresh",
        lambda: {"available": True, "models": [{"name": "qwen3.5:9b"}]},
    )
    return runtime, conversation_id


def test_explicit_html_line_and_fence_requirements_drive_continuation():
    request = "Создай автономный HTML-файл не менее 500 физических строк в ```html fence"
    short = "```html\n<!DOCTYPE html>\n<html><body></body></html>\n```"
    complete = "```html\n<!DOCTYPE html>\n<html>\n<body>\n" + ("<div>x</div>\n" * 494) + "</body>\n</html>\n```"
    assert response_missing_requested_structure(request, short) is True
    assert len(complete.splitlines()) >= 500
    assert response_missing_requested_structure(request, complete) is False

def test_premature_html_fence_filter_handles_split_utf8_stream_chunks():
    stream_filter = PrematureHtmlFenceFilter()
    opening = stream_filter.feed("```html\n<!DOCTYPE html>\n<html>\n<body>\n")
    premature_a = stream_filter.feed("<main>live</main>\n`")
    premature_b = stream_filter.feed("``\n")
    closing = stream_filter.feed("</body>\n</html>\n```") + stream_filter.flush()
    assert opening.startswith("```html")
    assert premature_a == "<main>live</main>\n"
    assert premature_b == "\n"
    assert stream_filter.suppressed == 1
    assert closing.endswith("</html>\n```")

def test_continuation_prompt_reports_progress_and_prioritizes_closing():
    accumulated = "```html\n" + "\n".join(f"<div>{index}</div>" for index in range(500))
    messages = continuation_messages(
        [{"role": "user", "content": "Сделай HTML минимум на 500 строк"}],
        accumulated,
        num_ctx=32768,
    )
    instruction = messages[-1]["content"]
    assert "501 физических строк" in instruction
    assert "Не добавляй новые возможности" in instruction
    assert "закрывающие части style/body/script/html/fence" in instruction

def test_stream_separates_thinking_and_content_and_persists_final(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)

    async def fake_stream(_self, _messages, tools=None, model=None):
        yield _chunk(thinking="Сначала ")
        yield _chunk(thinking="план.")
        yield _chunk(content="```html\n")
        yield _chunk(content="<html></html>\n```", done=True, reason="stop", eval_count=17)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    response = client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Напиши HTML код"},
    )
    events = _events(response)

    assert [event["type"] for event in events] == [
        "generation.started",
        "assistant.thinking.delta",
        "assistant.thinking.delta",
        "assistant.content.delta",
        "assistant.content.delta",
        "assistant.content.delta",
        "generation.segment.completed",
        "generation.completed",
    ]
    assert "".join(event.get("delta", "") for event in events if "thinking" in event["type"]) == "Сначала план."
    assert "".join(event.get("delta", "") for event in events if "content" in event["type"]) == "```html\n<html></html>\n```"
    stored = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["content"] == "```html\n<html></html>\n```"
    assert stored["metadata"]["thinking"] == "Сначала план."
    assert stored["metadata"]["status"] == "completed"


def test_length_auto_continues_in_same_message_and_removes_seam(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _chunk(content="```html\n<div>\nlast line\n", done=True, reason="length", eval_count=4096)
        else:
            yield _chunk(content="```html\nlast line\n</div>\n</html>\n```")
            yield _chunk(done=True, reason="stop", eval_count=37)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    response = client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Создай полный HTML файл"},
    )
    events = _events(response)
    completed = events[-1]

    assert calls == 2
    assert completed["segment_count"] == 2
    assert completed["continuation_count"] == 1
    assert completed["eval_count_total"] == 4133
    started_ids = {event["assistant_message_id"] for event in events if event["type"] == "generation.started"}
    assert len(started_ids) == 1
    content = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]["content"]
    assert content == "```html\n<div>\nlast line\n</div>\n</html>\n```"
    assert content.count("```html") == 1
    assert content.count("last line") == 1


def test_two_length_segments_then_stop(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        pieces = {
            1: ("```html\n<!DOCTYPE html>\n<html>\n<body>\n" + "A" * 40 + "\n", "length", 100),
            2: ("A" * 40 + "\n" + "B" * 40 + "\n", "length", 100),
            3: ("B" * 40 + "\n</body>\n</html>\n```", "stop", 20),
        }
        content, reason, count = pieces[calls]
        yield _chunk(content=content, done=True, reason=reason, eval_count=count)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Полный HTML код"},
    ))
    assert [event["done_reason"] for event in events if event["type"] == "generation.segment.completed"] == [
        "length", "length", "stop",
    ]
    assert events[-1]["eval_count_total"] == 220
    assert events[-1]["continuation_count"] == 2


def test_max_auto_continuations_stops_chain(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    runtime.settings.save({"max_auto_continuations": 1})
    calls = 0

    async def always_length(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        yield _chunk(content="```html\n" if calls == 1 else f"part {calls}\n", done=True, reason="length", eval_count=10)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", always_length)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "HTML код"},
    ))
    assert calls == 2
    assert events[-1]["status"] == "length_limited"
    assert events[-1]["continuation_count"] == 1


def test_provider_error_after_partial_response_is_saved(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)

    async def broken(_self, _messages, tools=None, model=None):
        yield _chunk(content="```html\npartial")
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", broken)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "HTML код"},
    ))
    assert events[-1]["type"] == "generation.failed"
    stored = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["content"] == "```html\npartial"
    assert stored["metadata"]["status"] == "failed"
    assert stored["metadata"]["incomplete"] is True


def test_stream_idle_timeout_is_distinct():
    async def run():
        async def stalled():
            yield {"message": {"content": "first"}}
            await asyncio.sleep(0.1)
            yield {"message": {"content": "late"}}

        iterator = with_stream_timeouts(
            stalled(),
            first_token_timeout=0.05,
            idle_timeout=0.01,
            hard_deadline=asyncio.get_running_loop().time() + 1,
        )
        assert await anext(iterator) == {"message": {"content": "first"}}
        with pytest.raises(SienaTimeoutError, match="stream idle"):
            await anext(iterator)

    asyncio.run(run())


def test_seam_merge_preserves_code_whitespace_and_removes_repeat_fence():
    existing = "```html\n  <div>\n    repeated\n"
    incoming = "Продолжаю:\n```html\n    repeated\n      child\n"
    merged, overlap = seam_merge(existing, incoming, 4000)
    assert overlap == len("    repeated\n")
    assert merged == "      child\n"


def test_pending_stream_recovers_as_interrupted(client):
    runtime = client.app.state.runtime
    conversation_id = client.post("/api/conversations", json={}).json()["conversation_id"]
    pending = runtime.conversations.append_message(
        conversation_id,
        "assistant",
        "```html\npartial",
        metadata={"status": "answering", "incomplete": True},
    )
    assert runtime.conversations.recover_interrupted_messages() == 1
    stored = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["id"] == pending["id"]
    assert stored["content"] == "```html\npartial"
    assert stored["metadata"]["status"] == "interrupted"

def test_client_disconnect_cancels_provider_and_preserves_partial(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)

    async def endless(_self, _messages, tools=None, model=None):
        yield _chunk(content="```html\npartial")
        await asyncio.sleep(60)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", endless)

    async def run():
        stream = runtime.chat.stream_turn(conversation_id, "Создай HTML код")
        assert (await anext(stream))["type"] == "generation.started"
        assert (await anext(stream))["type"] == "assistant.content.delta"
        await stream.aclose()

    asyncio.run(run())
    stored = client.get(f"/api/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["content"] == "```html\npartial"
    assert stored["metadata"]["status"] == "cancelled"
    assert stored["metadata"]["incomplete"] is True


def test_active_chunks_keep_stream_alive_beyond_single_request_threshold():
    async def run():
        async def active():
            for index in range(5):
                await asyncio.sleep(0.02)
                yield {"index": index}

        iterator = with_stream_timeouts(
            active(),
            first_token_timeout=0.05,
            idle_timeout=0.05,
            hard_deadline=asyncio.get_running_loop().time() + 0.2,
        )
        received = [item async for item in iterator]
        assert received == [{"index": index} for index in range(5)]

    asyncio.run(run())

def test_empty_stop_after_length_retries_while_structure_is_incomplete(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _chunk(content="```html\n<!DOCTYPE html>\n<html>\n<body>\n" + "x" * 80, done=True, reason="length", eval_count=100)
        elif calls == 2:
            yield _chunk(thinking="Need to continue", done=True, reason="stop", eval_count=10)
        else:
            yield _chunk(content="\n</body>\n</html>\n```", done=True, reason="stop", eval_count=10)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Полный HTML код"},
    ))
    assert calls == 3
    assert events[-1]["status"] == "completed"
    assert events[-1]["continuation_count"] == 2
    assert events[-1]["eval_count_total"] == 120

def test_stop_with_open_fence_still_continues_until_structure_closes(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield _chunk(content="```html\n<!DOCTYPE html>\n<html>\n<body>\n" + "body" * 20, done=True, reason="stop", eval_count=50)
        else:
            yield _chunk(content="\n</body>\n</html>\n```", done=True, reason="stop", eval_count=10)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Полный HTML файл"},
    ))
    assert calls == 2
    assert events[-1]["status"] == "completed"
    assert events[-1]["continuation_count"] == 1

def test_code_generation_does_not_offer_tool_schemas(client, monkeypatch):
    _runtime, conversation_id = _prepare(client, monkeypatch)
    observed_tools = []
    document = "```html\n<!DOCTYPE html>\n<html>\n<body>\n" + ("<div>x</div>\n" * 494) + "</body>\n</html>\n```"

    async def fake_stream(_self, _messages, tools=None, model=None):
        observed_tools.append(tools)
        yield _chunk(content=document, done=True, reason="stop", eval_count=100)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={
            "conversation_id": conversation_id,
            "message": "Создай автономный HTML-файл не менее 500 физических строк в ```html fence",
        },
    ))
    assert observed_tools == [None]
    assert events[-1]["status"] == "completed"

def test_late_tool_call_after_visible_content_is_not_dispatched(client, monkeypatch):
    runtime, conversation_id = _prepare(client, monkeypatch)
    dispatched = []
    monkeypatch.setattr(runtime.registry, "dispatch", lambda *args, **kwargs: dispatched.append(args))
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                "message": {
                    "role": "assistant",
                    "content": "```html\n<!DOCTYPE html>\n<html>\n<body>\n" + "x" * 80,
                    "tool_calls": [{"function": {"name": "delegate_model", "arguments": {"task": "duplicate"}}}],
                },
                "done": True,
                "done_reason": "stop",
                "eval_count": 50,
            }
        else:
            yield _chunk(content="\n</body>\n</html>\n```", done=True, reason="stop", eval_count=10)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "Полный HTML файл"},
    ))
    assert dispatched == []
    assert calls == 2
    assert events[-1]["status"] == "completed"


def test_tool_only_pass_executes_after_complete_call_and_streams_followup(client, monkeypatch):
    from core.message import ToolResult

    runtime, conversation_id = _prepare(client, monkeypatch)
    dispatched = []

    def dispatch(name, args, tool_call_id=None):
        dispatched.append((name, args, tool_call_id))
        return ToolResult(ok=True, content="tool result")

    monkeypatch.setattr(runtime.registry, "dispatch", dispatch)
    calls = 0

    async def fake_stream(_self, _messages, tools=None, model=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call-1", "function": {"name": "short_memory_search", "arguments": {"query": "x"}}}],
                },
                "done": True,
                "done_reason": "stop",
                "eval_count": 5,
            }
        else:
            yield _chunk(content="final answer", done=True, reason="stop", eval_count=7)

    monkeypatch.setattr("core.ollama_client.OllamaClient.stream_chat", fake_stream)
    events = _events(client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "message": "remember this"},
    ))
    assert dispatched == [("short_memory_search", {"query": "x"}, "call-1")]
    assert "final answer" == "".join(event.get("delta", "") for event in events if event["type"] == "assistant.content.delta")
    assert not any("arguments" in event for event in events)
