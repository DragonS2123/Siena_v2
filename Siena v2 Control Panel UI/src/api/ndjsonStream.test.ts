// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { readNdjsonStream } from "./ndjsonStream";

function byteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function collect(chunks: Uint8Array[]) {
  const events = [];
  for await (const event of readNdjsonStream(byteStream(chunks))) events.push(event);
  return events;
}

describe("NDJSON streaming parser", () => {
  it("preserves a UTF-8 Cyrillic character split between reads", async () => {
    const bytes = new TextEncoder().encode('{"type":"assistant.content.delta","delta":"Привет"}\n');
    const split = bytes.indexOf(0xd0) + 1;
    const events = await collect([bytes.slice(0, split), bytes.slice(split)]);
    expect(events[0].delta).toBe("Привет");
  });

  it("keeps an incomplete NDJSON line until the next read", async () => {
    const encoder = new TextEncoder();
    const events = await collect([
      encoder.encode('{"type":"assistant.thinking.'),
      encoder.encode('delta","delta":"plan"}\n'),
    ]);
    expect(events).toEqual([{ type: "assistant.thinking.delta", delta: "plan" }]);
  });

  it("parses multiple events delivered by one transport read", async () => {
    const chunk = new TextEncoder().encode(
      '{"type":"assistant.thinking.delta","delta":"a"}\n' +
      '{"type":"assistant.content.delta","delta":"b"}\n',
    );
    expect(await collect([chunk])).toEqual([
      { type: "assistant.thinking.delta", delta: "a" },
      { type: "assistant.content.delta", delta: "b" },
    ]);
  });

  it("parses a final valid line without a trailing newline", async () => {
    const chunk = new TextEncoder().encode('{"type":"generation.completed","done_reason":"stop"}');
    expect(await collect([chunk])).toEqual([
      { type: "generation.completed", done_reason: "stop" },
    ]);
  });
});
