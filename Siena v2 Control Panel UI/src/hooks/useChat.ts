import { useCallback, useEffect, useRef, useState } from "react";
import { readNdjsonStream, type StreamGenerationStatus } from "../api/ndjsonStream";
import { apiUrl, sienaClient, SienaApiError } from "../api/sienaClient";
import type { ChatAttachmentPayload, ChatMode, ChatTurnStatus, StoredAttachmentMetadata } from "../api/types";
import type { Attachment } from "../app/App";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  timestamp: string;
  attachments?: Attachment[];
  status?: ChatTurnStatus | StreamGenerationStatus;
  error?: string | null;
  doneReason?: string | null;
  incomplete?: boolean;
  segmentCount?: number;
  continuationCount?: number;
  configuredNumPredict?: number | null;
  modelUsed?: string | null;
  requestedRole?: string | null;
  selectionReason?: string | null;
}

export interface SendResult {
  turn: ChatTurn | null;
  errorMessage: string | null;
}

interface UseChatResult {
  messages: ChatTurn[];
  sending: boolean;
  error: string | null;
  send: (
    text: string,
    attachments?: Attachment[],
    conversationId?: string | null,
    isConversationActive?: (conversationId: string) => boolean,
    mode?: ChatMode,
  ) => Promise<SendResult>;
  cancel: () => void;
  reset: (messages?: ChatTurn[]) => void;
}

function nowLabel(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function toPayloadAttachment(a: Attachment): ChatAttachmentPayload {
  return {
    name: a.name,
    type: a.type,
    size: a.size,
    lang: a.lang,
    mime: a.mime,
    content: a.type === "image" ? undefined : a.content,
    data_url: a.type === "image" ? a.dataUrl : undefined,
  };
}

export function fromStoredAttachment(a: StoredAttachmentMetadata): Attachment {
  const type = (a.client_type ?? (a.kind === "image" ? "image" : "text")) as Attachment["type"];
  return {
    id: a.id,
    type,
    name: a.original_name,
    size: a.size_label ?? formatStoredSize(a.size_bytes),
    lang: a.lang ?? undefined,
    mime: a.mime_type ?? undefined,
    url: apiUrl(a.url),
    source: a.source,
    persisted: true,
    ocrStatus: a.ocr_status ?? undefined,
    ocrPreview: a.ocr_preview ?? undefined,
    ocrQuality: a.ocr_quality ?? undefined,
    visionStatus: a.vision_status ?? undefined,
    visionPreview: a.vision_preview ?? undefined,
  };
}

function formatStoredSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function useChat(initial: ChatTurn[] = []): UseChatResult {
  const [messages, setMessages] = useState<ChatTurn[]>(initial);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeAssistantIdRef = useRef<string | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    const activeId = activeAssistantIdRef.current;
    if (activeId) {
      setMessages((current) => current.map((message) =>
        message.id === activeId
          ? { ...message, status: "cancelled", incomplete: true }
          : message,
      ));
    }
  }, []);

  useEffect(() => () => cancel(), [cancel]);

  const send = useCallback(async (
    text: string,
    attachments: Attachment[] = [],
    conversationId?: string | null,
    isConversationActive?: (conversationId: string) => boolean,
    mode: ChatMode = "auto",
  ): Promise<SendResult> => {
    const trimmed = text.trim();
    const fallbackContent = attachments.length > 0 ? `[${attachments.map((a) => a.name).join(", ")}]` : "";
    const content = trimmed || fallbackContent;
    if (!content) return { turn: null, errorMessage: null };

    const localUserId = crypto.randomUUID();
    const temporaryAssistantId = crypto.randomUUID();
    let assistantId: string = temporaryAssistantId;
    let userId: string = localUserId;
    const timestamp = nowLabel();
    const sentAttachments = attachments.map((a) =>
      a.type === "image" ? { ...a, ocrStatus: "running" as const } : a,
    );
    let rawThinking = "";
    let rawContent = "";
    let status: StreamGenerationStatus = "thinking";
    let doneReason: string | null = null;
    let incomplete = true;
    let segmentCount = 0;
    let continuationCount = 0;
    let configuredNumPredict: number | null = null;
    let modelUsed: string | null = null;
    let requestedRole: string | null = null;
    let selectionReason: string | null = null;
    let streamStarted = false;
    let renderTimer: ReturnType<typeof setTimeout> | null = null;

    const currentTurn = (): ChatTurn => ({
      id: assistantId,
      role: "assistant",
      content: rawContent,
      thinking: rawThinking,
      timestamp,
      status,
      doneReason,
      incomplete,
      segmentCount,
      continuationCount,
      configuredNumPredict,
      modelUsed,
      requestedRole,
      selectionReason,
    });
    const flush = (immediate = false) => {
      const commit = () => {
        renderTimer = null;
        const snapshot = currentTurn();
        setMessages((current) => current.map((message) =>
          message.id === assistantId || message.id === temporaryAssistantId
            ? snapshot
            : message,
        ));
      };
      if (immediate) {
        if (renderTimer) clearTimeout(renderTimer);
        commit();
      } else if (!renderTimer) {
        renderTimer = setTimeout(commit, 40);
      }
    };

    setMessages((current) => [
      ...current,
      {
        id: localUserId,
        role: "user",
        content,
        timestamp,
        attachments: sentAttachments.length > 0 ? sentAttachments : undefined,
        status: "processing",
      },
      currentTurn(),
    ]);
    activeAssistantIdRef.current = temporaryAssistantId;
    setSending(true);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await sienaClient.openChatStream(
        content,
        attachments.map(toPayloadAttachment),
        conversationId,
        mode,
        controller.signal,
      );
      for await (const event of readNdjsonStream(response.body!)) {
        if (event.type === "generation.started") {
          streamStarted = true;
          const previousAssistantId = assistantId;
          assistantId = event.assistant_message_id ?? assistantId;
          userId = event.message_id ?? userId;
          modelUsed = typeof event.model_used === "string" ? event.model_used : modelUsed;
          requestedRole = typeof event.requested_role === "string" ? event.requested_role : requestedRole;
          selectionReason = typeof event.selection_reason === "string" ? event.selection_reason : selectionReason;
          activeAssistantIdRef.current = assistantId;
          const stored = Array.isArray(event.attachments)
            ? (event.attachments as StoredAttachmentMetadata[]).map(fromStoredAttachment)
            : undefined;
          setMessages((current) => current.map((message) => {
            if (message.id === previousAssistantId || message.id === temporaryAssistantId) return currentTurn();
            if (message.id === localUserId) {
              return {
                ...message,
                id: userId,
                status: "completed",
                attachments: stored && stored.length > 0 ? stored : message.attachments,
              };
            }
            return message;
          }));
        } else if (event.type === "assistant.thinking.delta") {
          rawThinking += event.delta ?? "";
          if (!rawContent) status = "thinking";
          flush();
        } else if (event.type === "assistant.content.delta") {
          rawContent += event.delta ?? "";
          if (status !== "continuing") status = "answering";
          flush();
        } else if (event.type === "generation.continuation.started") {
          status = "continuing";
          continuationCount = Number(event.continuation ?? continuationCount + 1);
          flush(true);
        } else if (event.type === "generation.segment.completed") {
          segmentCount = Number(event.index ?? segmentCount + 1);
          doneReason = typeof event.done_reason === "string" ? event.done_reason : doneReason;
          flush();
        } else if (event.type === "generation.completed") {
          status = event.status ?? "completed";
          doneReason = typeof event.done_reason === "string" ? event.done_reason : null;
          incomplete = event.incomplete === true;
          segmentCount = Number(event.segment_count ?? segmentCount);
          continuationCount = Number(event.continuation_count ?? continuationCount);
          configuredNumPredict = typeof event.configured_num_predict === "number" ? event.configured_num_predict : null;
          flush(true);
        } else if (event.type === "generation.failed") {
          status = "failed";
          incomplete = true;
          flush(true);
          throw new Error(event.error ?? "Streaming generation failed");
        } else if (event.type === "generation.cancelled") {
          status = "cancelled";
          incomplete = true;
          flush(true);
        }
      }
      const stillActive = !conversationId || !isConversationActive || isConversationActive(conversationId);
      const turn = currentTurn();
      if (!stillActive) return { turn, errorMessage: null };
      void sienaClient.logClientEvent("chat_stream_completed", {
        content_length: rawContent.length,
        thinking_length: rawThinking.length,
        content_tail_json: JSON.stringify(rawContent.slice(-300)),
        done_reason: doneReason,
        segment_count: segmentCount,
        continuation_count: continuationCount,
      });
      return { turn, errorMessage: null };
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        status = "cancelled";
        incomplete = true;
        flush(true);
        return { turn: currentTurn(), errorMessage: null };
      }
      if (!streamStarted && caught instanceof SienaApiError && [404, 405, 501].includes(caught.status)) {
        const fallback = await sienaClient.sendChatMessage(content, attachments.map(toPayloadAttachment), conversationId, mode);
        assistantId = fallback.assistant_message_id ?? assistantId;
        rawContent = fallback.answer;
        doneReason = fallback.done_reason ?? null;
        incomplete = fallback.incomplete === true;
        status = incomplete ? "length_limited" : "completed";
        configuredNumPredict = fallback.configured_num_predict ?? null;
        flush(true);
        return { turn: currentTurn(), errorMessage: null };
      }
      const message = caught instanceof Error ? caught.message : "Failed to reach Siena backend";
      setError(message);
      status = "failed";
      incomplete = true;
      flush(true);
      setMessages((current) => current.map((item) =>
        item.id === userId || item.id === localUserId ? { ...item, status: "failed", error: message } : item,
      ));
      return { turn: null, errorMessage: message };
    } finally {
      if (renderTimer) clearTimeout(renderTimer);
      if (abortRef.current === controller) abortRef.current = null;
      activeAssistantIdRef.current = null;
      setSending(false);
    }
  }, []);

  const reset = useCallback((next: ChatTurn[] = []) => {
    cancel();
    setMessages(next);
  }, [cancel]);

  return { messages, sending, error, send, cancel, reset };
}

