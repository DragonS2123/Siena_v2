import { useCallback, useState } from "react";
import { apiUrl, sienaClient } from "../api/sienaClient";
import type { ChatAttachmentPayload, ChatTurnStatus, StoredAttachmentMetadata } from "../api/types";
import type { Attachment } from "../app/App";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  attachments?: Attachment[];
  status?: ChatTurnStatus;
  error?: string | null;
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
  ) => Promise<SendResult>;
  reset: (messages?: ChatTurn[]) => void;
}

function nowLabel(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Image attachments send their dataUrl as data_url so the backend can run
// glm-ocr on them (Phase 4B) — text-like attachments send `content` instead;
// each attachment only ever carries one or the other.
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

/**
 * Wraps POST /api/chat with local message-list state. Text and attachments
 * (for text/code/markdown/json/log — full content; for image — dataUrl, OCR'd
 * server-side) are both sent to the backend, which injects the resulting
 * content into the model's context. Attachments stay on the local user turn
 * regardless, so the existing attachment-chip rendering in MessageBubble
 * keeps working, and image chips get their OCR status (running/extracted/
 * failed/unavailable) reflected back once the response arrives.
 */
export function useChat(initial: ChatTurn[] = []): UseChatResult {
  const [messages, setMessages] = useState<ChatTurn[]>(initial);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async (
    text: string,
    attachments: Attachment[] = [],
    conversationId?: string | null,
    isConversationActive?: (conversationId: string) => boolean,
  ) => {
    const trimmed = text.trim();
    const fallbackContent = attachments.length > 0 ? `[${attachments.map((a) => a.name).join(", ")}]` : "";
    const content = trimmed || fallbackContent;
    if (!content) return { turn: null, errorMessage: null };

    const messageId = crypto.randomUUID();
    const sentAttachments = attachments.map((a) => (a.type === "image" ? { ...a, ocrStatus: "running" as const } : a));
    setMessages((m) => [
      ...m,
      {
        id: messageId,
        role: "user",
        content,
        timestamp: nowLabel(),
        attachments: sentAttachments.length > 0 ? sentAttachments : undefined,
        status: "processing",
      },
    ]);
    setSending(true);
    setError(null);
    try {
      const { answer, message_id, assistant_message_id, attachments: stored_attachments, ocr_results, vision_results } =
        await sienaClient.sendChatMessage(content, attachments.map(toPayloadAttachment), conversationId);
      const stillActive = !conversationId || !isConversationActive || isConversationActive(conversationId);
      if (!stillActive) {
        return {
          turn: { id: assistant_message_id ?? crypto.randomUUID(), role: "assistant", content: answer, timestamp: nowLabel(), status: "completed" },
          errorMessage: null,
        };
      }
      if (message_id || (stored_attachments && stored_attachments.length > 0)) {
        const persistedAttachments = stored_attachments?.map(fromStoredAttachment);
        setMessages((m) =>
          m.map((msg) =>
            msg.id === messageId || msg.id === message_id
              ? {
                  ...msg,
                  id: message_id ?? msg.id,
                  status: "completed",
                  error: null,
                  attachments: persistedAttachments && persistedAttachments.length > 0 ? persistedAttachments : msg.attachments,
                }
              : msg,
          ),
        );
      }
      if ((ocr_results && ocr_results.length > 0) || (vision_results && vision_results.length > 0)) {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === (message_id ?? messageId)
              ? {
                  ...msg,
                  attachments: msg.attachments?.map((a) => {
                    const ocrResult = ocr_results?.find((r) => r.name === a.name);
                    const visionResult = vision_results?.find((r) => r.name === a.name);
                    return {
                      ...a,
                      ...(ocrResult ? { ocrStatus: ocrResult.status, ocrPreview: ocrResult.preview, ocrQuality: ocrResult.quality } : {}),
                      ...(visionResult ? { visionStatus: visionResult.status, visionPreview: visionResult.preview } : {}),
                    };
                  }),
                }
              : msg,
          ),
        );
      }
      const assistantTurn: ChatTurn = { id: assistant_message_id ?? crypto.randomUUID(), role: "assistant", content: answer, timestamp: nowLabel(), status: "completed" };
      setMessages((m) => [...m, assistantTurn]);
      return { turn: assistantTurn, errorMessage: null };
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to reach Siena backend";
      setError(message);
      if (!conversationId || !isConversationActive || isConversationActive(conversationId)) {
        setMessages((m) =>
          m.map((msg) =>
            msg.id === messageId
              ? { ...msg, status: "failed", error: message }
              : msg,
          ),
        );
      }
      return { turn: null, errorMessage: message };
    } finally {
      setSending(false);
    }
  }, []);

  const reset = useCallback((next: ChatTurn[] = []) => setMessages(next), []);

  return { messages, sending, error, send, reset };
}
