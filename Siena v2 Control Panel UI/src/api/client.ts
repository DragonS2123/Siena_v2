import type { ChatAttachment, Conversation, ModelsPayload } from "./types";

const API = "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export const api = {
  models: () => request<ModelsPayload>("/api/models"),
  refreshModels: () => request<ModelsPayload>("/api/models/refresh", { method: "POST" }),
  assignRole: (role: string, model: string) =>
    request(`/api/models/roles/${role}`, { method: "PUT", body: JSON.stringify({ model }) }),
  conversations: () => request<{ conversations: Conversation[] }>("/api/conversations"),
  conversation: (id: string) => request<Conversation>(`/api/conversations/${id}`),
  createConversation: () =>
    request<{ conversation_id: string }>("/api/conversations", { method: "POST", body: "{}" }),
  updateConversation: (id: string, patch: { title?: string; model_override?: string | null }) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  chat: (conversationId: string, message: string, attachments: ChatAttachment[] = []) =>
    request<{ answer: string; model_used: string }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, message, attachments }),
    }),
  settings: () => request<{ values: Record<string, unknown>; classification: Record<string, string> }>("/api/settings"),
  saveSettings: (values: Record<string, unknown>) =>
    request("/api/settings", { method: "POST", body: JSON.stringify(values) }),
  shortMemory: () => request<{ entries: unknown[] }>("/api/memory/short"),
  longMemory: () => request<{ entries: unknown[] }>("/api/memory/long"),
  insights: () => request<{ items: unknown[] }>("/api/insights"),
  voiceStatus: () => request("/api/voice/status"),
  transcribe: (audio: Blob, language = "ru") => {
    const body = new FormData();
    body.append("file", audio, "recording.wav");
    body.append("language", language);
    return request<{ text: string }>("/api/voice/stt/transcribe", { method: "POST", body });
  },
  synthesize: (text: string) =>
    request<{ audio_url: string }>("/api/voice/synthesize", { method: "POST", body: JSON.stringify({ text }) }),
  audioUrl: (path: string) => `${API}${path}`,
  ocrStatus: () => request("/api/ocr/status"),
  visionStatus: () => request("/api/vision/status"),
  diagnostics: () => request("/api/diagnostics"),
  logs: () => request<{ events: unknown[] }>("/api/logs/recent"),
  trace: () => request<{ events: unknown[] }>("/api/trace/recent"),
};
