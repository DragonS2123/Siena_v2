// Typed shapes mirroring the Siena_v2 Python backend (api/server.py).
// Kept in sync manually — there is no shared schema generation yet.

export interface OllamaStatus {
  connected: boolean;
  models: string[];
  error?: string;
}

export interface RegisteredTool {
  name: string;
  schema: unknown;
}

export interface RuntimeStatus {
  primary_model: string;
  code_model: string;
  delegate_models: Record<string, string>;
  ollama_host: string;
  ollama_status: OllamaStatus;
  registered_tools: RegisteredTool[];
  max_iterations: number;
  request_timeout_seconds: number;
  delegate_timeout_seconds: number;
  memory_paths: { short: string; long: string };
  log_path: string;
  web_search_provider: string;
  log_level: string;
  max_context_messages: number;
  num_ctx: number;
  num_predict: number;
  last_used_model: string | null;
  last_used_role: string | null;
  active_chat_model: string;
  // Runtime view resource meters (core/system_metrics.py). CPU/RAM are always
  // populated via psutil; cpu_ram_error is only set if psutil itself throws.
  cpu_percent: number | null;
  ram_total_gb: number | null;
  ram_used_gb: number | null;
  ram_available_gb: number | null;
  ram_percent: number | null;
  cpu_ram_error?: string;
  // VRAM is best-effort (nvidia-smi only) — vram_supported=false on AMD/no
  // NVIDIA driver, with a human-readable vram_reason instead of a fake number.
  vram_supported: boolean;
  vram_reason: string | null;
  vram_total_gb: number | null;
  vram_used_gb: number | null;
  vram_percent: number | null;
}

// GET /api/models — model registry (Phase 4D). routing_mode is the STATIC
// registry classification (config.MODEL_REGISTRY): "auto" (main_chat,
// default) | "auto_for_code" (code specialist, auto-routed only for explicit
// coding requests) | "explicit_only" (reviewer/critic, only on explicit
// request) | "manual_only" (qwen3.5:27b — never auto-selected; switching to
// it is the explicit human action POST /api/models/active, Phase 4E) |
// "tool" (OCR/translator, not part of chat routing at all). This is distinct
// from a single chat turn's routing_mode in ChatResponse (below), which is a
// per-request DECISION ("manual_active_chat_model" when the human-selected
// model is actually in use for that turn) — same word, different axis.
export interface ModelRegistryEntry {
  name: string;
  role: string;
  routing_mode: "auto" | "auto_for_code" | "explicit_only" | "manual_only" | "tool";
  enabled: boolean;
  description: string;
  status: "installed" | "missing" | "unknown";
  is_last_used: boolean;
  is_active_chat_model: boolean;
}

export interface ModelsResponse {
  models: ModelRegistryEntry[];
  ollama_connected: boolean;
  last_used_model: string | null;
  last_used_role: string | null;
  active_chat_model: string;
}

// GET/POST /api/models/active — manual active chat model switch (Phase 4E).
// allowed_manual_models is always exactly [MAIN_CHAT_MODEL, MANUAL_HEAVY_MODEL]
// (qwen3.5:9b / qwen3.5:27b) — ornith/coder/glm-ocr/translategemma can never
// appear here even though they're valid entries in ModelRegistryEntry.
export interface ActiveChatModelResponse {
  active_chat_model: string;
  allowed_manual_models: string[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string;
}

export interface ConversationsListResponse {
  conversations: ConversationSummary[];
  active_conversation_id: string | null;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  model: string | null;
  created_at: string;
  metadata: {
    status?: "processing" | "completed" | "failed" | string;
    error?: string | null;
    attachments?: StoredAttachmentMetadata[];
    ocr_results?: OcrResult[];
    vision_results?: VisionResult[];
    [key: string]: unknown;
  };
  attachments?: StoredAttachmentMetadata[];
}

export interface ConversationEvent {
  id: number;
  event: string;
  ts: string;
  [key: string]: unknown;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
  events: ConversationEvent[];
}

// Wire shape for POST /api/chat attachments — matches api/server.py's
// ChatAttachment pydantic model. `content` carries text/code/markdown/json/log
// content; `data_url` carries the base64 image payload for image attachments
// (Phase 4B — OCR'd server-side via glm-ocr, see ocr/glm_ocr_service.py).
export interface ChatAttachmentPayload {
  name: string;
  type: string;
  size?: string;
  lang?: string;
  mime?: string;
  content?: string;
  data_url?: string;
}

export type ChatTurnStatus = "processing" | "completed" | "failed";

export interface StoredAttachmentMetadata {
  id: string;
  kind: "image" | "text" | "document" | "unknown";
  source: "uploaded" | "generated";
  original_name: string;
  stored_filename: string;
  stored_relative_path: string;
  mime_type?: string | null;
  size_bytes: number;
  created_at: string;
  sha256?: string | null;
  url: string;
  client_type?: string | null;
  lang?: string | null;
  size_label?: string | null;
  ocr_status?: "ready" | "running" | "extracted" | "low_quality" | "failed" | "unavailable" | null;
  ocr_preview?: string | null;
  ocr_quality?: "ok" | "low_quality" | null;
  vision_status?: "described" | "failed" | "unavailable" | null;
  vision_preview?: string | null;
}

export interface OcrResult {
  name: string;
  status: "extracted" | "low_quality" | "failed" | "unavailable";
  chars?: number;
  preview?: string;
  quality?: "ok" | "low_quality";
  error?: string;
}

// Image scene/object understanding (qwen2.5vl) — separate from OcrResult
// above. Only present when the user's message actually triggered vision
// (see core/image_intent.py); "described" means the model produced a real
// visual description, "failed"/"unavailable" are honest failure statuses.
export interface VisionResult {
  name: string;
  status: "described" | "failed" | "unavailable";
  chars?: number;
  preview?: string;
  error?: string;
}

export interface ChatResponse {
  answer: string;
  conversation_id: string;
  message_id?: string;
  assistant_message_id?: string;
  attachments?: StoredAttachmentMetadata[];
  ocr_results?: OcrResult[];
  vision_results?: VisionResult[];
  // Phase 4D — which model actually answered this turn (see core/model_router.py).
  // model_used is never config.MANUAL_HEAVY_MODEL (qwen3.5:27b) — the router
  // has no code path that returns it, so manual_only is always false today.
  model_used?: string;
  model_role?: string;
  routing_reason?: string;
  routing_mode?: string;
  manual_only?: boolean;
}

// POST /api/translate — matches api/server.py's TranslateRequest/response
// (Phase 4C). Standalone, explicit-only — never called automatically by chat.
export interface TranslateResponse {
  ok: boolean;
  provider: string;
  source_lang: string;
  target_lang: string;
  translated_text: string;
  duration_ms: number;
  fallback_used: boolean;
}

export interface TraceEvent {
  ts?: string;
  event: string;
  level?: string;
  conversation_id?: string;
  [key: string]: unknown;
}

export interface TraceRecentResponse {
  events: TraceEvent[];
}

export interface LogsRecentResponse {
  entries: TraceEvent[];
}

export interface ShortMemoryEntry {
  id: string;
  created_at: string;
  text: string;
  source: string;
}

export interface ShortMemoryResponse {
  entries: ShortMemoryEntry[];
}

export interface LongMemoryEntry {
  id: number;
  created_at: string;
  updated_at?: string;
  text: string;
  category: string | null;
  importance: string | null;
  source?: string | null;
}

export interface LongMemoryResponse {
  entries: LongMemoryEntry[];
}

// POST /api/memory/long — Feedback row "Save-to-memory" (Chat tab), an
// explicit human-confirmed action. Writes through the same
// LongMemoryStore.save() as the long_memory_save tool.
export interface MemoryLongSaveResponse {
  saved: boolean;
  entry: LongMemoryEntry;
}

export interface SettingsPayload {
  primary_model: string;
  code_model: string;
  ollama_host: string;
  max_iterations: number;
  request_timeout_seconds: number;
  delegate_timeout_seconds: number;
  num_ctx: number;
  num_predict: number;
  max_context_messages: number;
  log_level: string;
  // Settings unfreeze pass (HANDOFF_v2.md) — real, persisted to
  // storage/settings.json, applied live (no restart needed). keep_alive/
  // model-lifecycle fields are deliberately not part of this payload yet.
  enable_ocr: boolean;
  enable_image_understanding: boolean;
  enable_translator: boolean;
  enable_code_specialist_auto: boolean;
  enable_reviewer_explicit: boolean;
  /** whisper.cpp default recognition language ("auto" | "ru" | "en"). */
  stt_language: string;
  // Settings Pass 2 — pure frontend UI/display preferences. No backend
  // behavioral effect; persisted purely so they survive a restart (see
  // config.py's own comment on these fields).
  appearance_theme: "dark" | "light" | "system" | string;
  accent_color: "sienna" | "slate" | "forest" | "amber" | "violet" | string;
  ui_font_size: "small" | "default" | "large" | string;
  ui_density: "comfortable" | "compact" | string;
  show_message_timestamps: boolean;
  show_typing_animation: boolean;
  copy_before_clear_chat: boolean;
  startup_page: "chat" | "runtime" | "settings" | string;
  code_font_size: "small" | "default" | "large" | string;
  code_line_wrap: boolean;
  // Settings Pass 3 — remaining code-display visibility toggles + the
  // experimental Stream-button visibility toggle (both pure frontend), and
  // one real addition: a soft chat-prompt language preference.
  code_syntax_highlighting: boolean;
  code_show_line_numbers: boolean;
  code_show_language_badge: boolean;
  code_show_copy_button: boolean;
  code_show_collapse_button: boolean;
  code_show_save_button: boolean;
  show_experimental_stream_button: boolean;
  preferred_response_language: "auto" | "ru" | "en" | string;
  // Real UI localization pass — application UI language only. Separate from
  // stt_language (voice input) and preferred_response_language (soft model
  // reply preference) above.
  interface_language: "en" | "ru" | string;
  // Presence layer (0.2.1, Phase 1) — real, persisted, applied live (see
  // presence/presence_service.py). allow_proactive_presence_messages
  // defaults false: Presence never creates chat messages on its own unless
  // a human explicitly opts in.
  enable_presence: boolean;
  allow_proactive_presence_messages: boolean;
  presence_idle_minutes: number;
  presence_max_messages_per_hour: number;
  presence_quiet_hours_enabled: boolean;
  presence_quiet_hours_start: string;
  presence_quiet_hours_end: string;
  presence_style: "calm" | "playful" | "minimal" | string;
  show_presence_card: boolean;
  // Presence Behavior Layer (0.2.1, Phase 2) — welcome-back / recent-event /
  // insert-to-composer gates, all real and persisted like Phase 1's fields.
  presence_show_welcome_back: boolean;
  presence_show_recent_event: boolean;
  presence_allow_insert_to_chat: boolean;
  presence_min_seconds_between_ui_messages: number;
}

// The latest UI-only presence event (welcome_back / say) shown in the
// Presence Card's "Latest event" block. `message` is the backend's canonical
// RU line; the frontend renders the localized text via the i18n key
// presence.event.<type>.<style>.<variant>, falling back to `message`.
export interface PresenceRecentEvent {
  type: "welcome_back" | "say" | string;
  style: "calm" | "playful" | "minimal" | string;
  variant: number;
  message: string;
  created_at: string;
}

// GET /api/presence/status | POST /api/presence/{ping,quiet,wake} — all
// return this same shape (presence/presence_state.py::PresenceState).
export type PresenceStateValue =
  | "available" | "idle" | "listening" | "thinking" | "speaking"
  | "quiet" | "offline" | "error";

export interface PresenceStatus {
  state: PresenceStateValue;
  message: string;
  last_user_activity_at: string | null;
  last_assistant_activity_at: string | null;
  last_presence_message_at: string | null;
  quiet_until: string | null;
  is_quiet_mode: boolean;
  uptime_seconds: number | null;
  current_activity: string | null;
  recent_event: PresenceRecentEvent | null;
}

// POST /api/presence/say — deterministic, local, no-LLM status line (never
// persisted as a conversation message by the backend).
export interface PresenceSayResponse {
  message: string | null;
  throttled: boolean;
  style: string;
  variant: number | null;
}

export interface CandidateMemoryEntry {
  id: number;
  created_at: string;
  updated_at: string;
  observation: string;
  insight: string;
  reflection: string;
  proposed_memory: string;
  confidence: number | null;
  category: string | null;
  status: "pending" | "promoted" | "rejected" | "later" | string;
}

// GET /api/insights — human-in-the-loop review queue for candidate_memory_create
// (core cognitive cycle: Observation -> Insight -> Reflection -> Candidate Memory).
// The model can only ever create candidates; promote/reject/later/delete are
// REST-only actions triggered by an explicit human click (api/server.py).
export interface InsightsResponse {
  entries: CandidateMemoryEntry[];
}

export interface InsightPromoteResponse {
  promoted: number;
  long_memory_entry: LongMemoryEntry;
}

export interface InsightRejectResponse {
  rejected: number;
}

export interface InsightDeferResponse {
  deferred: number;
}

export interface InsightDeleteResponse {
  deleted: number;
}

// Voice profiles (voice/voice_profiles.py, storage/voice_profiles.json) —
// real, backend-persisted TTS timbre presets, entirely separate from
// storage/settings.json. get_active_profile() is read live by synthesis
// (see api/server.py's VoiceService construction), so activating a profile
// here changes future Speak/Stream output immediately, no restart needed.
export interface VoiceProfile {
  id: string;
  name: string;
  provider: string;
  model_repo: string;
  language: string;
  speaker: string;
  instruct: string;
  created_at: string;
  updated_at: string;
}

export interface VoiceProfilesResponse {
  profiles: VoiceProfile[];
}

// GET /api/voice/status — read-only, safe to poll on demand. TTS playback
// (Phase 1, HANDOFF_v2.md) uses this only to show whether qwen3_tts_ggml_vulkan
// / its Silero fallback are reachable before offering the Speak button as
// more than a "try it and see" action. STT fields (Phase 1 backend, Phase 2
// mic UI) reflect the whisper.cpp provider — stt_available gates whether the
// mic button is enabled at all; stt_reason is the honest explanation shown
// in its tooltip when it isn't.
export interface VoiceStatusResponse {
  stt_available: boolean;
  stt_model: string;
  stt_device: string;
  stt_provider?: string;
  stt_reason?: string | null;
  stt_backend_hint?: string;
  tts_available: boolean;
  tts_provider: string;
  tts_fallback_provider: string | null;
  tts_language: string;
  tts_voice: string;
}

// POST /api/voice/stt/transcribe (Phase 1 backend, Phase 2 mic UI,
// HANDOFF_v2.md) — whisper.cpp STT. confidence is always null today (the
// CLI doesn't expose a per-utterance confidence score); kept in the type so
// a future backend addition doesn't require a client-side shape change.
export interface TranscribeSpeechResponse {
  text: string;
  language: string;
  provider: string;
  backend: "vulkan" | "cpu_fallback" | "cpu" | string;
  elapsed_ms: number;
  confidence: number | null;
}

// POST /api/voice/synthesize — text-to-speech via VoiceService (primary
// provider configured in config.VOICE_TTS_PROVIDER, currently
// qwen3_tts_ggml_vulkan, with an automatic Silero fallback on failure).
// `provider` is which one actually produced this audio — always trust it
// over assuming the primary spoke, since the fallback is silent by design
// on the backend side (api/server.py never 503s just because the primary
// failed, as long as the fallback works).
export interface VoiceSynthesizeResponse {
  ok: boolean;
  audio_url: string;
  audio_path: string;
  duration_sec: number;
  provider: string;
}

// POST /api/voice/tts/stream (Phase 2/3, experimental, HANDOFF_v2.md) — raw
// PCM proxied straight from qwentts.cpp's tts-server.exe, qwen3_tts_ggml_vulkan
// only, no Silero fallback. Diagnostic X-Siena-TTS-* headers describe the
// format actually used; if a header is missing (unexpected, but the client
// must not crash on it) sienaClient falls back to the documented defaults
// (sampleRate=24000, channels=1, format="pcm").
export interface StreamSpeechResult {
  body: ReadableStream<Uint8Array>;
  sampleRate: number;
  channels: number;
  format: string;
  provider: string | null;
}

// GET /api/resources/status — Resource/Model Lifecycle Phase 1
// (HANDOFF_v2.md): honest visibility only, no automatic policy. Three
// genuinely separate things: Ollama's own loaded models, the external
// qwen3_tts_ggml_vulkan tts-server.exe subprocess (which can outlive the
// backend's own handle to it after a restart/reload), and whisper.cpp's
// normally-ephemeral whisper-cli.exe.
export interface OllamaLoadedModel {
  name: string;
  model: string;
  size_bytes: number;
  size_vram_bytes: number;
  processor: string;
  context_length: number | null;
  expires_at: string | null;
  digest: string | null;
}

export interface TtsServerStatus {
  running: boolean | null;
  managed_by_backend: boolean;
  pid: number | null;
  path: string | null;
  port_reachable: boolean | null;
  expected_path_match: boolean | null;
  process_count?: number;
  note?: string;
}

export interface WhisperCliStatus {
  running: boolean;
  pids: number[];
  note: string;
}

export interface ResourcesStatusResponse {
  ollama_available: boolean;
  ollama_loaded_models: OllamaLoadedModel[];
  ollama_error: string | null;
  external_processes: {
    tts_server: TtsServerStatus;
    whisper_cli: WhisperCliStatus;
  };
  policy: {
    phase: string;
    auto_unload_enabled: boolean;
  };
}

// POST /api/voice/tts/stop?force=<bool> — see api/server.py's docstring for
// the exact honesty contract: ok=false with external_process_found=true
// means "found it but didn't touch it" (no force), never a false success.
export interface TtsStopResponse {
  ok: boolean;
  running: boolean;
  managed_by_backend: boolean;
  external_process_found: boolean;
  message?: string;
  killed_pids?: number[];
  skipped_path_mismatch_pids?: number[];
}

// POST /api/models/lifecycle/unload
export type ModelLifecycleUnloadTarget = "tool_models" | "all_non_chat" | "specific";

export interface ModelLifecycleUnloadResult {
  model: string;
  attempted: boolean;
  ok: boolean;
  note: string | null;
  error: string | null;
}

export interface ModelLifecycleUnloadResponse {
  ok: boolean;
  target: ModelLifecycleUnloadTarget;
  results: ModelLifecycleUnloadResult[];
}
