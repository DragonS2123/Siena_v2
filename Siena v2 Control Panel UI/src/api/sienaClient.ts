// Minimal typed HTTP/WebSocket client for the Siena_v2 Python backend
// (api/server.py, started via start_backend.bat on 127.0.0.1:8000).
//
// This file only wraps existing backend endpoints — it does not invent new
// ones. See ARCHITECTURE.md / the backend source for the authoritative
// contract; keep this in sync manually when the backend shape changes.

import type {
  ActiveChatModelResponse,
  ChatAttachmentPayload,
  ChatResponse,
  ConversationDetail,
  ConversationsListResponse,
  InsightDeferResponse,
  InsightDeleteResponse,
  InsightPromoteResponse,
  InsightRejectResponse,
  InsightsResponse,
  LogsRecentResponse,
  LongMemoryResponse,
  MemoryLongSaveResponse,
  ModelLifecycleUnloadResponse,
  ModelLifecycleUnloadTarget,
  ModelsResponse,
  PresenceSayResponse,
  PresenceStatus,
  ResourcesStatusResponse,
  RuntimeStatus,
  SettingsPayload,
  ShortMemoryResponse,
  StreamSpeechResult,
  TraceRecentResponse,
  TranscribeSpeechResponse,
  TranslateResponse,
  TtsStopResponse,
  VoiceProfile,
  VoiceProfilesResponse,
  VoiceStatusResponse,
  VoiceSynthesizeResponse,
} from "./types";

export const API_BASE_URL = "http://127.0.0.1:8000";
export const TRACE_WS_URL = "ws://127.0.0.1:8000/ws/trace";

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export class SienaApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "SienaApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (err) {
    throw new SienaApiError(0, err instanceof Error ? err.message : "Network request failed");
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new SienaApiError(res.status, detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const sienaClient = {
  getRuntimeStatus: () => request<RuntimeStatus>("/api/runtime/status"),

  listConversations: (limit = 50) =>
    request<ConversationsListResponse>(`/api/conversations?limit=${limit}`),

  getConversation: (conversationId: string) =>
    request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`),

  createConversation: (title?: string) =>
    request<{ conversation_id: string }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    }),

  activateConversation: (conversationId: string) =>
    request<{ conversation_id: string; message_count: number }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/activate`,
      { method: "POST" },
    ),

  sendChatMessage: (message: string, attachments: ChatAttachmentPayload[] = [], conversationId?: string | null) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId ?? undefined, attachments }),
    }),

  getRecentTrace: (limit = 100) =>
    request<TraceRecentResponse>(`/api/trace/recent?limit=${limit}`),

  // Model registry (Phase 4D) — role/routing_mode/install-status for every
  // configured model, including qwen3.5:27b shown as manual_only.
  getShortMemory: () => request<ShortMemoryResponse>("/api/memory/short"),

  getLongMemory: (limit = 50, search = "") =>
    request<LongMemoryResponse>(`/api/memory/long?limit=${limit}&search=${encodeURIComponent(search)}`),

  // Feedback row "Save-to-memory" (Chat tab) — explicit human-confirmed
  // save, never automatic. source defaults to "feedback_row" server-side.
  saveToLongMemory: (text: string, conversationId?: string | null, messageId?: string) =>
    request<MemoryLongSaveResponse>("/api/memory/long", {
      method: "POST",
      body: JSON.stringify({
        text,
        conversation_id: conversationId ?? undefined,
        message_id: messageId,
      }),
    }),

  // Insights (candidate memory review queue). status="" fetches every status;
  // the model can only create candidates (candidate_memory_create) — these
  // four actions are REST-only, triggered by an explicit human click.
  listInsights: (status = "pending", limit = 50) =>
    request<InsightsResponse>(`/api/insights?status=${encodeURIComponent(status)}&limit=${limit}`),

  promoteInsight: (candidateId: number) =>
    request<InsightPromoteResponse>(`/api/insights/${candidateId}/promote`, { method: "POST" }),

  rejectInsight: (candidateId: number) =>
    request<InsightRejectResponse>(`/api/insights/${candidateId}/reject`, { method: "POST" }),

  laterInsight: (candidateId: number) =>
    request<InsightDeferResponse>(`/api/insights/${candidateId}/later`, { method: "POST" }),

  deleteInsight: (candidateId: number) =>
    request<InsightDeleteResponse>(`/api/insights/${candidateId}`, { method: "DELETE" }),

  getRecentLogs: (limit = 200) =>
    request<LogsRecentResponse>(`/api/logs/recent?limit=${limit}`),

  getSettings: () => request<SettingsPayload>("/api/settings"),

  updateSettings: (update: Partial<SettingsPayload>) =>
    request<SettingsPayload>("/api/settings", {
      method: "POST",
      body: JSON.stringify(update),
    }),

  getModels: () => request<ModelsResponse>("/api/models"),

  // Manual active chat model switch (Phase 4E) — explicit human action only,
  // never invoked automatically. Backend rejects anything outside
  // [qwen3.5:9b, qwen3.5:27b] or not installed in Ollama.
  getActiveChatModel: () => request<ActiveChatModelResponse>("/api/models/active"),

  setActiveChatModel: (model: string) =>
    request<{ ok: boolean; active_chat_model: string }>("/api/models/active", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),

  // Standalone, explicit-only translation (Phase 4C) — never called
  // automatically by chat; used by the per-message Translate button.
  translate: (
    text: string,
    options: { sourceLang?: string; targetLang: string; preserveFormatting?: boolean },
  ) =>
    request<TranslateResponse>("/api/translate", {
      method: "POST",
      body: JSON.stringify({
        text,
        source_lang: options.sourceLang ?? "auto",
        target_lang: options.targetLang,
        preserve_formatting: options.preserveFormatting ?? true,
      }),
    }),

  // Bridges purely client-side attachment events (add/remove/unsupported/
  // too_large — things that happen before, or without, a /api/chat call)
  // into the same JSONL+WS trace as backend events. Best-effort: trace
  // visibility should never block the actual attachment UX, so callers
  // don't need to await/handle failures here.
  logClientEvent: (event: string, fields: Record<string, unknown> = {}) =>
    request<{ logged: string }>("/api/trace/client-event", {
      method: "POST",
      body: JSON.stringify({ event, fields }),
    }).catch(() => undefined),

  // Voice — TTS playback (Phase 1, HANDOFF_v2.md) and STT (Phase 1 backend,
  // Phase 2 mic UI). getVoiceStatus reports both; stt_available gates
  // whether the mic button is enabled.
  getVoiceStatus: () => request<VoiceStatusResponse>("/api/voice/status"),

  // Voice profiles (Settings > Voice) — real, backend-persisted TTS timbre
  // presets (voice/voice_profiles.py). Activating one changes future
  // Speak/Stream output immediately.
  listVoiceProfiles: () => request<VoiceProfilesResponse>("/api/voice/profiles"),
  getActiveVoiceProfile: () => request<VoiceProfile>("/api/voice/profiles/active"),
  setActiveVoiceProfile: (profileId: string) =>
    request<VoiceProfile>("/api/voice/profiles/active", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId }),
    }),

  // Mic recording UI (Phase 2, HANDOFF_v2.md) — posts a WAV file recorded via
  // useVoiceRecorder to the whisper.cpp backend endpoint. Does NOT go
  // through the shared request() helper: this is a multipart/form-data body,
  // not JSON.
  transcribeSpeech: async (file: Blob, language?: string, signal?: AbortSignal): Promise<TranscribeSpeechResponse> => {
    const form = new FormData();
    form.append("file", file, "recording.wav");
    if (language) form.append("language", language);

    let res: Response;
    try {
      res = await fetch(`${API_BASE_URL}/api/voice/stt/transcribe`, {
        method: "POST",
        body: form,
        signal,
      });
    } catch (err) {
      throw new SienaApiError(0, err instanceof Error ? err.message : "Network request failed");
    }

    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new SienaApiError(res.status, detail || res.statusText);
    }
    return res.json() as Promise<TranscribeSpeechResponse>;
  },

  synthesizeSpeech: (text: string, voice?: string, signal?: AbortSignal) =>
    request<VoiceSynthesizeResponse>("/api/voice/synthesize", {
      method: "POST",
      body: JSON.stringify(voice ? { text, voice } : { text }),
      signal,
    }),

  // Experimental (Phase 2/3, HANDOFF_v2.md) — raw PCM streaming, backed by
  // POST /api/voice/tts/stream. Does NOT go through the shared request()
  // helper above (that always calls res.json(); this needs the raw
  // ReadableStream body and response headers instead). qwen3_tts_ggml_vulkan
  // only, no Silero fallback — a non-2xx response is thrown as a
  // SienaApiError exactly like request() does, so callers handle it the
  // same honest way as any other API error.
  streamSpeech: async (text: string, voice?: string, signal?: AbortSignal): Promise<StreamSpeechResult> => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE_URL}/api/voice/tts/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(voice ? { text, voice } : { text }),
        signal,
      });
    } catch (err) {
      throw new SienaApiError(0, err instanceof Error ? err.message : "Network request failed");
    }

    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new SienaApiError(res.status, detail || res.statusText);
    }
    if (!res.body) {
      throw new SienaApiError(0, "Streaming response has no readable body");
    }

    const sampleRate = Number(res.headers.get("X-Siena-TTS-Sample-Rate")) || 24000;
    const channels = Number(res.headers.get("X-Siena-TTS-Channels")) || 1;
    const format = res.headers.get("X-Siena-TTS-Format") || "pcm";
    const provider = res.headers.get("X-Siena-TTS-Provider");

    return { body: res.body, sampleRate, channels, format, provider };
  },

  // Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — honest visibility +
  // safe manual controls only, no automatic keep_alive/TTL policy.
  getResourcesStatus: () => request<ResourcesStatusResponse>("/api/resources/status"),

  stopTtsServer: (force = false) =>
    request<TtsStopResponse>(`/api/voice/tts/stop${force ? "?force=true" : ""}`, { method: "POST" }),

  unloadModels: (target: ModelLifecycleUnloadTarget, model?: string) =>
    request<ModelLifecycleUnloadResponse>("/api/models/lifecycle/unload", {
      method: "POST",
      body: JSON.stringify(model ? { target, model } : { target }),
    }),

  // Presence layer (0.2.1, Phase 1) — local, lightweight, opt-in runtime
  // state (available/idle/listening/thinking/speaking/quiet/offline/error).
  // Never calls the model; quiet/wake/say are plain REST actions, not tools.
  getPresenceStatus: () => request<PresenceStatus>("/api/presence/status"),

  pingPresence: () => request<PresenceStatus>("/api/presence/ping", { method: "POST" }),

  setPresenceQuiet: () => request<PresenceStatus>("/api/presence/quiet", { method: "POST" }),

  setPresenceWake: () => request<PresenceStatus>("/api/presence/wake", { method: "POST" }),

  sayPresence: () => request<PresenceSayResponse>("/api/presence/say", { method: "POST" }),

  // Phase 2 — frontend-reported voice lifecycle (raw mic recording / actual
  // audio playback, which the backend cannot see on its own). Best-effort:
  // presence is a soft indicator, never worth failing the actual voice UX
  // over, so callers don't need to await/handle failures here.
  postPresenceActivity: (activity: "listening" | "speaking" | "available") =>
    request<PresenceStatus>("/api/presence/activity", {
      method: "POST",
      body: JSON.stringify({ activity, source: "frontend" }),
    }).catch(() => undefined),

  dismissPresenceEvent: () =>
    request<PresenceStatus & { dismissed: boolean }>("/api/presence/event/dismiss", { method: "POST" }),
};
