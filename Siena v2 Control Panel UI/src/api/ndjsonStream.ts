export type StreamGenerationStatus =
  | "thinking"
  | "answering"
  | "continuing"
  | "completed"
  | "length_limited"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface ChatStreamEvent {
  type:
    | "generation.started"
    | "assistant.thinking.delta"
    | "assistant.content.delta"
    | "assistant.tool_call.delta"
    | "generation.segment.completed"
    | "generation.continuation.started"
    | "generation.completed"
    | "generation.failed"
    | "generation.cancelled";
  delta?: string;
  message_id?: string;
  assistant_message_id?: string;
  conversation_id?: string;
  model_used?: string;
  attachments?: unknown[];
  status?: StreamGenerationStatus;
  done_reason?: string | null;
  segment_count?: number;
  continuation_count?: number;
  eval_count?: number;
  eval_count_total?: number;
  prompt_eval_count?: number;
  total_duration?: number;
  configured_num_predict?: number;
  num_ctx?: number;
  incomplete?: boolean;
  error?: string;
  [key: string]: unknown;
}

/** Parse arbitrary TCP splits without losing partial UTF-8 or NDJSON lines. */
export async function* readNdjsonStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatStreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let pending = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline = pending.indexOf("\n");
      while (newline >= 0) {
        const line = pending.slice(0, newline).trim();
        pending = pending.slice(newline + 1);
        if (line) yield JSON.parse(line) as ChatStreamEvent;
        newline = pending.indexOf("\n");
      }
      if (done) break;
    }
    const finalLine = pending.trim();
    if (finalLine) yield JSON.parse(finalLine) as ChatStreamEvent;
  } finally {
    reader.releaseLock();
  }
}
