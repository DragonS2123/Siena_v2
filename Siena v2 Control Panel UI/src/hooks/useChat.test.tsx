// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { sienaClient } from "../api/sienaClient";
import { useChat } from "./useChat";

const encoder = new TextEncoder();

function responseFromLines(groups: string[], delay = 0): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      groups.forEach((group, index) => {
        window.setTimeout(() => {
          controller.enqueue(encoder.encode(group));
          if (index === groups.length - 1) controller.close();
        }, delay * index);
      });
    },
  }), { headers: { "Content-Type": "application/x-ndjson" } });
}

describe("useChat end-to-end stream state", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(sienaClient, "logClientEvent").mockResolvedValue(undefined);
  });

  it("updates one assistant turn from thinking through content completion", async () => {
    vi.spyOn(sienaClient, "openChatStream").mockResolvedValue(responseFromLines([
      '{"type":"generation.started","message_id":"user-1","assistant_message_id":"assistant-1"}\n' +
      '{"type":"assistant.thinking.delta","delta":"План "}\n',
      '{"type":"assistant.thinking.delta","delta":"готов."}\n' +
      '{"type":"assistant.content.delta","delta":"```html\\n"}\n',
      '{"type":"assistant.content.delta","delta":"<html></html>\\n```"}\n' +
      '{"type":"generation.segment.completed","index":1,"done_reason":"stop"}\n' +
      '{"type":"generation.completed","status":"completed","done_reason":"stop","segment_count":1,"incomplete":false}\n',
    ], 100));
    const { result } = renderHook(() => useChat());
    let sendPromise!: ReturnType<typeof result.current.send>;

    act(() => {
      sendPromise = result.current.send("HTML", [], "conversation-1");
    });
    await waitFor(() => {
      const assistant = result.current.messages.find((message) => message.role === "assistant");
      expect(assistant?.thinking).toBe("План ");
      expect(assistant?.content).toBe("");
      expect(assistant?.status).toBe("thinking");
    });
    await act(async () => { await sendPromise; });

    const assistants = result.current.messages.filter((message) => message.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]).toMatchObject({
      id: "assistant-1",
      thinking: "План готов.",
      content: "```html\n<html></html>\n```",
      status: "completed",
      doneReason: "stop",
      segmentCount: 1,
      incomplete: false,
    });
  });

  it("appends continuation deltas to the same assistant turn", async () => {
    vi.spyOn(sienaClient, "openChatStream").mockResolvedValue(responseFromLines([
      '{"type":"generation.started","assistant_message_id":"assistant-1"}\n' +
      '{"type":"assistant.content.delta","delta":"first"}\n' +
      '{"type":"generation.segment.completed","index":1,"done_reason":"length"}\n' +
      '{"type":"generation.continuation.started","continuation":1}\n' +
      '{"type":"assistant.content.delta","delta":" second"}\n' +
      '{"type":"generation.segment.completed","index":2,"done_reason":"stop"}\n' +
      '{"type":"generation.completed","status":"completed","done_reason":"stop","segment_count":2,"continuation_count":1,"incomplete":false}\n',
    ]));
    const { result } = renderHook(() => useChat());
    await act(async () => { await result.current.send("code", [], "conversation-1"); });
    const assistants = result.current.messages.filter((message) => message.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]).toMatchObject({
      content: "first second",
      segmentCount: 2,
      continuationCount: 1,
    });
  });

  it("marks the partial assistant turn cancelled when Stop aborts fetch", async () => {
    vi.spyOn(sienaClient, "openChatStream").mockImplementation(async (_message, _attachments, _id, _mode, signal) => {
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(
            '{"type":"generation.started","assistant_message_id":"assistant-1"}\n' +
            '{"type":"assistant.content.delta","delta":"partial"}\n',
          ));
          signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
        },
      }));
    });
    const { result } = renderHook(() => useChat());
    let sendPromise!: ReturnType<typeof result.current.send>;
    act(() => { sendPromise = result.current.send("code", [], "conversation-1"); });
    await waitFor(() => expect(result.current.messages.at(-1)?.content).toBe("partial"));
    act(() => result.current.cancel());
    await act(async () => { await sendPromise; });
    expect(result.current.messages.at(-1)).toMatchObject({
      content: "partial",
      status: "cancelled",
      incomplete: true,
    });
  });
});
