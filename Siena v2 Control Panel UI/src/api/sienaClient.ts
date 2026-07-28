import type {
  ChatAttachmentPayload,
  ChatResponse,
  ConversationDetail,
  ConversationsListResponse,
  InsightsResponse,
  LogsRecentResponse,
  LongMemoryResponse,
  MemoryLongSaveResponse,
  ModelsResponse,
  ResourcesStatusResponse,
  RuntimeStatus,
  SettingsPayload,
  ShortMemoryResponse,
  TraceRecentResponse,
  TranscribeSpeechResponse,
  VoiceProfile,
  VoiceProfilesResponse,
  VoiceStatusResponse,
  VoiceSynthesizeResponse,
} from "./types";

export const API_BASE_URL = "http://127.0.0.1:8000";
export const TRACE_WS_URL = "ws://127.0.0.1:8000/ws/trace";

let activeConversationId: string | null = null;
const LOCAL_SETTINGS_KEY = "siena.frontend-settings.v2";

const FRONTEND_DEFAULTS: Partial<SettingsPayload> = {
  appearance_theme: "dark",
  accent_color: "sienna",
  ui_font_size: "default",
  ui_density: "comfortable",
  show_message_timestamps: true,
  show_typing_animation: true,
  copy_before_clear_chat: false,
  startup_page: "chat",
  code_font_size: "default",
  code_line_wrap: false,
  code_syntax_highlighting: true,
  code_show_line_numbers: true,
  code_show_language_badge: true,
  code_show_copy_button: true,
  code_show_collapse_button: true,
  code_show_save_button: true,
  preferred_response_language: "auto",
  interface_language: "en",
};

const PERSISTED_SETTINGS = new Set([
  "max_context_messages", "num_ctx", "num_predict", "request_timeout_seconds",
  "stt_language", "tts_provider", "interface_language", "appearance_theme",
  "ui_font_size", "ui_density", "show_message_timestamps",
  "show_typing_animation", "startup_page", "log_level",
]);

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
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: init?.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    throw new SienaApiError(0, error instanceof Error ? error.message : "Network request failed");
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new SienaApiError(response.status, detail || response.statusText);
  }
  return response.json() as Promise<T>;
}

function readLocalSettings(): Record<string, unknown> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(LOCAL_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeLocalSettings(update: Record<string, unknown>): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify({ ...readLocalSettings(), ...update }));
}

async function settingsPayload(): Promise<SettingsPayload> {
  const response = await request<{ values: Record<string, unknown> }>("/api/settings");
  return { ...FRONTEND_DEFAULTS, ...readLocalSettings(), ...response.values } as SettingsPayload;
}

async function modelsPayload(refresh = false): Promise<ModelsResponse> {
  const payload = await request<any>(refresh ? "/api/models/refresh" : "/api/models", refresh ? { method: "POST" } : undefined);
  const chatRole = payload.roles?.find((role: any) => role.role === "chat")?.model ?? "";
  return {
    ...payload,
    ollama_connected: Boolean(payload.available),
    active_chat_model: chatRole,
    last_used_model: null,
    last_used_role: null,
    models: (payload.models ?? []).map((model: any) => {
      const roleNames = (payload.roles ?? []).filter((role: any) => role.model === model.name).map((role: any) => role.role);
      return {
        ...model,
        role: roleNames.join(", ") || "unassigned",
        roles: roleNames,
        routing_mode: roleNames.includes("chat") ? "auto" : roleNames.includes("coder") ? "auto_for_code" : "tool",
        enabled: true,
        description: [model.family, model.parameter_size, model.quantization].filter(Boolean).join(" · ") || "Installed Ollama model",
        status: "installed",
        is_last_used: false,
        is_active_chat_model: model.name === chatRole,
      };
    }),
  } as ModelsResponse;
}

export const sienaClient = {
  getRuntimeStatus: async (): Promise<RuntimeStatus> => {
    const [runtime, models, settings] = await Promise.all([
      request<any>("/api/runtime/status"),
      modelsPayload(),
      settingsPayload(),
    ]);
    const roles = Object.fromEntries((models as any).roles?.map((role: any) => [role.role, role.model]) ?? []);
    return {
      primary_model: roles.chat ?? "n/a",
      code_model: roles.coder ?? "n/a",
      delegate_models: roles,
      ollama_host: "http://127.0.0.1:11434",
      ollama_status: { connected: Boolean(runtime.ollama?.available), models: models.models.map(model => model.name), error: runtime.ollama?.error },
      registered_tools: runtime.registered_tools ?? [],
      max_iterations: 0,
      request_timeout_seconds: Number(settings.request_timeout_seconds ?? 0),
      delegate_timeout_seconds: 0,
      memory_paths: { short: runtime.paths?.short_memory ?? "", long: runtime.paths?.long_memory ?? "" },
      log_path: runtime.paths?.logs ?? "",
      web_search_provider: "disabled",
      log_level: String(settings.log_level ?? "INFO"),
      max_context_messages: Number(settings.max_context_messages ?? 0),
      num_ctx: Number(settings.num_ctx ?? 0),
      num_predict: Number(settings.num_predict ?? 0),
      last_used_model: null,
      last_used_role: null,
      active_chat_model: roles.chat ?? "n/a",
      cpu_percent: null,
      ram_total_gb: null,
      ram_used_gb: null,
      ram_available_gb: null,
      ram_percent: null,
      vram_supported: false,
      vram_reason: "System metrics are not exposed by the current core API",
      vram_total_gb: null,
      vram_used_gb: null,
      vram_percent: null,
    };
  },

  listConversations: async (limit = 50): Promise<ConversationsListResponse> => {
    const response = await request<{ conversations: any[] }>(`/api/conversations?limit=${limit}`);
    if (activeConversationId && !response.conversations.some(item => item.id === activeConversationId)) activeConversationId = null;
    if (!activeConversationId && response.conversations.length) activeConversationId = response.conversations[0].id;
    return { conversations: response.conversations, active_conversation_id: activeConversationId };
  },
  getConversation: (conversationId: string) =>
    request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`),
  createConversation: async (title?: string) => {
    const response = await request<{ conversation_id: string }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    });
    activeConversationId = response.conversation_id;
    return response;
  },
  activateConversation: async (conversationId: string) => {
    const conversation = await request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`);
    activeConversationId = conversationId;
    return { conversation_id: conversationId, message_count: conversation.messages?.length ?? 0 };
  },
  updateConversation: (conversationId: string, patch: { title?: string; model_override?: string | null }) =>
    request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  sendChatMessage: (message: string, attachments: ChatAttachmentPayload[] = [], conversationId?: string | null) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId ?? activeConversationId, attachments }),
    }),

  getRecentTrace: (limit = 100) => request<TraceRecentResponse>(`/api/trace/recent?limit=${limit}`),
  getRecentLogs: async (limit = 200): Promise<LogsRecentResponse> => {
    const response = await request<{ events: any[] }>(`/api/logs/recent?limit=${limit}`);
    return { entries: response.events ?? [] };
  },
  logClientEvent: (event: string, fields: Record<string, unknown> = {}) =>
    request<{ logged: string }>("/api/trace/client-event", {
      method: "POST",
      body: JSON.stringify({ event, fields }),
    }).catch(() => undefined),

  getShortMemory: () => request<ShortMemoryResponse>("/api/memory/short"),
  getLongMemory: (limit = 50, search = "") =>
    request<LongMemoryResponse>(`/api/memory/long?limit=${limit}&query=${encodeURIComponent(search)}`),
  saveToLongMemory: async (text: string, _conversationId?: string | null, _messageId?: string): Promise<MemoryLongSaveResponse> => {
    const entry = await request<any>("/api/memory/long", { method: "POST", body: JSON.stringify({ text }) });
    return { saved: true, entry };
  },

  listInsights: async (status = "pending", limit = 50): Promise<InsightsResponse> => {
    const response = await request<{ items: any[] }>(`/api/insights?status=${encodeURIComponent(status)}&limit=${limit}`);
    return { entries: response.items ?? [] };
  },
  promoteInsight: (id: number) => request<any>(`/api/insights/${id}/promote`, { method: "POST" }),
  rejectInsight: (id: number) => request<any>(`/api/insights/${id}/reject`, { method: "POST" }),
  laterInsight: (id: number) => request<any>(`/api/insights/${id}/later`, { method: "POST" }),
  deleteInsight: (id: number) => request<any>(`/api/insights/${id}`, { method: "DELETE" }),

  getSettings: settingsPayload,
  updateSettings: async (update: Partial<SettingsPayload>): Promise<SettingsPayload> => {
    const backend: Record<string, unknown> = {};
    const local: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(update)) {
      (PERSISTED_SETTINGS.has(key) ? backend : local)[key] = value;
    }
    if (Object.keys(local).length) writeLocalSettings(local);
    if (Object.keys(backend).length) {
      await request("/api/settings", { method: "POST", body: JSON.stringify(backend) });
    }
    return settingsPayload();
  },

  getModels: () => modelsPayload(),
  refreshModels: () => modelsPayload(true),
  setActiveChatModel: async (model: string) => {
    const response = await request<any>("/api/models/roles/chat", {
      method: "PUT",
      body: JSON.stringify({ model }),
    });
    return { ok: true, active_chat_model: response.model_roles?.chat ?? model };
  },
  assignModelRole: (role: string, model: string) =>
    request<any>(`/api/models/roles/${encodeURIComponent(role)}`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),

  getVoiceStatus: async (): Promise<VoiceStatusResponse> => {
    const response = await request<any>("/api/voice/status");
    return {
      stt_available: Boolean(response.stt?.available),
      stt_model: response.stt?.model ?? "",
      stt_device: "",
      stt_provider: response.stt?.provider,
      stt_reason: response.stt?.reason,
      tts_available: Boolean(response.tts?.available),
      tts_provider: response.tts?.provider ?? "",
      tts_fallback_provider: null,
      tts_language: response.tts?.language ?? "",
      tts_voice: response.tts?.voice ?? "",
    };
  },
  listVoiceProfiles: async (): Promise<VoiceProfilesResponse> => {
    const response = await request<any>("/api/voice/profiles");
    return { profiles: response.profiles ?? [], active_profile_id: response.active_profile_id } as VoiceProfilesResponse;
  },
  getActiveVoiceProfile: async (): Promise<VoiceProfile> => {
    const response = await request<any>("/api/voice/profiles");
    return response.profiles.find((profile: VoiceProfile) => profile.id === response.active_profile_id) ?? response.profiles[0];
  },
  setActiveVoiceProfile: (profileId: string) =>
    request<VoiceProfile>(`/api/voice/profiles/active/${encodeURIComponent(profileId)}`, { method: "POST" }),
  transcribeSpeech: async (file: Blob, language?: string, signal?: AbortSignal): Promise<TranscribeSpeechResponse> => {
    const form = new FormData();
    form.append("file", file, "recording.wav");
    if (language) form.append("language", language);
    return request<TranscribeSpeechResponse>("/api/voice/stt/transcribe", { method: "POST", body: form, signal });
  },
  synthesizeSpeech: (text: string, voice?: string, signal?: AbortSignal) =>
    request<VoiceSynthesizeResponse>("/api/voice/synthesize", {
      method: "POST",
      body: JSON.stringify(voice ? { text, voice } : { text }),
      signal,
    }),

  getResourcesStatus: async (): Promise<ResourcesStatusResponse> => {
    const [models, voice] = await Promise.all([modelsPayload(), sienaClient.getVoiceStatus()]);
    return {
      ollama_available: models.ollama_connected,
      ollama_loaded_models: models.models.filter((model: any) => model.loaded).map((model: any) => ({
        name: model.name,
        model: model.name,
        size_bytes: model.size ?? 0,
        size_vram_bytes: 0,
        processor: "Ollama",
        context_length: null,
        expires_at: null,
        digest: model.digest ?? null,
      })),
      ollama_error: (models as any).error ?? null,
      external_processes: {
        tts_server: {
          running: voice.tts_available,
          managed_by_backend: false,
          pid: null,
          path: null,
          port_reachable: voice.tts_available,
          expected_path_match: null,
          note: "Read-only status from the current voice API",
        },
        whisper_cli: {
          running: false,
          pids: [],
          note: voice.stt_available ? "Available on demand" : (voice.stt_reason ?? "Unavailable"),
        },
      },
      policy: { phase: "read-only", auto_unload_enabled: false },
    };
  },
};
