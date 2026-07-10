import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  MessageSquare, Settings, Plus, Send, Copy, Check,
  ChevronDown, ChevronUp, Terminal, Brain, Activity,
  Cpu, Zap, Moon, Search, MoreHorizontal, Menu, Code2,
  Mic, CheckCircle, Hash, Database, ScrollText,
  Bug, Globe, Volume2, AlertTriangle, Filter,
  RefreshCw, PanelRight, X, Info,
  ChevronRight, Workflow, Layers, Save,
  ThumbsUp, ThumbsDown, RotateCcw, BookmarkPlus,
  Paperclip, FileText, FileCode, ImageIcon, FolderOpen, FileJson, Languages,
  Lightbulb, Trash2, Loader2, Square, VolumeX, Waves, Headphones, Download,
  Sparkles,
} from "lucide-react";

import { sienaClient, API_BASE_URL } from "../api/sienaClient";
import type { ChatTurnStatus, ResourcesStatusResponse, RuntimeStatus, SettingsPayload, StoredAttachmentMetadata, TraceEvent, VoiceProfile } from "../api/types";
import { fromStoredAttachment, useChat, type ChatTurn, type SendResult } from "../hooks/useChat";
import { useConversations } from "../hooks/useConversations";
import { useInsights, type InsightStatusFilter } from "../hooks/useInsights";
import { useLogs } from "../hooks/useLogs";
import { useLongMemory, useShortMemory } from "../hooks/useMemory";
import { useModels } from "../hooks/useModels";
import { useResourcesStatus } from "../hooks/useResourcesStatus";
import { RuntimeStatusProvider, useRuntimeStatus } from "../hooks/useRuntimeStatus";
import { UiPreferencesProvider, useUiPreferences } from "../hooks/useUiPreferences";
import type { StartupPage } from "../hooks/useUiPreferences";
import { usePresence } from "../hooks/usePresence";
import { useSettings } from "../hooks/useSettings";
import { useSpeech, type SpeechState, type UseSpeechResult } from "../hooks/useSpeech";
import { useStreamingSpeech, type UseStreamingSpeechResult } from "../hooks/useStreamingSpeech";
import { useVoiceConversation } from "../hooks/useVoiceConversation";
import { useVoiceRecorder } from "../hooks/useVoiceRecorder";
import { useVoiceStatus } from "../hooks/useVoiceStatus";
import { TraceSocketProvider, useTraceSocket } from "../hooks/useTraceSocket";

// Injected at build time by vite.config.ts's `define` from package.json's
// own version field — real, not a hand-typed placeholder (Settings >
// Developer > About).
declare const __APP_VERSION__: string;
const APP_VERSION = __APP_VERSION__;

// ─── Types ─────────────────────────────────────────────────────────────────────

type AppView = "splash" | "main";
type MainView =
  | "chat" | "tool-trace" | "short-memory" | "long-memory" | "insights"
  | "logs" | "models" | "runtime" | "debug" | "settings";
type ModelState = "idle" | "thinking" | "generating" | "tool";
type SettingsSection =
  | "appearance" | "model" | "startup" | "tools"
  | "code" | "voice" | "language" | "presence" | "developer";
type AttachmentType = "image" | "text" | "code" | "markdown" | "json" | "log";
type VoiceState =
  | "idle" | "requesting-permission" | "listening" | "speaking-user" | "transcribing"
  | "thinking" | "speaking-siena" | "fallback" | "error-mic" | "error-tts"
  // Voice Conversation Mode only (experimental, Phase 3, HANDOFF_v2.md) —
  // push-to-talk never produces these three.
  | "speech-detected" | "silence-wait" | "finalizing-wait";

type OcrStatus = "ready" | "running" | "extracted" | "low_quality" | "failed" | "unavailable";
// No "ready"/"running" state — vision is intent-gated server-side (only
// called when the message actually asks what the image shows, see
// core/image_intent.py), so the client can't know in advance whether it
// will run at all. Only ever set once a real vision_results entry comes
// back with send()'s response; undefined means "not requested this turn",
// never shown as a fake idle status.
type VisionStatus = "described" | "failed" | "unavailable";

export interface Attachment {
  id: string;
  type: AttachmentType;
  name: string;
  size: string;
  lang?: string;
  dataUrl?: string;
  url?: string;
  source?: "uploaded" | "generated";
  persisted?: boolean;
  mime?: string;
  preview?: string;
  /** Full file/paste content for text/code/markdown/json/log — sent to the
   * backend and injected into the model prompt. Never set for images. */
  content?: string;
  /** Image attachments only — glm-ocr status, set once send() resolves (see useChat.ts). */
  ocrStatus?: OcrStatus;
  ocrPreview?: string;
  ocrQuality?: "ok" | "low_quality";
  /** Image attachments only — qwen2.5vl scene/object understanding status,
   * set once send() resolves, only when vision was actually requested this
   * turn (see useChat.ts / VisionStatus above). */
  visionStatus?: VisionStatus;
  visionPreview?: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  attachments?: Attachment[];
  status?: ChatTurnStatus;
  error?: string | null;
}

// ─── Nav config ────────────────────────────────────────────────────────────────

const NAV_PRIMARY: { id: MainView; labelKey: string; icon: React.ElementType }[] = [
  { id: "chat", labelKey: "nav.chat", icon: MessageSquare },
  { id: "tool-trace", labelKey: "nav.toolTrace", icon: Workflow },
  { id: "short-memory", labelKey: "nav.shortMemory", icon: Zap },
  { id: "long-memory", labelKey: "nav.longMemory", icon: Database },
  { id: "insights", labelKey: "nav.insights", icon: Lightbulb },
  { id: "logs", labelKey: "nav.logs", icon: ScrollText },
  { id: "models", labelKey: "nav.models", icon: Cpu },
  { id: "runtime", labelKey: "nav.runtime", icon: Activity },
];

const NAV_SECONDARY: { id: MainView; labelKey: string; icon: React.ElementType }[] = [
  { id: "debug", labelKey: "nav.debug", icon: Bug },
  { id: "settings", labelKey: "nav.settings", icon: Settings },
];

// Voice constants

const VOICE_LABELS: Record<VoiceState, { primary: string; sub: string }> = {
  idle:                    { primary: "",                      sub: "" },
  "requesting-permission": { primary: "Requesting microphone…", sub: "Waiting for permission" },
  listening:               { primary: "Listening…",            sub: "whisper.cpp · speak now" },
  "speaking-user":         { primary: "Hearing you…",          sub: "whisper.cpp · live input" },
  transcribing:            { primary: "Transcribing…",         sub: "whisper.cpp processing" },
  thinking:                { primary: "Processing…",           sub: "Siena is thinking" },
  "speaking-siena":        { primary: "Siena is speaking…",   sub: "faster_qwen3-tts streaming" },
  fallback:                { primary: "Siena is speaking…",   sub: "Silero fallback active" },
  "error-mic":             { primary: "Transcription failed",  sub: "Check microphone / STT status" },
  "error-tts":             { primary: "TTS provider failed",  sub: "Check Voice settings" },
  "speech-detected":       { primary: "I hear you…",          sub: "Conversation mode · speak freely" },
  "silence-wait":          { primary: "Waiting for you…",     sub: "Conversation mode · take your time" },
  "finalizing-wait":       { primary: "Finishing phrase…",    sub: "Conversation mode · still time to continue" },
};

const VOICE_ARC_COLOR: Record<VoiceState, string> = {
  idle:                    "rgba(240,235,227,0.2)",
  "requesting-permission": "rgba(240,235,227,0.4)",
  listening:               "rgba(240,235,227,0.55)",
  "speaking-user":         "rgba(240,235,227,0.88)",
  transcribing:            "rgba(196,100,74,0.65)",
  thinking:                "rgba(196,100,74,0.42)",
  "speaking-siena":"#c4644a",
  fallback:        "#d4975e",
  "error-mic":     "#ef4444",
  "speech-detected": "rgba(240,235,227,0.88)",
  "silence-wait":    "rgba(240,235,227,0.5)",
  "finalizing-wait": "rgba(196,100,74,0.5)",
  "error-tts":     "#ef4444",
};

// ─── Attachment utilities ──────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function detectLang(text: string): string {
  const t = text.trimStart();
  if (/^(import |from |def |class |async def |@)/.test(t)) return "python";
  if (/(const |let |var |function |=>|interface |type )/.test(t)) return "typescript";
  if (/^(#include|int main|void )/.test(t)) return "cpp";
  if (/^(package |func |import \()/.test(t)) return "go";
  return "text";
}

function isLikelyCode(text: string): boolean {
  const t = text.trimStart();
  return /^(import |from |def |class |async |const |let |var |function |#include|package )/.test(t)
    || (text.includes("{") && text.includes("}") && text.includes(":"))
    || text.split("\n").length > 4 && text.includes("    ");
}

// ─── Attachment classification & limits ────────────────────────────────────────
// Mirrored server-side in config.py (MAX_ATTACHMENTS_PER_MESSAGE etc.) — the
// backend is the authoritative enforcement, these are the UX-side copies.

const MAX_ATTACHMENTS_PER_MESSAGE = 5;
const MAX_ATTACHMENT_TEXT_CHARS = 20_000;
const MAX_TOTAL_ATTACHMENT_TEXT_CHARS = 60_000;

// Settings unfreeze pass (HANDOFF_v2.md) — auto-speak's default is a pure
// frontend behavior (the backend has no concept of it), so it's persisted
// via localStorage rather than routed through POST /api/settings, unlike
// stt_language/enable_*/log_level below which the backend actually reads.
const AUTO_SPEAK_DEFAULT_KEY = "siena.autoSpeakDefault";
// Same rationale as AUTO_SPEAK_DEFAULT_KEY above: this only decides what
// FeedbackRow's translate() sends as TranslateRequest.preserve_formatting on
// its next call — a frontend request-shaping preference, not a config.py
// default, so it doesn't need a POST /api/settings round trip.
const TRANSLATE_PRESERVE_FORMATTING_KEY = "siena.translatePreserveFormatting";

function storedAttachmentsFromMessage(message: { attachments?: StoredAttachmentMetadata[]; metadata?: Record<string, unknown> }): Attachment[] | undefined {
  const direct = message.attachments;
  const metadataAttachments = message.metadata?.attachments;
  const status = message.metadata?.status;
  const stored = Array.isArray(direct)
    ? direct
    : Array.isArray(metadataAttachments)
      ? (metadataAttachments as StoredAttachmentMetadata[])
      : [];
  if (stored.length === 0) return undefined;
  return stored.map((item) => {
    const attachment = fromStoredAttachment(item);
    if (status === "processing" && attachment.type === "image" && !attachment.ocrStatus) {
      return { ...attachment, ocrStatus: "running" };
    }
    return attachment;
  });
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);
const JSON_EXTENSIONS = new Set(["json"]);
const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);
const LOG_EXTENSIONS = new Set(["log"]);
const CODE_EXTENSIONS = new Set([
  "py", "js", "jsx", "ts", "tsx", "rs", "go", "cpp", "cc", "c", "h", "hpp",
  "java", "sh", "bash", "yaml", "yml", "rb", "php", "cs", "kt", "swift", "sql", "css", "html", "xml",
]);
const CODE_LANG_BY_EXT: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  rs: "rust", go: "go", cpp: "cpp", cc: "cpp", c: "c", h: "c", hpp: "cpp", java: "java",
  sh: "bash", bash: "bash", yaml: "yaml", yml: "yaml", rb: "ruby", php: "php", cs: "csharp",
  kt: "kotlin", swift: "swift", sql: "sql", css: "css", html: "html", xml: "xml",
};

/**
 * Classifies a picked/dropped file by extension/mime into one of the
 * supported attachment types. Returns null for anything we don't know how to
 * read meaningfully (pdf/doc/binary/etc.) — callers must reject those with a
 * visible "unsupported" error rather than silently attaching garbage.
 */
function classifyAttachment(filename: string, mime: string): AttachmentType | null {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  if (mime.startsWith("image/") || IMAGE_EXTENSIONS.has(ext)) return "image";
  if (JSON_EXTENSIONS.has(ext)) return "json";
  if (MARKDOWN_EXTENSIONS.has(ext)) return "markdown";
  if (LOG_EXTENSIONS.has(ext)) return "log";
  if (CODE_EXTENSIONS.has(ext)) return "code";
  if (ext === "txt" || mime.startsWith("text/")) return "text";
  return null;
}

function totalAttachmentTextChars(list: Attachment[]): number {
  return list.reduce((sum, a) => sum + (a.content?.length ?? 0), 0);
}

// ─── Shared UI primitives ──────────────────────────────────────────────────────

function Badge({ label, variant = "neutral" }: { label: string; variant?: string }) {
  const styles: Record<string, string> = {
    ok: "bg-green-400/10 text-green-400",
    warn: "bg-amber-400/10 text-amber-400",
    error: "bg-red-400/10 text-red-400",
    neutral: "bg-white/[0.06] text-[#6b5f57]",
    accent: "bg-[#c4644a]/12 text-[#c4644a]",
    loaded: "bg-green-400/10 text-green-400",
    standby: "bg-amber-400/10 text-amber-400",
  };
  return (
    <span className={`inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium ${styles[variant] ?? styles.neutral}`}>
      {label}
    </span>
  );
}

function SectionHeader({ title, desc, action }: { title: string; desc?: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between mb-5">
      <div>
        <h3 className="text-sm font-semibold text-[#f0ebe3]">{title}</h3>
        {desc && <p className="text-xs text-[#6b5f57] mt-0.5">{desc}</p>}
      </div>
      {action}
    </div>
  );
}

function SettingsCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/[0.06] overflow-hidden">
      <div className="px-4 py-2 bg-white/[0.02] border-b border-white/[0.05]">
        <span className="text-[10px] uppercase tracking-[0.1em] font-semibold text-[#6b5f57]">{title}</span>
      </div>
      <div className="px-4 py-3.5 space-y-3">{children}</div>
    </div>
  );
}

function LocalOnlyNotice({ label = "This section is local-only UI state. Backend settings are not connected yet." }: { label?: string }) {
  return (
    <div className="px-3 py-2 rounded-lg border border-amber-400/15 bg-amber-400/05 text-[10px] text-amber-400">
      {label}
    </div>
  );
}

// Uncontrolled by default (existing decorative demo usages elsewhere in
// Settings keep working unchanged). Pass `checked`+`onChange` together to
// switch it into controlled mode for a real, backend-connected setting —
// see ToolSettings/CodeSettings/VoiceSettings/DeveloperSettings below.
function Toggle({ label, sub, defaultChecked = false, checked, onChange, disabled = false, badge }: {
  label: string; sub?: string; defaultChecked?: boolean;
  checked?: boolean; onChange?: (value: boolean) => void; disabled?: boolean; badge?: React.ReactNode;
}) {
  const [internalOn, setInternalOn] = useState(defaultChecked);
  const isControlled = checked !== undefined;
  const on = isControlled ? checked : internalOn;
  const toggle = () => {
    if (disabled) return;
    if (isControlled) onChange?.(!on);
    else setInternalOn(v => !v);
  };
  return (
    <div className={`flex items-center justify-between gap-4 ${disabled ? "opacity-40" : ""}`}>
      <div>
        <div className="flex items-center gap-1.5">
          <span className={`text-xs text-[#a89f96] select-none ${disabled ? "" : "cursor-pointer"}`} onClick={toggle}>{label}</span>
          {badge}
        </div>
        {sub && <p className="text-[10px] text-[#4b4540] mt-px">{sub}</p>}
      </div>
      <button onClick={toggle} disabled={disabled} className={`relative w-8 h-[18px] rounded-full transition-colors duration-200 shrink-0 ${on ? "bg-[#c4644a]" : "bg-white/[0.1]"} ${disabled ? "cursor-not-allowed" : ""}`}>
        <motion.div animate={{ x: on ? 14 : 2 }} transition={{ duration: 0.15, ease: "easeInOut" }}
          className="absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow-sm" />
      </button>
    </div>
  );
}

function SliderRow({ label, min, max, defaultValue, unit }: { label: string; min: number; max: number; defaultValue: number; unit?: string }) {
  const [val, setVal] = useState(defaultValue);
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-xs text-[#8a7f75]">{label}</span>
      <div className="flex items-center gap-2">
        <input type="range" min={min} max={max} value={val} onChange={e => setVal(+e.target.value)} className="w-24 accent-[#c4644a]" />
        <span className="text-xs text-[#c8c0b7] w-14 text-right tabular-nums">{val}{unit}</span>
      </div>
    </div>
  );
}

function NumberSetting({ label, value, onChange, min }: { label: string; value: number; onChange: (value: number) => void; min?: number }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-xs text-[#8a7f75]">{label}</span>
      <input
        type="number"
        min={min}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-28 bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2.5 py-1.5 outline-none text-right font-mono"
      />
    </div>
  );
}

function IconBtn({ icon: Icon, onClick, active }: { icon: React.ElementType; onClick?: () => void; active?: boolean }) {
  return (
    <button onClick={onClick} className={`p-2 rounded-lg transition-colors ${active ? "text-[#c4644a] bg-[#c4644a]/10" : "text-[#3a342e] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
      <Icon size={14} />
    </button>
  );
}

function ViewShell({ children }: { children: React.ReactNode }) {
  return <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 [scrollbar-width:none]">{children}</div>;
}

function ViewHeader({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-6 py-3 border-b border-white/[0.05] shrink-0">
      <div>
        <h2 className="text-sm font-semibold text-[#f0ebe3]">{title}</h2>
        {sub && <p className="text-[10px] text-[#6b5f57] mt-px">{sub}</p>}
      </div>
      {children && <div className="flex items-center gap-1">{children}</div>}
    </div>
  );
}

// ─── Syntax highlighter ────────────────────────────────────────────────────────

const PY_KW = new Set(["import","from","async","await","def","class","return","yield","for","in","if","else","elif","try","except","raise","with","as","and","or","not","True","False","None","pass","break","continue","lambda","while","property","list","str","int","dict","Union","self"]);
const JS_KW = new Set(["import","export","from","const","let","var","function","async","await","return","if","else","for","while","class","extends","new","this","typeof","interface","type","enum","default","true","false","null","undefined","void","readonly","static","of"]);

type TT = "kw"|"str"|"num"|"fn"|"type"|"comment"|"deco"|"op"|"plain";
const TC: Record<TT, string> = {
  kw: "text-[#c084fc]", str: "text-[#86c98e]", num: "text-[#e6956a]",
  fn: "text-[#7dd3fc]", type: "text-[#fbbf24]", comment: "text-[#5a5550] italic",
  deco: "text-[#fb923c]", op: "text-[#6b7280]", plain: "text-[#d8d0c7]",
};

function tokenizeLine(line: string, lang: string) {
  const kws = lang === "python" ? PY_KW : JS_KW;
  const out: { t: TT; v: string }[] = [];
  let i = 0;
  while (i < line.length) {
    if ((lang === "python" && line[i] === "#") || (lang !== "python" && line.slice(i, i + 2) === "//")) {
      out.push({ t: "comment", v: line.slice(i) }); break;
    }
    if ('"\'`'.includes(line[i])) {
      const q = line[i]; let j = i + 1;
      while (j < line.length && line[j] !== q) { if (line[j] === "\\") j++; j++; }
      out.push({ t: "str", v: line.slice(i, j + 1) }); i = j + 1; continue;
    }
    if (/\d/.test(line[i]) && (i === 0 || !/\w/.test(line[i - 1]))) {
      let j = i; while (j < line.length && /[\d._xXa-fA-F]/.test(line[j])) j++;
      out.push({ t: "num", v: line.slice(i, j) }); i = j; continue;
    }
    if (line[i] === "@") {
      let j = i + 1; while (j < line.length && /\w/.test(line[j])) j++;
      out.push({ t: "deco", v: line.slice(i, j) }); i = j; continue;
    }
    if (/[a-zA-Z_]/.test(line[i])) {
      let j = i; while (j < line.length && /\w/.test(line[j])) j++;
      const w = line.slice(i, j);
      const t: TT = kws.has(w) ? "kw" : j < line.length && line[j] === "(" ? "fn" : /^[A-Z]/.test(w) ? "type" : "plain";
      out.push({ t, v: w }); i = j; continue;
    }
    out.push({ t: "op", v: line[i] }); i++;
  }
  return out;
}

const CODE_FONT_SIZE_PX: Record<string, string> = { small: "text-[11px]", default: "text-[13px]", large: "text-[15px]" };
const CODE_LANG_EXTENSIONS: Record<string, string> = {
  javascript: "js", typescript: "ts", jsx: "jsx", tsx: "tsx", python: "py", json: "json",
  bash: "sh", shell: "sh", sh: "sh", html: "html", css: "css", sql: "sql", yaml: "yml",
  markdown: "md", rust: "rs", go: "go", java: "java", cpp: "cpp", c: "c", csharp: "cs",
};

function SyntaxHighlight({ code, lang, fontSize = "default", wrap = false, highlight = true, showLineNumbers = true }: {
  code: string; lang: string; fontSize?: string; wrap?: boolean; highlight?: boolean; showLineNumbers?: boolean;
}) {
  return (
    <div className={`${CODE_FONT_SIZE_PX[fontSize] ?? CODE_FONT_SIZE_PX.default} leading-[1.65] font-mono`}>
      {code.split("\n").map((line, li) => (
        <div key={li} className="flex min-h-[1.65em]">
          {showLineNumbers && <span className="select-none w-8 text-right pr-4 text-[#3a342e] shrink-0 text-xs leading-[1.65]">{li + 1}</span>}
          <span className={wrap ? "flex-1 whitespace-pre-wrap break-words" : "flex-1 whitespace-pre"}>
            {highlight
              ? tokenizeLine(line, lang).map((tok, ti) => (
                  <span key={ti} className={TC[tok.t]}>{tok.v}</span>
                ))
              : <span className="text-[#c8c0b7]">{line}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Code block ────────────────────────────────────────────────────────────────

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const { prefs, t } = useUiPreferences();
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const copy = useCallback(async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  }, [code]);

  // Real client-side download — no backend or file-system access involved,
  // just a Blob + a throwaway <a download>, same as any "export" button.
  const saveToFile = useCallback(() => {
    const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const ext = CODE_LANG_EXTENSIONS[lang.toLowerCase()] ?? "txt";
    const a = document.createElement("a");
    a.href = url;
    a.download = `snippet.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  }, [code, lang]);

  const actions: { label: string; Icon: typeof Copy; onClick: () => void; active?: boolean }[] = [];
  if (prefs.codeShowCollapseButton) {
    actions.push({ label: collapsed ? t("codeBlock.expand") : t("codeBlock.collapse"), Icon: collapsed ? ChevronDown : ChevronUp, onClick: () => setCollapsed(c => !c) });
  }
  if (prefs.codeShowCopyButton) {
    actions.push({ label: copied ? t("codeBlock.copied") : t("codeBlock.copy"), Icon: copied ? Check : Copy, onClick: copy, active: copied });
  }
  if (prefs.codeShowSaveButton) {
    actions.push({ label: t("codeBlock.save"), Icon: Save, onClick: saveToFile });
  }

  return (
    <div className="mt-3 rounded-xl overflow-hidden border border-white/[0.07] bg-[#0f0e0c]">
      <div className="flex items-center justify-between px-4 py-2 bg-[#181512] border-b border-white/[0.06]">
        {prefs.codeShowLanguageBadge ? (
          <div className="flex items-center gap-2">
            <Terminal size={11} className="text-[#c4644a]" />
            <span className="text-[10px] font-mono uppercase tracking-widest text-[#6b5f57]">{lang}</span>
          </div>
        ) : <div />}
        <div className="flex items-center gap-0.5">
          {actions.map(b => (
            <button key={b.label} onClick={b.onClick}
              className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors ${
                b.active ? "text-green-400" : "text-[#6b5f57] hover:text-[#d8d0c7] hover:bg-white/[0.05]"
              }`}>
              <b.Icon size={11} />{b.label}
            </button>
          ))}
        </div>
      </div>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2, ease: "easeInOut" }} className="overflow-hidden">
            <div className="px-3 py-4 overflow-x-auto [scrollbar-width:thin]">
              <SyntaxHighlight code={code} lang={lang} fontSize={prefs.codeFontSize} wrap={prefs.codeLineWrap}
                highlight={prefs.codeSyntaxHighlighting} showLineNumbers={prefs.codeShowLineNumbers} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Attachment chip (inside messages and composer) ────────────────────────────

const OCR_STATUS_LABEL: Record<OcrStatus, { text: string; color: string }> = {
  ready: { text: "OCR ready", color: "text-[#6b5f57]" },
  running: { text: "OCR running…", color: "text-amber-400/90" },
  extracted: { text: "OCR extracted", color: "text-green-400/90" },
  low_quality: { text: "OCR low quality", color: "text-amber-400/90" },
  failed: { text: "OCR failed", color: "text-red-400/90" },
  unavailable: { text: "glm-ocr not installed", color: "text-red-400/90" },
};

const VISION_STATUS_LABEL: Record<VisionStatus, { text: string; color: string }> = {
  described: { text: "Vision described", color: "text-green-400/90" },
  failed: { text: "Vision failed", color: "text-red-400/90" },
  unavailable: { text: "qwen2.5vl not installed", color: "text-red-400/90" },
};

const ATTACH_ICON: Record<AttachmentType, React.ElementType> = {
  image: ImageIcon, text: FileText, code: FileCode, markdown: FileText, json: FileJson, log: FileText,
};
const ATTACH_COLOR: Record<AttachmentType, string> = {
  image: "text-[#7dd3fc]", text: "text-[#8a7f75]", code: "text-[#c084fc]", markdown: "text-[#8a7f75]", json: "text-[#fbbf24]", log: "text-[#6b5f57]",
};

function AttachChip({ a, onRemove, compact }: { a: Attachment; onRemove?: () => void; compact?: boolean }) {
  const Icon = ATTACH_ICON[a.type];
  const [expanded, setExpanded] = useState(false);
  const [imageMissing, setImageMissing] = useState(false);
  const imageSrc = a.dataUrl ?? a.url;

  if (a.type === "image" && imageSrc) {
    return (
      <div className="relative flex items-start gap-2 bg-[#252118] border border-white/[0.08] rounded-xl overflow-hidden pr-2.5 max-w-[320px]">
        {imageMissing ? (
          <div className="w-12 h-12 shrink-0 flex items-center justify-center bg-red-400/10 text-red-400">
            <AlertTriangle size={14} />
          </div>
        ) : (
          <img src={imageSrc} alt={a.name} onError={() => setImageMissing(true)} className="w-12 h-12 object-cover shrink-0" />
        )}
        <div className="min-w-0 py-1">
          <div className="text-[11px] font-medium text-[#c8c0b7] truncate">{a.name}</div>
          <div className="text-[10px] text-[#4b4540]">image · {a.size}</div>
          {imageMissing && <div className="text-[9px] mt-0.5 text-red-400/90">Attachment content missing</div>}
          <div className={`text-[9px] mt-0.5 ${OCR_STATUS_LABEL[a.ocrStatus ?? "ready"].color}`}>
            {OCR_STATUS_LABEL[a.ocrStatus ?? "ready"].text}
          </div>
          {a.ocrPreview && (
            <details className="mt-1 max-w-[240px]">
              <summary className="cursor-pointer text-[9px] text-[#6b5f57] hover:text-[#c8c0b7]">OCR preview</summary>
              <pre className="mt-1 text-[10px] leading-relaxed text-[#c8c0b7] whitespace-pre-wrap break-words max-h-28 overflow-auto">{a.ocrPreview}</pre>
            </details>
          )}
          {a.visionStatus && (
            <div className={`text-[9px] mt-0.5 ${VISION_STATUS_LABEL[a.visionStatus].color}`}>
              {VISION_STATUS_LABEL[a.visionStatus].text}
            </div>
          )}
          {a.visionPreview && (
            <details className="mt-1 max-w-[240px]">
              <summary className="cursor-pointer text-[9px] text-[#6b5f57] hover:text-[#c8c0b7]">Vision preview</summary>
              <pre className="mt-1 text-[10px] leading-relaxed text-[#c8c0b7] whitespace-pre-wrap break-words max-h-28 overflow-auto">{a.visionPreview}</pre>
            </details>
          )}
        </div>
        {onRemove && (
          <button onClick={onRemove} className="ml-1 shrink-0 text-[#4b4540] hover:text-red-400 transition-colors"><X size={11} /></button>
        )}
      </div>
    );
  }

  return (
    <div className={`bg-[#252118] border border-white/[0.08] rounded-xl overflow-hidden ${a.preview && !compact ? "max-w-xs" : ""}`}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        <Icon size={13} className={ATTACH_COLOR[a.type]} />
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-medium text-[#c8c0b7] truncate">{a.name}</div>
          <div className="text-[10px] text-[#4b4540]">{a.lang ? `${a.lang} · ` : ""}{a.size}</div>
        </div>
        {a.preview && !compact && (
          <button onClick={() => setExpanded(e => !e)} className="text-[#4b4540] hover:text-[#8a7f75] transition-colors">
            <ChevronDown size={11} className={`transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
        )}
        {onRemove && (
          <button onClick={onRemove} className="text-[#4b4540] hover:text-red-400 transition-colors"><X size={11} /></button>
        )}
      </div>
      <AnimatePresence initial={false}>
        {a.preview && expanded && (
          <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }}
            transition={{ duration: 0.15 }} className="overflow-hidden border-t border-white/[0.05]">
            <pre className="px-3 py-2 text-[10px] font-mono text-[#6b5f57] leading-relaxed whitespace-pre-wrap">{a.preview}</pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Feedback row (under assistant messages) ───────────────────────────────────

// Phase 4C — no shared "preferred language" state exists anywhere in this app
// yet (Settings screens are all local-only mocks, see LanguageSettings), so
// the per-message Translate button picks a target language heuristically:
// mostly-Cyrillic content -> translate to English, otherwise -> Russian.
// Matches the RU<->EN focus of translategemma:4b (see config.py).
function guessTranslateTarget(text: string): "ru" | "en" {
  const cyrillicCount = (text.match(/[а-яё]/gi) || []).length;
  return cyrillicCount > text.length * 0.15 ? "en" : "ru";
}

interface FeedbackRowProps {
  content: string;
  messageId: string;
  speech: UseSpeechResult;
  streaming: UseStreamingSpeechResult;
  conversationId: string | null;
  isLatestAssistant: boolean;
  retrying: boolean;
  retryDisabled: boolean;
  retryError?: string;
  onRetry: (messageId: string) => void;
}

function FeedbackRow({ content, messageId, speech, streaming, conversationId, isLatestAssistant, retrying, retryDisabled, retryError, onRetry }: FeedbackRowProps) {
  const { prefs, t } = useUiPreferences();
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [copied, setCopied] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [translation, setTranslation] = useState<{ text: string; source: string; target: string; provider: string; fallbackUsed: boolean } | null>(null);
  const [translateError, setTranslateError] = useState<string | null>(null);
  const [translateOpen, setTranslateOpen] = useState(false);

  // Save-to-memory (HANDOFF_v2.md) — explicit inline editor, never a silent
  // auto-save. Prefills with the user's own text selection if there is one
  // (window.getSelection() at the moment Save is clicked), otherwise a short
  // snippet of the message so there's always something sensible to edit
  // before confirming.
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveText, setSaveText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const openSaveEditor = () => {
    const selected = window.getSelection()?.toString().trim();
    const draft = selected && selected.length > 0 ? selected : (content.length > 300 ? `${content.slice(0, 300)}…` : content);
    setSaveText(draft);
    setSaveError(null);
    setSaved(false);
    setSaveOpen(true);
  };

  const cancelSave = () => {
    setSaveOpen(false);
    setSaveError(null);
  };

  const confirmSave = async () => {
    const text = saveText.trim();
    if (!text) return;
    setSaving(true);
    setSaveError(null);
    try {
      await sienaClient.saveToLongMemory(text, conversationId, messageId);
      setSaved(true);
      setSaveOpen(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save to memory");
    } finally {
      setSaving(false);
    }
  };

  const isThisMessage = speech.activeMessageId === messageId;
  const speakState: SpeechState = isThisMessage ? speech.state : "idle";
  const speakUsedFallback = isThisMessage && speech.state === "speaking" && speech.provider && speech.provider !== "qwen3_tts_ggml_vulkan";

  // Safe variant chosen deliberately (HANDOFF_v2.md): the stable WAV Speak
  // and the experimental PCM Stream Speak are never allowed to play at the
  // same time, in either direction — starting one always fully stops
  // whatever the other was doing first, rather than disabling a button or
  // silently letting two audio pipelines run together.
  const handleSpeakClick = () => {
    if (speakState === "speaking" || speakState === "preparing") {
      speech.stop();
    } else {
      streaming.stop();
      speech.speak(content, messageId);
    }
  };

  const isThisStreamMessage = streaming.activeMessageId === messageId;
  const streamState = isThisStreamMessage ? streaming.status : "idle";
  const streamActive = streamState === "preparing" || streamState === "streaming" || streamState === "stopping";

  const handleStreamClick = () => {
    if (streamActive) {
      streaming.stop();
    } else {
      speech.stop();
      streaming.streamSpeak(messageId, content);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  };

  const translate = async () => {
    if (translation) { setTranslateOpen(o => !o); return; }
    setTranslating(true); setTranslateError(null);
    try {
      const targetLang = guessTranslateTarget(content);
      const preserveFormatting = typeof window === "undefined" || window.localStorage.getItem(TRANSLATE_PRESERVE_FORMATTING_KEY) !== "0";
      const result = await sienaClient.translate(content, { targetLang, preserveFormatting });
      setTranslation({ text: result.translated_text, source: result.source_lang, target: result.target_lang, provider: result.provider, fallbackUsed: result.fallback_used });
      setTranslateOpen(true);
    } catch (err) {
      setTranslateError(err instanceof Error ? err.message : "Translation failed");
      setTranslateOpen(true);
    } finally {
      setTranslating(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-0.5 mt-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
        <button onClick={() => setVote(v => v === "up" ? null : "up")}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${vote === "up" ? "text-green-400 bg-green-400/10" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
          <ThumbsUp size={12} />
        </button>
        <button onClick={() => setVote(v => v === "down" ? null : "down")}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${vote === "down" ? "text-red-400 bg-red-400/10" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
          <ThumbsDown size={12} />
        </button>
        <div className="w-px h-3 bg-white/[0.07] mx-0.5" />
        <button onClick={copy}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${copied ? "text-green-400" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
          {copied ? <Check size={12} /> : <Copy size={12} />}
          <span>{copied ? t("common.copied") : t("common.copy")}</span>
        </button>
        <button
          onClick={() => onRetry(messageId)}
          disabled={!isLatestAssistant || retryDisabled}
          title={!isLatestAssistant ? t("common.retryLatestOnly") : undefined}
          className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04] disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent transition-colors">
          {retrying ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
          <span>{retrying ? t("common.retrying") : t("common.retry")}</span>
        </button>
        <button onClick={() => (saveOpen ? cancelSave() : openSaveEditor())}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${saved ? "text-[#c4644a]" : saveOpen ? "text-[#c4644a] bg-[#c4644a]/10" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
          <BookmarkPlus size={12} /><span>{saved ? t("common.saved") : t("common.save")}</span>
        </button>
        <button onClick={translate} disabled={translating}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors disabled:opacity-50 ${translateOpen ? "text-[#c4644a] bg-[#c4644a]/10" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"}`}>
          <Languages size={12} /><span>{translating ? t("common.translating") : t("common.translate")}</span>
        </button>
        <button onClick={handleSpeakClick}
          title={isThisMessage && speech.error ? speech.error : undefined}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${
            speakState === "speaking" ? "text-[#c4644a] bg-[#c4644a]/10"
            : speakState === "error" ? "text-red-400"
            : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"
          }`}>
          {speakState === "preparing" ? <Loader2 size={12} className="animate-spin" />
            : speakState === "speaking" ? <Square size={12} />
            : <Volume2 size={12} />}
          <span>
            {speakState === "preparing" ? t("common.preparing")
              : speakState === "speaking" ? t("common.stop")
              : speakState === "error" ? t("common.speakFailed")
              : t("common.speak")}
          </span>
        </button>
        {prefs.showExperimentalStreamButton && (
          <button onClick={handleStreamClick}
            title={
              (isThisStreamMessage && streaming.error) ? streaming.error
                : "Experimental: raw PCM streaming via /api/voice/tts/stream, qwen3_tts_ggml_vulkan only, no Silero fallback"
            }
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${
              streamState === "streaming" ? "text-[#c4644a] bg-[#c4644a]/10"
              : streamState === "error" ? "text-red-400"
              : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.04]"
            }`}>
            {streamState === "preparing" ? <Loader2 size={12} className="animate-spin" />
              : streamActive ? <Square size={12} />
              : <Waves size={12} />}
            <span>
              {streamState === "preparing" ? t("common.preparing")
                : streamState === "streaming" ? t("common.stopStream")
                : streamState === "error" ? t("common.streamFailed")
                : t("common.stream")}
            </span>
            <Badge label="exp" variant="warn" />
          </button>
        )}
      </div>
      {speakUsedFallback && (
        <p className="mt-1 text-[10px] text-amber-400/80">Playing via Silero fallback — qwen3_tts_ggml_vulkan was unavailable.</p>
      )}
      {isThisMessage && speakState === "error" && speech.error && (
        <p className="mt-1 text-[10px] text-red-400">{speech.error}</p>
      )}
      {isThisStreamMessage && streamState === "error" && streaming.error && (
        <p className="mt-1 text-[10px] text-red-400">Stream failed: {streaming.error}</p>
      )}
      {isThisStreamMessage && streamState === "streaming" && (
        <p className="mt-1 text-[10px] text-[#6b5f57]">
          streaming… first chunk {streaming.diagnostics.firstChunkMs}ms · {(streaming.diagnostics.totalBytes / 1024).toFixed(1)}KB
          {streaming.diagnostics.estimatedDurationSec != null ? ` · ~${streaming.diagnostics.estimatedDurationSec.toFixed(1)}s` : ""}
        </p>
      )}
      {retryError && (
        <p className="mt-1 text-[10px] text-red-400">Retry failed: {retryError}</p>
      )}
      {saveOpen && (
        <div className="mt-2 rounded-xl border border-white/[0.07] bg-[#181512] px-3 py-2.5 max-w-[90%]">
          <textarea
            value={saveText}
            onChange={(e) => setSaveText(e.target.value)}
            rows={3}
            placeholder="Text to save to long-term memory…"
            className="w-full bg-transparent text-xs text-[#c8c0b7] leading-relaxed outline-none resize-none placeholder-[#4b4540]"
          />
          {saveError && <p className="mt-1 text-[11px] text-red-400">{saveError}</p>}
          <div className="flex items-center gap-2 mt-2">
            <button
              onClick={confirmSave}
              disabled={saving || !saveText.trim()}
              className="px-2.5 py-1 rounded-lg text-[11px] font-medium bg-[#c4644a]/15 text-[#c4644a] hover:bg-[#c4644a]/22 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              onClick={cancelSave}
              disabled={saving}
              className="px-2.5 py-1 rounded-lg text-[11px] text-[#6b5f57] hover:text-[#c8c0b7] disabled:opacity-40 transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}
      {translateOpen && (
        <div className="mt-2 rounded-xl border border-white/[0.07] bg-[#181512] px-3 py-2.5 max-w-[90%]">
          {translateError ? (
            <p className="text-[11px] text-red-400">{translateError}</p>
          ) : translation ? (
            <>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[9px] uppercase tracking-wider text-[#4b4540]">{translation.source} → {translation.target} · {translation.provider}</span>
                {translation.fallbackUsed && <Badge label="fallback" variant="warn" />}
                <button onClick={() => setTranslateOpen(false)} className="ml-auto text-[#4b4540] hover:text-[#8a7f75] transition-colors"><X size={11} /></button>
              </div>
              <p className="text-xs text-[#c8c0b7] leading-relaxed whitespace-pre-wrap">{translation.text}</p>
            </>
          ) : null}
        </div>
      )}
    </>
  );
}

// ─── Message ───────────────────────────────────────────────────────────────────

interface MessageSegment {
  type: "text" | "code";
  content: string;
  lang?: string;
}

/**
 * Defensive repair for malformed fences where the newline after the
 * language tag was lost upstream (e.g. "```bash #!/bin/bash ...", all on one
 * line) — inserts the missing newline so the fence parses as code instead of
 * flattening into plain text. The real fix is that this shouldn't happen
 * upstream; this is a safety net, not the primary fix.
 */
function repairMalformedFences(text: string): string {
  return text.replace(/```(\w+)[ \t]+(?=\S)/g, "```$1\n");
}

/**
 * Splits assistant message content into alternating text/code segments so
 * fenced code blocks (```lang ... ```) render through the syntax-highlighted
 * CodeBlock component instead of being dumped as raw text. Plain text
 * segments still need `whitespace-pre-wrap` at render time — this function
 * only splits, it doesn't touch whitespace itself.
 */
function parseMessageSegments(content: string): MessageSegment[] {
  const repaired = repairMalformedFences(content);
  const segments: MessageSegment[] = [];
  const fenceRe = /```(\w*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = fenceRe.exec(repaired)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", content: repaired.slice(lastIndex, match.index) });
    }
    segments.push({ type: "code", lang: match[1] || "text", content: match[2].replace(/\n$/, "") });
    lastIndex = fenceRe.lastIndex;
  }
  if (lastIndex < repaired.length) {
    segments.push({ type: "text", content: repaired.slice(lastIndex) });
  }
  return segments.length > 0 ? segments : [{ type: "text", content: repaired }];
}

interface MessageBubbleProps {
  message: Message;
  index: number;
  speech: UseSpeechResult;
  streaming: UseStreamingSpeechResult;
  conversationId: string | null;
  isLatestAssistant: boolean;
  retrying: boolean;
  retryDisabled: boolean;
  retryError?: string;
  onRetry: (messageId: string) => void;
}

function MessageBubble({ message, index, speech, streaming, conversationId, isLatestAssistant, retrying, retryDisabled, retryError, onRetry }: MessageBubbleProps) {
  const { prefs } = useUiPreferences();
  const isUser = message.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: Math.min(index * 0.03, 0.15), ease: "easeOut" }}
      className={`flex gap-3 mb-5 group ${isUser ? "flex-row-reverse" : ""}`}
    >
      <div className={`shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-[11px] font-bold mt-0.5 ${
        isUser ? "bg-[#c4644a]/20 text-[#c4644a] border border-[#c4644a]/25" : "bg-[#2a2520] text-[#6b5f57] border border-white/[0.06]"
      }`}>{isUser ? "U" : "S"}</div>

      <div className={`flex-1 max-w-[84%] flex flex-col ${isUser ? "items-end" : "items-start"}`}>
        {/* Attachments above user bubble */}
        {isUser && message.attachments && message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2 justify-end">
            {message.attachments.map(a => <AttachChip key={a.id} a={a} compact />)}
          </div>
        )}

        {isUser ? (
          <div className="bg-[#c4644a]/12 border border-[#c4644a]/18 rounded-2xl rounded-tr-sm px-4 py-2.5">
            <p className="text-sm text-[#f0ebe3] leading-relaxed whitespace-pre-wrap">{message.content}</p>
          </div>
        ) : (
          <div className="w-full">
            {parseMessageSegments(message.content).map((seg, i) =>
              seg.type === "code" ? (
                <CodeBlock key={i} lang={seg.lang ?? "text"} code={seg.content} />
              ) : seg.content.trim() ? (
                <p key={i} className="text-sm text-[#c8c0b7] leading-relaxed whitespace-pre-wrap">{seg.content}</p>
              ) : null,
            )}
            <FeedbackRow
              content={message.content}
              messageId={message.id}
              speech={speech}
              streaming={streaming}
              conversationId={conversationId}
              isLatestAssistant={isLatestAssistant}
              retrying={retrying}
              retryDisabled={retryDisabled}
              retryError={retryError}
              onRetry={onRetry}
            />
          </div>
        )}
        {isUser && message.status === "processing" && (
          <span className="text-[10px] text-[#6b5f57] mt-1 px-0.5">Processing attachment/message...</span>
        )}
        {isUser && message.status === "failed" && (
          <span className="text-[10px] text-red-400 mt-1 px-0.5">
            Processing failed{message.error ? `: ${message.error}` : ""}
          </span>
        )}
        {prefs.showMessageTimestamps && <span className="text-[10px] text-[#3a342e] mt-1.5 px-0.5">{message.timestamp}</span>}
      </div>
    </motion.div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex gap-3 mb-5">
      <div className="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-[11px] font-bold mt-0.5 bg-[#2a2520] text-[#6b5f57] border border-white/[0.06]">S</div>
      <div className="flex items-center gap-1.5 px-4 py-2.5 bg-[#221e1b] border border-white/[0.06] rounded-2xl rounded-tl-sm">
        {[0, 1, 2].map(i => (
          <motion.div key={i} className="w-1.5 h-1.5 rounded-full bg-[#c4644a]"
            animate={{ opacity: [0.25, 1, 0.25], scale: [0.85, 1, 0.85] }}
            transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.18, ease: "easeInOut" }} />
        ))}
      </div>
    </div>
  );
}

// ─── Attachment menu ───────────────────────────────────────────────────────────

// Rendered via a portal into document.body and positioned with `fixed`
// coordinates from the paperclip button's own bounding rect, instead of
// `absolute` inside the composer box. The composer's outer container uses
// `overflow-hidden` (to clip its rounded corners), which silently clipped
// this dropdown to zero visible height when it was a plain in-tree
// `absolute bottom-full` child — clicking the paperclip looked like it did
// nothing because the menu items were rendered but invisible/unclickable.
function AttachmentMenu({ onSelect, anchorRect }: { onSelect: (type: AttachmentType, accept: string) => void; anchorRect: DOMRect }) {
  const items = [
    { type: "image" as AttachmentType, label: "Image", accept: "image/*", Icon: ImageIcon },
    { type: "text" as AttachmentType, label: "Text / Markdown / Log", accept: ".txt,.md,.markdown,.log", Icon: FileText },
    { type: "code" as AttachmentType, label: "Code / JSON", accept: ".py,.js,.jsx,.ts,.tsx,.rs,.go,.cpp,.c,.h,.java,.sh,.json,.yaml,.yml,.rb,.php,.cs,.sql", Icon: FileCode },
    { type: "text" as AttachmentType, label: "Project folder", accept: "", Icon: FolderOpen, dev: true },
  ];
  return createPortal(
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 6, scale: 0.97 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      style={{ position: "fixed", left: anchorRect.left, bottom: window.innerHeight - anchorRect.top + 8 }}
      className="attachment-menu-portal bg-[#1e1b18] border border-white/[0.1] rounded-xl shadow-2xl overflow-hidden z-50 w-48"
    >
      {items.map(item => (
        <button key={item.label} onClick={() => onSelect(item.type, item.accept)}
          className="w-full flex items-center gap-2.5 px-3.5 py-2.5 hover:bg-white/[0.05] text-left transition-colors">
          <item.Icon size={13} className={ATTACH_COLOR[item.type]} />
          <span className="text-xs text-[#c8c0b7] flex-1">{item.label}</span>
          {item.dev && <Badge label="dev" />}
        </button>
      ))}
    </motion.div>,
    document.body,
  );
}

// ─── Voice orb ────────────────────────────────────────────────────────────────

const ORB_R = 32;
const ORB_CX = 36;
const ORB_CY = 36;
const ORB_CIRC = 2 * Math.PI * ORB_R; // 201.06

function VoiceOrb({ state, amplitude }: { state: VoiceState; amplitude: number }) {
  const isActive = state !== "idle";
  const isError = state === "error-mic" || state === "error-tts";
  const isSiena = state === "speaking-siena" || state === "fallback";
  const isUser = state === "speaking-user";
  const isTranscribing = state === "transcribing";

  const arcLen = isActive
    ? ORB_CIRC * Math.max(0.07, Math.min(amplitude, 1) * 0.6)
    : ORB_CIRC * 0.12;
  const dashOffset = ORB_CIRC - arcLen;
  const arcColor = VOICE_ARC_COLOR[state];

  // Rotation duration: user speaking fastest, siena slowest
  const rotDur = isUser ? 2.5 : isTranscribing ? 3.5 : isSiena ? 5 : 6.5;

  return (
    <svg width="72" height="72" viewBox="0 0 72 72" className="shrink-0">
      <defs>
        <radialGradient id="orb-ambient" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={isSiena ? "rgba(196,100,74,0.18)" : isUser ? "rgba(240,235,227,0.07)" : "rgba(196,100,74,0.06)"} />
          <stop offset="100%" stopColor="transparent" />
        </radialGradient>
      </defs>

      {/* Ambient fill */}
      {isActive && !isError && (
        <circle cx={ORB_CX} cy={ORB_CY} r="36" fill="url(#orb-ambient)" />
      )}

      {/* Base ring */}
      <circle cx={ORB_CX} cy={ORB_CY} r={ORB_R}
        stroke="rgba(240,235,227,0.09)" strokeWidth="1" fill="none" />

      {/* Inner ring */}
      <circle cx={ORB_CX} cy={ORB_CY} r={ORB_R - 9}
        stroke="rgba(240,235,227,0.04)" strokeWidth="0.5" fill="none" />

      {/* Rotating arc */}
      {!isError && (
        <motion.g
          style={{ transformOrigin: `${ORB_CX}px ${ORB_CY}px` }}
          animate={isActive ? { rotate: [0, 360] } : { rotate: 0 }}
          transition={{ duration: rotDur, repeat: Infinity, ease: "linear" }}
        >
          <motion.circle
            cx={ORB_CX} cy={ORB_CY} r={ORB_R}
            stroke={arcColor}
            strokeWidth="1.5"
            fill="none"
            strokeLinecap="round"
            transform={`rotate(-90 ${ORB_CX} ${ORB_CY})`}
            style={{ strokeDasharray: ORB_CIRC }}
            animate={{ strokeDashoffset: dashOffset }}
            transition={{ duration: 0.11, ease: "easeOut" }}
          />
        </motion.g>
      )}

      {/* Error cross */}
      {isError && (
        <g>
          <line x1="28" y1="28" x2="44" y2="44" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="44" y1="28" x2="28" y2="44" stroke="#ef4444" strokeWidth="1.5" strokeLinecap="round" />
        </g>
      )}

      {/* Center dot */}
      {!isError && (
        <motion.circle
          cx={ORB_CX} cy={ORB_CY} r={2.5}
          fill={isSiena ? "#c4644a" : "rgba(240,235,227,0.65)"}
          style={{ transformOrigin: `${ORB_CX}px ${ORB_CY}px` }}
          animate={isActive ? { scale: [1, isSiena ? 1.5 : 1.3, 1] } : { scale: 1 }}
          transition={{ duration: isSiena ? 1.0 : 0.45, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
    </svg>
  );
}

function VoiceStateText({ state }: { state: VoiceState }) {
  const { primary, sub } = VOICE_LABELS[state];
  const isError = state === "error-mic" || state === "error-tts";
  const isFallback = state === "fallback";
  const isSiena = state === "speaking-siena" || isFallback;
  const dotColor = isError ? "bg-red-400" : isFallback ? "bg-amber-400" : isSiena ? "bg-[#c4644a]" : "bg-[rgba(240,235,227,0.4)]";
  const labelColor = isError ? "text-red-400" : isFallback ? "text-amber-400" : isSiena ? "text-[#c4644a]" : "text-[#f0ebe3]";

  return (
    <div className="flex-1 min-w-0">
      <AnimatePresence mode="wait">
        <motion.div key={state}
          initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -3 }} transition={{ duration: 0.14 }}>
          <div className={`text-sm font-semibold leading-tight ${labelColor}`}>{primary}</div>
          <div className="flex items-center gap-1.5 mt-1">
            {!isError && (
              <motion.div className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColor}`}
                animate={{ opacity: [1, 0.25, 1] }}
                transition={{ duration: 1.2, repeat: Infinity }} />
            )}
            <span className="text-[11px] text-[#6b5f57] leading-tight">{sub}</span>
          </div>
          {state === "transcribing" && (
            <div className="flex gap-1 mt-2">
              {[0, 1, 2].map(i => (
                <motion.div key={i} className="w-1 h-1 rounded-full bg-[#c4644a]"
                  animate={{ opacity: [0.25, 1, 0.25] }}
                  transition={{ duration: 0.85, repeat: Infinity, delay: i * 0.22 }} />
              ))}
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

// ─── Composer ─────────────────────────────────────────────────────────────────

const MAX_COMPOSER_H = 200;

function Composer({ onSend, thinking, speech, streaming, conversationId }: {
  onSend: (text: string, attachments: Attachment[]) => Promise<SendResult>; thinking: boolean;
  speech: UseSpeechResult; streaming: UseStreamingSpeechResult; conversationId: string | null;
}) {
  const { t } = useUiPreferences();
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuAnchorRect, setMenuAnchorRect] = useState<DOMRect | null>(null);
  const [amplitude, setAmplitude] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileAcceptRef = useRef<string>("*");
  const fileTypeRef = useRef<AttachmentType>("text");
  const menuRef = useRef<HTMLDivElement>(null);
  const attachBtnRef = useRef<HTMLButtonElement>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const attachErrorTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const { status: composerRuntimeStatus } = useRuntimeStatus();

  // Mic / STT (Phase 2, HANDOFF_v2.md) — real getUserMedia recording through
  // the Phase 1 whisper.cpp backend endpoint. Mic button stays disabled
  // unless the backend honestly reports stt_available=true; if the status
  // request itself fails, voiceStatusData is null and the button stays
  // disabled with that error as the reason.
  const { status: voiceStatusData, error: voiceStatusError } = useVoiceStatus(30000);
  const sttAvailable = voiceStatusData?.stt_available === true;

  const handleTranscribed = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setValue(prev => {
      if (!prev.trim()) return trimmed;
      const needsSpace = prev.length > 0 && !/\s$/.test(prev);
      return prev + (needsSpace ? " " : "") + trimmed;
    });
  }, []);

  // Recording while Siena's own voice is playing would feed synthesized
  // speech straight back into the transcription through the same speakers —
  // always stop both TTS paths before a new recording actually starts.
  const handleBeforeRecordingStart = useCallback(() => {
    speech.stop();
    streaming.stop();
  }, [speech, streaming]);

  const recorder = useVoiceRecorder({
    onTranscribed: handleTranscribed,
    onBeforeStart: handleBeforeRecordingStart,
  });

  // Voice Conversation Mode (experimental, Phase 3, HANDOFF_v2.md) —
  // hands-free listen -> transcribe -> auto-send -> speak -> listen loop.
  // Reuses the exact same onSend/handleSend path the manual Send
  // button/Enter key use, so a voice-driven turn is a completely normal
  // user+assistant message pair, never a fake/bypassed one.
  const conversation = useVoiceConversation({
    speech,
    streaming,
    sendMessage: useCallback((text: string) => onSend(text, []), [onSend]),
  });

  // Switching conversations must not leave a live mic stream/AudioContext
  // running in the background pointed at whatever composer used to be
  // active — cancel()/stop() are no-ops if nothing was in progress.
  const { cancel: cancelRecording } = recorder;
  const { stop: stopConversation } = conversation;
  useEffect(() => {
    cancelRecording();
    stopConversation();
  }, [conversationId, cancelRecording, stopConversation]);

  // Mutual exclusion: only one mic stream at a time. Starting either mode
  // silently tears down the other first (cancel(), not stop(), for the one
  // not explicitly requested — no "stopped" trace noise for something the
  // user didn't click).
  const startPushToTalk = useCallback(() => {
    if (conversation.active) conversation.cancel();
    void recorder.start();
  }, [conversation, recorder]);
  const startConversationMode = useCallback(() => {
    if (recorder.status !== "idle") recorder.cancel();
    void conversation.start();
  }, [conversation, recorder]);

  const voiceState: VoiceState =
    conversation.state === "listening" ? "listening"
    : conversation.state === "speech_detected" ? "speech-detected"
    : conversation.state === "silence_wait" ? "silence-wait"
    : conversation.state === "finalizing_wait" ? "finalizing-wait"
    : conversation.state === "transcribing" ? "transcribing"
    : conversation.state === "thinking" ? "thinking"
    : conversation.state === "speaking" ? "speaking-siena"
    : conversation.state === "error" ? "error-mic"
    : recorder.status === "requesting_permission" ? "requesting-permission"
    : recorder.status === "recording" ? "listening"
    : recorder.status === "transcribing" ? "transcribing"
    : recorder.status === "error" ? "error-mic"
    : "idle";

  const voiceActive = voiceState !== "idle";
  const isError = voiceState === "error-mic" || voiceState === "error-tts";
  const isSiena = voiceState === "speaking-siena" || voiceState === "fallback";

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const h = Math.min(el.scrollHeight, MAX_COMPOSER_H);
    el.style.height = h + "px";
    el.style.overflowY = el.scrollHeight > MAX_COMPOSER_H ? "auto" : "hidden";
  }, [value]);

  // Close menu on outside click. The dropdown itself now renders through a
  // portal into document.body (see AttachmentMenu), so it's no longer a DOM
  // descendant of menuRef — it's matched separately via the
  // `.attachment-menu-portal` class instead of `.contains()`.
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (menuRef.current && menuRef.current.contains(target)) return;
      if (target.closest?.(".attachment-menu-portal")) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  // VoiceOrb is driven by real mic RMS amplitude while there's an actual
  // signal to show (push-to-talk recording, or Conversation Mode's
  // listening/speech-detected/silence-wait/finalizing-wait — the mic keeps
  // capturing through all of those, see useVoiceConversation.ts) — see
  // displayAmplitude below. This effect only covers states with no audio
  // signal at all (permission prompt / transcribing / thinking / TTS
  // speaking-back), where it's just a gentle breathing animation so the orb
  // doesn't look frozen.
  useEffect(() => {
    const active = ["requesting-permission", "transcribing", "thinking", "speaking-siena", "fallback"];
    if (!active.includes(voiceState)) { setAmplitude(0); return; }
    let t = 0;
    const id = setInterval(() => {
      t += 0.09;
      if (voiceState === "speaking-siena" || voiceState === "fallback") {
        setAmplitude(0.48 + 0.32 * Math.sin(t * 1.4));
      } else {
        setAmplitude(0.28 + 0.22 * Math.sin(t * 0.65));
      }
    }, 45);
    return () => clearInterval(id);
  }, [voiceState]);

  // Real mic input level while there's an actual mic signal to show; the
  // simulated breathing amplitude above for every other active state.
  const conversationListeningStates: VoiceState[] = ["listening", "speech-detected", "silence-wait", "finalizing-wait"];
  const displayAmplitude =
    conversation.active && conversationListeningStates.includes(voiceState)
      ? conversation.amplitude
      : voiceState === "listening" && recorder.status === "recording"
        ? recorder.amplitude
        : amplitude;

  // Panel Stop/Dismiss button. Conversation Mode takes priority when active
  // (its Stop always means "end the whole hands-free loop", including
  // whatever TTS might be playing right now). Otherwise the push-to-talk
  // logic: while actually recording this stops and sends the WAV for
  // transcription; while requesting permission/transcribing it cancels
  // (aborting the in-flight fetch if there is one); on error it just
  // dismisses and resets to idle. cancel()/stopAndTranscribe() are both
  // idempotent no-ops outside their relevant state.
  const handleVoicePanelStop = useCallback(() => {
    if (conversation.active) { conversation.stop(); return; }
    if (recorder.status === "recording") recorder.stopAndTranscribe();
    else recorder.cancel();
  }, [conversation, recorder]);

  const toggleVoice = () => {
    if (conversation.active) return; // mic button is disabled while Conversation Mode owns the mic
    if (recorder.status === "idle" || recorder.status === "error") startPushToTalk();
    else if (recorder.status === "recording") recorder.stopAndTranscribe();
    // requesting_permission / transcribing: ignore extra clicks on the mic icon itself
  };

  const toggleConversationMode = () => {
    if (conversation.active) conversation.stop();
    else startConversationMode();
  };

  const showAttachError = useCallback((message: string) => {
    setAttachError(message);
    clearTimeout(attachErrorTimerRef.current);
    attachErrorTimerRef.current = setTimeout(() => setAttachError(null), 4000);
  }, []);

  const logAttachmentEvent = useCallback((event: string, fields: Record<string, unknown>) => {
    sienaClient.logClientEvent(event, fields);
  }, []);

  const removeAttachment = (id: string) => {
    setAttachments(a => {
      const removed = a.find(x => x.id === id);
      if (removed) logAttachmentEvent("attachment_remove", { name: removed.name, type: removed.type });
      return a.filter(x => x.id !== id);
    });
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items);
    const imgItem = items.find(item => item.type.startsWith("image/"));
    if (imgItem) {
      e.preventDefault();
      if (attachments.length >= MAX_ATTACHMENTS_PER_MESSAGE) {
        showAttachError(`Max ${MAX_ATTACHMENTS_PER_MESSAGE} attachments per message.`);
        return;
      }
      const file = imgItem.getAsFile();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = ev => {
        const a: Attachment = { id: crypto.randomUUID(), type: "image", name: "pasted-image.png", size: fmtSize(file.size), mime: file.type, dataUrl: ev.target?.result as string };
        setAttachments(list => [...list, a]);
        logAttachmentEvent("attachment_add", { name: a.name, type: a.type });
      };
      reader.readAsDataURL(file);
      return;
    }
    const text = e.clipboardData.getData("text");
    if (text && text.length > 400) {
      e.preventDefault();
      if (attachments.length >= MAX_ATTACHMENTS_PER_MESSAGE) {
        showAttachError(`Max ${MAX_ATTACHMENTS_PER_MESSAGE} attachments per message.`);
        return;
      }
      if (text.length > MAX_ATTACHMENT_TEXT_CHARS) {
        showAttachError(`Pasted text too large (${text.length.toLocaleString()} chars, max ${MAX_ATTACHMENT_TEXT_CHARS.toLocaleString()}).`);
        logAttachmentEvent("attachment_too_large", { name: "pasted-text", chars: text.length });
        return;
      }
      if (totalAttachmentTextChars(attachments) + text.length > MAX_TOTAL_ATTACHMENT_TEXT_CHARS) {
        showAttachError("Total attachment text limit reached for this message.");
        logAttachmentEvent("attachment_too_large", { name: "pasted-text", chars: text.length, total: true });
        return;
      }
      const code = isLikelyCode(text);
      const lang = code ? detectLang(text) : undefined;
      const ext = lang === "python" ? "py" : lang === "typescript" ? "ts" : lang === "go" ? "go" : "txt";
      const a: Attachment = {
        id: crypto.randomUUID(), type: code ? "code" : "text",
        name: code ? `pasted-snippet.${ext}` : "pasted-text.txt",
        size: fmtSize(text.length), lang, content: text,
        preview: text.slice(0, 300) + (text.length > 300 ? "\n…" : ""),
      };
      setAttachments(list => [...list, a]);
      logAttachmentEvent("attachment_add", { name: a.name, type: a.type });
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    const type = classifyAttachment(file.name, file.type);
    if (!type) {
      showAttachError(`Unsupported file type: ${file.name}`);
      logAttachmentEvent("attachment_unsupported", { name: file.name, mime: file.type });
      return;
    }
    if (attachments.length >= MAX_ATTACHMENTS_PER_MESSAGE) {
      showAttachError(`Max ${MAX_ATTACHMENTS_PER_MESSAGE} attachments per message.`);
      return;
    }

    if (type === "image") {
      const reader = new FileReader();
      reader.onload = ev => {
        const a: Attachment = { id: crypto.randomUUID(), type: "image", name: file.name, size: fmtSize(file.size), mime: file.type, dataUrl: ev.target?.result as string };
        setAttachments(list => [...list, a]);
        logAttachmentEvent("attachment_add", { name: a.name, type: a.type });
      };
      reader.readAsDataURL(file);
      return;
    }

    const reader = new FileReader();
    reader.onload = ev => {
      const content = (ev.target?.result as string) ?? "";
      if (content.length > MAX_ATTACHMENT_TEXT_CHARS) {
        showAttachError(`${file.name} is too large (${content.length.toLocaleString()} chars, max ${MAX_ATTACHMENT_TEXT_CHARS.toLocaleString()}).`);
        logAttachmentEvent("attachment_too_large", { name: file.name, chars: content.length });
        return;
      }
      if (totalAttachmentTextChars(attachments) + content.length > MAX_TOTAL_ATTACHMENT_TEXT_CHARS) {
        showAttachError("Total attachment text limit reached for this message.");
        logAttachmentEvent("attachment_too_large", { name: file.name, chars: content.length, total: true });
        return;
      }
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      const a: Attachment = {
        id: crypto.randomUUID(), type, name: file.name, size: fmtSize(file.size), mime: file.type,
        lang: type === "code" ? (CODE_LANG_BY_EXT[ext] ?? "text") : undefined,
        content,
        preview: content.slice(0, 300) + (content.length > 300 ? "\n…" : ""),
      };
      setAttachments(list => [...list, a]);
      logAttachmentEvent("attachment_add", { name: a.name, type: a.type });
    };
    reader.readAsText(file);
  };

  const openFilePicker = (type: AttachmentType, accept: string) => {
    fileTypeRef.current = type;
    fileAcceptRef.current = accept;
    if (fileInputRef.current) { fileInputRef.current.accept = accept || "*"; fileInputRef.current.click(); }
    setMenuOpen(false);
  };

  const send = () => {
    // Conversation Mode owns the send pipeline while it's active (it needs
    // to know exactly which turn is "its" reply to speak back) — block the
    // manual path entirely rather than risk two concurrent /api/chat calls
    // racing each other. Stop Conversation Mode first to type/send by hand.
    if (conversation.active) return;
    if ((!value.trim() && attachments.length === 0) || thinking) return;
    onSend(value.trim(), attachments);
    setValue("");
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const hasContent = value.trim().length > 0 || attachments.length > 0;

  return (
    <div className="px-5 pb-5 pt-2 shrink-0">
      <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileSelect} />

      <motion.div layout transition={{ duration: 0.22, ease: "easeInOut" }}
        className="bg-[#1e1b18] border border-white/[0.08] rounded-2xl overflow-hidden focus-within:border-[#c4644a]/28 focus-within:ring-1 focus-within:ring-[#c4644a]/08 transition-all">

        {/* Attachment error banner (unsupported type / size limit) */}
        <AnimatePresence initial={false}>
          {attachError && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.15 }} className="overflow-hidden">
              <div className="px-3 py-1.5 text-[11px] text-red-400 bg-red-400/[0.05] border-b border-red-400/10">
                {attachError}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Attachment previews */}
        <AnimatePresence initial={false}>
          {attachments.length > 0 && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18, ease: "easeInOut" }} className="overflow-hidden">
              <div className="px-3 pt-3 pb-2 border-b border-white/[0.05] flex flex-wrap gap-2">
                {attachments.map(a => <AttachChip key={a.id} a={a} onRemove={() => removeAttachment(a.id)} />)}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Voice panel ── appears above input row when voice is active */}
        <AnimatePresence initial={false}>
          {voiceActive && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: "easeInOut" }}
              className="overflow-hidden border-b border-white/[0.06]"
            >
              <div className="flex items-center gap-4 px-4 py-3.5">
                <VoiceOrb state={voiceState} amplitude={displayAmplitude} />
                <div className="flex-1 min-w-0">
                  {conversation.active && (
                    <span className="inline-block mb-1 px-1.5 py-0.5 rounded text-[9px] font-semibold tracking-wide uppercase bg-[#c4644a]/15 text-[#c4644a]">
                      Conversation · experimental
                    </span>
                  )}
                  <VoiceStateText state={voiceState} />
                </div>

                {!conversation.active && voiceState === "listening" && (
                  <span className="shrink-0 text-[11px] tabular-nums text-[#6b5f57]">
                    {String(Math.floor(recorder.elapsedSec / 60)).padStart(1, "0")}:{String(recorder.elapsedSec % 60).padStart(2, "0")}
                  </span>
                )}
                {conversation.active &&
                  (voiceState === "speech-detected" || voiceState === "silence-wait" || voiceState === "finalizing-wait") && (
                  <span
                    className="shrink-0 text-[9px] leading-tight tabular-nums text-[#6b5f57] text-right"
                    title="Temporary VAD tuning diagnostics: amplitude / threshold (noise floor) — speech ms / silence ms — auto-send countdown"
                  >
                    <div>
                      amp {conversation.diagnostics.amplitude.toFixed(2)} / thr {conversation.diagnostics.threshold.toFixed(2)}{" "}
                      (nf {conversation.diagnostics.noiseFloor.toFixed(3)})
                    </div>
                    <div>
                      speech {(conversation.diagnostics.speechMs / 1000).toFixed(1)}s · silence{" "}
                      {(conversation.diagnostics.silenceMs / 1000).toFixed(1)}s
                      {conversation.diagnostics.autoSendInMs != null &&
                        ` · auto-send in ${(conversation.diagnostics.autoSendInMs / 1000).toFixed(1)}s`}
                    </div>
                  </span>
                )}

                {/* Manual "Finish" override (optional per HANDOFF_v2.md) —
                    lets the user commit the current utterance immediately
                    instead of waiting out the silence/finalize timers.
                    Never replaces Stop. */}
                {conversation.active &&
                  (voiceState === "speech-detected" || voiceState === "silence-wait" || voiceState === "finalizing-wait") && (
                  <button
                    onClick={() => conversation.finishNow()}
                    className="shrink-0 px-2.5 py-1.5 rounded-xl text-xs font-medium border border-white/[0.08] text-[#6b5f57] hover:text-[#f0ebe3] hover:border-white/15 transition-colors"
                  >
                    Finish
                  </button>
                )}

                {/* Stop / dismiss button */}
                <button
                  onClick={handleVoicePanelStop}
                  className={`shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium border transition-colors ${
                    isError
                      ? "border-red-400/20 text-red-400 hover:bg-red-400/10"
                      : "border-white/[0.08] text-[#6b5f57] hover:text-[#f0ebe3] hover:border-white/15"
                  }`}
                >
                  <div className={`w-2 h-2 rounded-sm ${isError ? "bg-red-400" : "bg-[#6b5f57]"}`} />
                  {isError ? "Dismiss" : "Stop"}
                </button>
              </div>
              {isError && (recorder.error || conversation.error) && (
                <p className="px-4 pb-2.5 -mt-2 text-[10px] text-red-400">{conversation.active ? conversation.error : recorder.error}</p>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Input row */}
        <div className="flex items-end gap-2 px-3 py-2.5">
          {/* Attachment button */}
          <div ref={menuRef} className="relative shrink-0 self-end pb-0.5">
            <button
              ref={attachBtnRef}
              onClick={() => {
                if (!menuOpen && attachBtnRef.current) setMenuAnchorRect(attachBtnRef.current.getBoundingClientRect());
                setMenuOpen(o => !o);
              }}
              className={`w-7 h-7 rounded-lg flex items-center justify-center transition-colors ${menuOpen ? "bg-[#c4644a]/15 text-[#c4644a]" : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.05]"}`}>
              <Paperclip size={14} />
            </button>
            <AnimatePresence>
              {menuOpen && menuAnchorRect && <AttachmentMenu onSelect={openFilePicker} anchorRect={menuAnchorRect} />}
            </AnimatePresence>
          </div>

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            onPaste={handlePaste}
            rows={1}
            placeholder={thinking ? t("chat.placeholder.thinking") : voiceActive ? t("chat.placeholder.voiceActive") : t("chat.placeholder.default")}
            disabled={thinking}
            className="flex-1 bg-transparent text-sm text-[#f0ebe3] placeholder-[#3a342e] outline-none resize-none leading-relaxed disabled:opacity-40 [scrollbar-width:thin]"
            style={{ maxHeight: MAX_COMPOSER_H }}
          />

          {/* Right actions */}
          <div className="flex items-center gap-1 shrink-0 self-end pb-0.5">
            <motion.button
              onClick={toggleConversationMode}
              disabled={!sttAvailable || recorder.status !== "idle" || (conversation.active && conversation.state === "error")}
              title={
                !sttAvailable
                  ? (voiceStatusData?.stt_reason ?? voiceStatusError ?? "STT unavailable")
                  : conversation.active
                    ? "Stop Conversation Mode"
                    : "Start Conversation Mode — hands-free, experimental"
              }
              whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.92 }}
              className={`relative w-7 h-7 rounded-lg flex items-center justify-center transition-colors ${
                !sttAvailable || recorder.status !== "idle"
                  ? "text-[#3a342e] opacity-50 cursor-not-allowed"
                  : conversation.active
                    ? "bg-[#c4644a]/15 text-[#c4644a]"
                    : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.05]"
              }`}
            >
              {conversation.active && (
                <motion.span
                  className="absolute w-7 h-7 rounded-lg border border-white/30"
                  animate={{ scale: [1, 1.6], opacity: [0.5, 0] }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeOut" }}
                />
              )}
              <Headphones size={14} />
            </motion.button>
            <motion.button
              onClick={toggleVoice}
              disabled={!sttAvailable || conversation.active || recorder.status === "requesting_permission" || recorder.status === "transcribing"}
              title={
                !sttAvailable
                  ? (voiceStatusData?.stt_reason ?? voiceStatusError ?? "STT unavailable")
                  : conversation.active
                    ? "Stop Conversation Mode to use push-to-talk"
                    : (voiceState === "listening" ? "Stop recording" : "Start voice input (whisper.cpp)")
              }
              whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.92 }}
              className={`relative w-7 h-7 rounded-lg flex items-center justify-center transition-colors ${
                !sttAvailable || conversation.active
                  ? "text-[#3a342e] opacity-50 cursor-not-allowed"
                  : voiceState === "listening"
                    ? "bg-[#c4644a]/15 text-[#c4644a]"
                    : "text-[#4b4540] hover:text-[#8a7f75] hover:bg-white/[0.05]"
              }`}
            >
              {/* Pulse ring while recording */}
              {!conversation.active && voiceState === "listening" && (
                <motion.span
                  className="absolute w-7 h-7 rounded-lg border border-white/30"
                  animate={{ scale: [1, 1.6], opacity: [0.5, 0] }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeOut" }}
                />
              )}
              <Mic size={14} />
            </motion.button>
            <motion.button
              onClick={send}
              disabled={!hasContent || thinking || conversation.active}
              title={conversation.active ? "Stop Conversation Mode to send manually" : undefined}
              whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }}
              className="w-7 h-7 rounded-xl bg-[#c4644a] flex items-center justify-center text-white disabled:opacity-25 transition-opacity shadow-sm"
            >
              <Send size={12} />
            </motion.button>
          </div>
        </div>
      </motion.div>

      <div className="flex justify-between mt-1.5 px-1">
        <span className="text-[9px] text-[#2e2a26]">⏎ send · ⇧⏎ newline · ⌘V paste image</span>
        <span className="text-[9px] text-[#2e2a26]">
          {conversation.active
            ? "Conversation mode · experimental"
            : voiceActive
              ? (isSiena ? "TTS streaming" : "STT active · Whisper")
              : `${composerRuntimeStatus?.active_chat_model ?? "Backend not connected"} · local`}
        </span>
      </div>
    </div>
  );
}

// ─── Inspector panel ───────────────────────────────────────────────────────────

interface ToolTraceRow {
  id: string;
  tool: string;
  status: "ok" | "error";
  ts: string;
  ms: number;
  args: string;
  result: string;
}

function formatTraceTs(ts: unknown): string {
  if (typeof ts !== "string" || !ts) return "--:--:--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString([], { hour12: false });
}

function elapsedTraceMs(startTs: unknown, endTs: unknown): number {
  if (typeof startTs !== "string" || typeof endTs !== "string") return 0;
  const a = new Date(startTs).getTime();
  const b = new Date(endTs).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.max(0, Math.round(b - a));
}

function safeTraceJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}

function isSearchResultShaped(value: unknown): value is Array<Record<string, unknown>> {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => item && typeof item === "object" && ("title" in item || "url" in item))
  );
}

/**
 * Renders web_search-shaped tool_result content (list of {title,url,snippet})
 * as a readable per-source summary instead of a flat JSON blob or (the bug
 * this replaces) an array coerced to a plain string, which shows up as
 * "[object Object],[object Object],...". Falls back to safeTraceJson for any
 * other tool's result shape.
 */
function formatToolResultContent(content: unknown): string {
  if (!isSearchResultShaped(content)) return safeTraceJson(content);
  return content
    .map((item, i) => {
      const title = (item.title as string | undefined) || "(no title)";
      const url = (item.url as string | undefined) || "";
      let domain = "unknown source";
      if (url) {
        try {
          domain = new URL(url).hostname;
        } catch {
          domain = url;
        }
      }
      const date = (item.date ?? item.published ?? item.published_date) as string | undefined;
      const snippet = (item.snippet as string | undefined) || "";
      const lines = [`${i + 1}. ${title} — ${domain}`];
      if (date) lines.push(`   Date: ${date}`);
      if (snippet) lines.push(`   ${snippet}`);
      if (url) lines.push(`   ${url}`);
      return lines.join("\n");
    })
    .join("\n");
}

/**
 * Turns the raw JSONL/WS trace stream (see api/server.py BroadcastLogger)
 * into "tool call" rows shared by the Inspector panel, Tool Trace view, and
 * Debug view's Tool Calls tab. Pairs *_started -> *_completed|failed events
 * (tool_dispatch/tool_result, ocr_*, translator_*, model_specialist_*) and
 * renders self-contained events (model_route_decision, active_model_changed/
 * failed, attachment/context-injection events) as single rows.
 */
function pairToolTraceEvents(events: TraceEvent[]): ToolTraceRow[] {
  const rows: ToolTraceRow[] = [];
  const pendingByTool: Record<string, TraceEvent[]> = {};
  const pendingOcr: TraceEvent[] = [];
  const pendingTranslator: TraceEvent[] = [];
  const pendingSpecialist: TraceEvent[] = [];
  const pendingVision: TraceEvent[] = [];
  const pendingSpeak: TraceEvent[] = [];
  const pendingStt: TraceEvent[] = [];
  const pendingNucleraresStatus: TraceEvent[] = [];
  const pendingNucleraresContext: TraceEvent[] = [];
  let seq = 0;
  const nextId = () => `trace-${seq++}`;

  for (const e of events) {
    switch (e.event) {
      case "tool_dispatch": {
        const name = String(e.name ?? "tool");
        (pendingByTool[name] ??= []).push(e);
        break;
      }
      case "tool_result": {
        const name = String(e.name ?? "tool");
        const queue = pendingByTool[name];
        const start = queue && queue.length > 0 ? queue.shift() : undefined;
        const isError = e.ok === false;
        rows.push({
          id: nextId(),
          tool: name,
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson(start?.args),
          result: isError ? String(e.error ?? "error") : formatToolResultContent(e.content),
        });
        break;
      }
      case "ocr_started":
        pendingOcr.push(e);
        break;
      case "ocr_completed":
      case "ocr_low_quality":
      case "ocr_failed": {
        const start = pendingOcr.shift();
        const isError = e.event === "ocr_failed";
        rows.push({
          id: nextId(),
          tool: `ocr:${String(e.name ?? "image")}`,
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({ name: e.name, mime: start?.mime }),
          result: isError ? String(e.error ?? "OCR failed") : `quality=${String(e.quality ?? "ok")}, chars=${String(e.chars ?? 0)}`,
        });
        break;
      }
      case "translator_started":
        pendingTranslator.push(e);
        break;
      case "translator_fallback":
        // Informational only — folded into the eventual completed/failed row, not its own row.
        break;
      case "translator_completed":
      case "translator_failed": {
        const start = pendingTranslator.shift();
        const isError = e.event === "translator_failed";
        rows.push({
          id: nextId(),
          tool: "translator",
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({ source_lang: start?.source_lang, target_lang: start?.target_lang }),
          result: isError ? String(e.error ?? "Translation failed") : `provider=${String(e.provider ?? "?")}, chars=${String(e.chars ?? 0)}`,
        });
        break;
      }
      case "model_specialist_started":
        pendingSpecialist.push(e);
        break;
      case "model_specialist_completed":
      case "model_specialist_failed": {
        const start = pendingSpecialist.shift();
        const isError = e.event === "model_specialist_failed";
        rows.push({
          id: nextId(),
          tool: `specialist:${String(e.role ?? e.model ?? "model")}`,
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({ model: e.model }),
          result: isError ? String(e.error ?? "Specialist call failed") : "completed",
        });
        break;
      }
      case "model_route_decision":
        rows.push({
          id: nextId(),
          tool: "router:decision",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ role: e.role, mode: e.mode }),
          result: `model=${String(e.model ?? "?")} reason=${String(e.reason ?? "?")}`,
        });
        break;
      case "active_model_changed":
        rows.push({
          id: nextId(),
          tool: "models:active_switch",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ previous_model: e.previous_model }),
          result: `active_chat_model -> ${String(e.new_model ?? "?")}`,
        });
        break;
      case "active_model_change_failed":
        rows.push({
          id: nextId(),
          tool: "models:active_switch",
          status: "error",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ attempted_model: e.attempted_model }),
          result: String(e.reason ?? "rejected"),
        });
        break;
      case "image_understanding_unavailable":
        rows.push({
          id: nextId(),
          tool: "ocr:image_understanding",
          status: "error",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ requested_text: e.requested_text }),
          result: "Image understanding not connected yet — OCR text only",
        });
        break;
      case "attachment_send":
        rows.push({
          id: nextId(),
          tool: "attachment:send",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ count: e.count, types: e.types }),
          result: `sent ${String(e.count ?? 0)} attachment(s)`,
        });
        break;
      case "attachment_context_injected":
        rows.push({
          id: nextId(),
          tool: "attachment:context",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ count: e.count }),
          result: `${String(e.chars ?? 0)} chars injected`,
        });
        break;
      case "ocr_context_injected":
        rows.push({
          id: nextId(),
          tool: "ocr:context",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ count: e.count }),
          result: `${String(e.chars ?? 0)} chars injected`,
        });
        break;
      case "translator_context_injected":
        rows.push({
          id: nextId(),
          tool: "translator:context",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({ name: e.name, target_lang: e.target_lang }),
          result: `${String(e.chars ?? 0)} chars injected`,
        });
        break;
      // Debug page (0.2.0 release readiness pass) — Vision/Speak/STT/
      // Nucleares/Insights weren't shown in Tool Calls at all before; these
      // mirror the OCR/translator start->end pairing pattern above exactly.
      case "vision_started":
        pendingVision.push(e);
        break;
      case "vision_completed":
      case "vision_failed": {
        const start = pendingVision.shift();
        const isError = e.event === "vision_failed";
        rows.push({
          id: nextId(),
          tool: `vision:${String(e.name ?? "image")}`,
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({ name: e.name }),
          result: isError ? String(e.error ?? "Vision failed") : `chars=${String(e.chars ?? 0)}`,
        });
        break;
      }
      case "voice_synthesize_start":
        pendingSpeak.push(e);
        break;
      case "voice_synthesize_result": {
        const start = pendingSpeak.shift();
        rows.push({
          id: nextId(),
          tool: "voice:speak",
          status: "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({ provider: start?.provider }),
          result: `duration=${String(e.duration_ms ?? "?")}ms, audio=${String(e.audio_duration_sec ?? "?")}s`,
        });
        break;
      }
      case "stt_transcribe_started":
        pendingStt.push(e);
        break;
      case "stt_transcribe_completed":
      case "stt_transcribe_failed": {
        const start = pendingStt.shift();
        const isError = e.event === "stt_transcribe_failed";
        rows.push({
          id: nextId(),
          tool: "voice:stt",
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({}),
          result: isError ? String(e.error ?? "STT failed") : `backend=${String(e.backend ?? "?")}`,
        });
        break;
      }
      case "nucleares_status_requested":
        pendingNucleraresStatus.push(e);
        break;
      case "nucleares_status_completed":
      case "nucleares_status_failed": {
        const start = pendingNucleraresStatus.shift();
        const isError = e.event === "nucleares_status_failed";
        rows.push({
          id: nextId(),
          tool: "nucleares:status",
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({}),
          result: isError ? String(e.error ?? "unreachable") : "connected",
        });
        break;
      }
      case "nucleares_context_injection_requested":
        pendingNucleraresContext.push(e);
        break;
      case "nucleares_context_injected":
      case "nucleares_context_unavailable": {
        const start = pendingNucleraresContext.shift();
        const isError = e.event === "nucleares_context_unavailable";
        rows.push({
          id: nextId(),
          tool: "nucleares:context",
          status: isError ? "error" : "ok",
          ts: formatTraceTs(start?.ts ?? e.ts),
          ms: elapsedTraceMs(start?.ts, e.ts),
          args: safeTraceJson({}),
          result: isError ? "context unavailable" : "context injected",
        });
        break;
      }
      case "nucleares_context_skipped":
        rows.push({
          id: nextId(),
          tool: "nucleares:context",
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({}),
          result: `skipped: ${String(e.reason ?? "?")}`,
        });
        break;
      case "candidate_memory_created":
        rows.push({
          id: nextId(), tool: "insights:candidate_created", status: "ok",
          ts: formatTraceTs(e.ts), ms: 0,
          args: safeTraceJson({ category: e.category }),
          result: `#${String(e.id ?? "?")} proposed`,
        });
        break;
      case "candidate_memory_promoted":
      case "candidate_memory_rejected":
      case "candidate_memory_deferred":
      case "candidate_memory_deleted":
        rows.push({
          id: nextId(),
          tool: `insights:${e.event.replace("candidate_memory_", "")}`,
          status: "ok",
          ts: formatTraceTs(e.ts),
          ms: 0,
          args: safeTraceJson({}),
          result: `#${String(e.candidate_id ?? "?")}`,
        });
        break;
      default:
        break;
    }
  }

  return rows;
}

interface LastRequestSummary {
  userMessageTs?: string;
  conversationId?: string;
  routeModel?: string;
  routeRole?: string;
  routeReason?: string;
  toolCalls: { name: string; ok: boolean }[];
  doneReason?: string;
  finalAnswerPreview?: string;
  durationMs?: number;
}

// Debug page (0.2.0 release readiness pass) — reconstructs a summary of the
// most recent chat turn purely from existing trace events (no new backend
// capture). Walks backward to the last "user_message", then forward to that
// turn's "final_answer". Any field whose event never showed up is simply
// left undefined — this never invents data.
function buildLastRequestSummary(events: TraceEvent[]): LastRequestSummary | null {
  let userIdx = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event === "user_message") { userIdx = i; break; }
  }
  if (userIdx === -1) return null;

  const userEvent = events[userIdx];
  const summary: LastRequestSummary = {
    userMessageTs: typeof userEvent.ts === "string" ? userEvent.ts : undefined,
    conversationId: typeof userEvent.conversation_id === "string" ? userEvent.conversation_id : undefined,
    toolCalls: [],
  };

  let lastModelResponse: TraceEvent | undefined;
  let finalAnswerEvent: TraceEvent | undefined;
  for (let i = userIdx + 1; i < events.length; i++) {
    const e = events[i];
    if (e.event === "model_route_decision") {
      summary.routeModel = typeof e.model === "string" ? e.model : undefined;
      summary.routeRole = typeof e.role === "string" ? e.role : undefined;
      summary.routeReason = typeof e.reason === "string" ? e.reason : undefined;
    } else if (e.event === "tool_result") {
      summary.toolCalls.push({ name: String(e.name ?? "tool"), ok: e.ok !== false });
    } else if (e.event === "model_response") {
      lastModelResponse = e;
    } else if (e.event === "final_answer") {
      finalAnswerEvent = e;
      break; // this turn is complete — do not read into the next turn
    }
  }

  if (lastModelResponse && typeof lastModelResponse.done_reason === "string") {
    summary.doneReason = lastModelResponse.done_reason;
  }
  if (finalAnswerEvent) {
    const content = typeof finalAnswerEvent.content === "string" ? finalAnswerEvent.content : "";
    summary.finalAnswerPreview = content.length > 200 ? `${content.slice(0, 200)}…` : content;
    if (summary.userMessageTs && typeof finalAnswerEvent.ts === "string") {
      const start = Date.parse(summary.userMessageTs);
      const end = Date.parse(finalAnswerEvent.ts);
      if (!Number.isNaN(start) && !Number.isNaN(end)) summary.durationMs = Math.max(0, end - start);
    }
  }

  return summary;
}

function InspectorPanel({ onClose }: { onClose: () => void }) {
  const { events } = useTraceSocket();
  const { status } = useRuntimeStatus();
  const recent = pairToolTraceEvents(events).slice(-4);
  return (
    <motion.div initial={{ width: 0, opacity: 0 }} animate={{ width: 240, opacity: 1 }}
      exit={{ width: 0, opacity: 0 }} transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
      className="shrink-0 border-l border-white/[0.05] bg-[#141210] overflow-hidden flex flex-col">
      <div className="w-[240px] flex flex-col h-full">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.05]">
          <span className="text-xs font-semibold text-[#c8c0b7]">Inspector</span>
          <button onClick={onClose} className="text-[#3a342e] hover:text-[#8a7f75] transition-colors"><X size={13} /></button>
        </div>
        <div className="flex-1 overflow-y-auto [scrollbar-width:none] p-3 space-y-4">
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-[#3a342e] font-semibold mb-2">Tool activity</p>
            <div className="space-y-1">
              {recent.length === 0 ? (
                <div className="text-[11px] text-[#6b5f57] px-2 py-2">No tool activity yet.</div>
              ) : recent.map(e => (
                <div key={e.id} className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.02]">
                  <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${e.status === "ok" ? "bg-green-400" : "bg-red-400"}`} />
                  <span className="text-[11px] text-[#8a7f75] flex-1 truncate font-mono">{e.tool}</span>
                  <span className="text-[10px] text-[#4b4540] tabular-nums">{e.ms}ms</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-[#3a342e] font-semibold mb-2">Delegation</p>
            <div className="px-2 py-2 rounded-lg border border-white/[0.06]">
              <div className="text-[11px] text-[#6b5f57]">Not connected yet</div>
              <div className="text-[10px] text-[#4b4540] mt-px">No dedicated delegation payload</div>
            </div>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-[#3a342e] font-semibold mb-2">Context</p>
            <div className="space-y-1.5">
              {[["Active", status?.active_chat_model ?? "n/a"], ["Last", status?.last_used_model ?? "n/a"], ["Tools", status ? String(status.registered_tools.length) : "n/a"], ["Context", status ? String(status.num_ctx) : "n/a"]].map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-[10px] text-[#4b4540]">{k}</span>
                  <span className="text-[10px] text-[#8a7f75] font-mono">{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ─── Chat view ─────────────────────────────────────────────────────────────────

function ChatView({ activeConversationId, activeConversationTitle, modelState, setModelState, onNewChat, transcriptRef }: {
  activeConversationId: string | null; activeConversationTitle?: string;
  modelState: ModelState; setModelState: (s: ModelState) => void;
  onNewChat: () => void;
  transcriptRef?: React.MutableRefObject<() => string>;
}) {
  const { messages, sending, error, send, reset } = useChat();
  const activeConversationIdRef = useRef<string | null>(activeConversationId);
  const { status: runtimeStatus } = useRuntimeStatus();
  const { prefs, t } = useUiPreferences();
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const speech = useSpeech();
  const streaming = useStreamingSpeech();
  // Phase 3 (HANDOFF_v2.md) — off by default so TTS never fires
  // unexpectedly. The session's own toggle state here is unpersisted (as
  // before); only the *initial* value on a fresh mount now reads the
  // Settings > Voice "auto-speak by default" preference (Settings unfreeze
  // pass) — a plain localStorage read, since this is a frontend-only
  // behavior default the backend has no concept of.
  const [autoSpeak, setAutoSpeak] = useState(
    () => typeof window !== "undefined" && window.localStorage.getItem(AUTO_SPEAK_DEFAULT_KEY) === "1",
  );
  // Guards against auto-speaking the same assistant reply twice (e.g. a
  // future re-render/poll-driven effect around `messages`) — handleSend
  // only ever calls speech.speak() once per send() resolution today, but
  // this makes "speak each reply at most once" an explicit invariant rather
  // than an incidental property of the current wiring.
  const lastAutoSpokenMessageIdRef = useRef<string | null>(null);

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    if (!transcriptRef) return;
    transcriptRef.current = () =>
      messages.map(m => `${m.role === "user" ? "You" : "Siena"}: ${m.content}`).join("\n\n");
  }, [messages, transcriptRef]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  // Load real message history from the backend whenever the active
  // conversation changes (switching sessions in the sidebar, or the initial
  // conversation resolving after useConversations loads).
  useEffect(() => {
    if (!activeConversationId) return;
    let cancelled = false;
    sienaClient.getConversation(activeConversationId)
      .then(conv => {
        if (cancelled) return;
        reset(conv.messages.map(m => ({
          id: m.id,
          role: m.role === "assistant" ? "assistant" : "user",
          content: m.content,
          timestamp: new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          attachments: storedAttachmentsFromMessage(m),
          status: (m.metadata.status === "processing" || m.metadata.status === "failed" || m.metadata.status === "completed") ? m.metadata.status : undefined,
          error: typeof m.metadata.error === "string" ? m.metadata.error : null,
        })));
      })
      .catch(() => {
        // Backend unreachable / conversation not found — keep whatever is
        // currently rendered rather than silently wiping the screen.
      });
    return () => { cancelled = true; };
  }, [activeConversationId, reset]);

  // Switching conversations (or creating a new one) must never leave the
  // previous chat's audio playing, and must never auto-speak the incoming
  // history's last message — loading history only ever calls reset() above,
  // which never calls speech.speak(), so this stop() is purely about
  // silencing whatever was already playing from the conversation being left.
  useEffect(() => {
    speech.stop();
    streaming.stop();
    lastAutoSpokenMessageIdRef.current = null;
  }, [activeConversationId, speech.stop, streaming.stop]);

  // Returns the SendResult (not just void) so callers other than the plain
  // composer text box — specifically Voice Conversation Mode
  // (useVoiceConversation.ts) — can read the assistant's reply back and
  // speak it, without duplicating any of the actual /api/chat wiring above.
  const handleSend = useCallback(async (text: string, attachments: Attachment[]): Promise<SendResult> => {
    if (!activeConversationId) return { turn: null, errorMessage: "No active conversation selected" };
    const targetConversationId = activeConversationId;
    setModelState("thinking");
    const result = await send(
      text,
      attachments,
      targetConversationId,
      (conversationId) => activeConversationIdRef.current === conversationId,
    );
    setModelState("idle");
    if (autoSpeak && activeConversationIdRef.current === targetConversationId && result.turn && lastAutoSpokenMessageIdRef.current !== result.turn.id) {
      lastAutoSpokenMessageIdRef.current = result.turn.id;
      streaming.stop();
      speech.speak(result.turn.content, result.turn.id);
    }
    return result;
  }, [activeConversationId, send, setModelState, autoSpeak, speech, streaming]);

  // Retry (HANDOFF_v2.md) — re-sends the user message that preceded a given
  // assistant reply through the same /api/chat flow as a normal send(). Safe
  // variant chosen deliberately: only ever offered for the LATEST assistant
  // message (enforced both here and in FeedbackRow's disabled state) — this
  // never rewrites/removes the existing assistant message, it only appends a
  // fresh user+assistant turn at the end, so conversation history is never
  // mutated destructively.
  const [retryingMessageId, setRetryingMessageId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<{ messageId: string; message: string } | null>(null);

  const handleRetry = useCallback(async (assistantMessageId: string) => {
    const idx = messages.findIndex((m) => m.id === assistantMessageId);
    if (idx === -1 || idx !== messages.length - 1) return;
    let userMsg: ChatTurn | undefined;
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].role === "user") { userMsg = messages[i]; break; }
    }
    if (!userMsg) return;
    if (!activeConversationId) return;
    const targetConversationId = activeConversationId;

    sienaClient.logClientEvent("feedback_retry_requested", { message_id: assistantMessageId });
    setRetryError(null);
    setRetryingMessageId(assistantMessageId);
    setModelState("thinking");
    sienaClient.logClientEvent("feedback_retry_started", { message_id: assistantMessageId });
    try {
      const { turn: assistantTurn, errorMessage } = await send(
        userMsg.content,
        userMsg.attachments ?? [],
        targetConversationId,
        (conversationId) => activeConversationIdRef.current === conversationId,
      );
      if (assistantTurn) {
        sienaClient.logClientEvent("feedback_retry_completed", { message_id: assistantMessageId, new_message_id: assistantTurn.id });
        if (autoSpeak && activeConversationIdRef.current === targetConversationId && lastAutoSpokenMessageIdRef.current !== assistantTurn.id) {
          lastAutoSpokenMessageIdRef.current = assistantTurn.id;
          streaming.stop();
          speech.speak(assistantTurn.content, assistantTurn.id);
        }
      } else {
        const message = errorMessage ?? "Retry failed";
        sienaClient.logClientEvent("feedback_retry_failed", { message_id: assistantMessageId, error: message });
        setRetryError({ messageId: assistantMessageId, message });
      }
    } finally {
      setModelState("idle");
      setRetryingMessageId(null);
    }
  }, [messages, activeConversationId, send, setModelState, autoSpeak, speech, streaming]);

  return (
    <div className="flex h-full">
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.05] shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-[#f0ebe3]">{activeConversationId ? (activeConversationTitle ?? "Untitled chat") : "No conversation selected"}</h2>
            <div className="flex items-center gap-1.5 mt-px">
              <motion.div className="w-1.5 h-1.5 rounded-full bg-green-400" animate={{ opacity: [1, 0.4, 1] }} transition={{ duration: 2, repeat: Infinity }} />
              <span className="text-[10px] text-[#6b5f57]">{runtimeStatus?.active_chat_model ?? "Backend not connected"} · local · ready</span>
            </div>
          </div>
          <div className="flex items-center gap-0.5">
            <IconBtn icon={Search} /><IconBtn icon={Hash} />
            <IconBtn
              icon={autoSpeak ? Volume2 : VolumeX}
              onClick={() => setAutoSpeak(v => !v)}
              active={autoSpeak}
            />
            <IconBtn icon={PanelRight} onClick={() => setInspectorOpen(o => !o)} active={inspectorOpen} />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 [scrollbar-width:none]">
          {!activeConversationId ? (
            <div className="h-full flex items-center justify-center">
              <div className="text-center max-w-sm">
                <h3 className="text-sm font-semibold text-[#f0ebe3]">{t("chat.selectOrCreate")}</h3>
                <p className="text-xs text-[#6b5f57] mt-2">{t("chat.selectOrCreateSub")}</p>
                <button onClick={onNewChat} className="mt-4 px-3 py-2 rounded-lg bg-[#c4644a]/12 text-[#c4644a] text-xs font-medium border border-[#c4644a]/20 hover:bg-[#c4644a]/18 transition-colors">{t("common.newChat")}</button>
              </div>
            </div>
          ) : (
            <>
              {messages.map((msg, i) => (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  index={i}
                  speech={speech}
                  streaming={streaming}
                  conversationId={activeConversationId}
                  isLatestAssistant={msg.role === "assistant" && i === messages.length - 1}
                  retrying={retryingMessageId === msg.id}
                  retryDisabled={retryingMessageId !== null || sending}
                  retryError={retryError?.messageId === msg.id ? retryError.message : undefined}
                  onRetry={handleRetry}
                />
              ))}
              {sending && prefs.showTypingAnimation && <ThinkingIndicator />}
              {error && <div className="text-xs text-red-400 px-1 py-2">{error}</div>}
            </>
          )}
          <div ref={bottomRef} />
        </div>

        {activeConversationId && (
          <Composer
            onSend={handleSend}
            thinking={sending}
            speech={speech}
            streaming={streaming}
            conversationId={activeConversationId}
          />
        )}
      </div>
      <AnimatePresence>{inspectorOpen && <InspectorPanel onClose={() => setInspectorOpen(false)} />}</AnimatePresence>
    </div>
  );
}

// ─── Tool Trace view ───────────────────────────────────────────────────────────

function ToolTraceView() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const { events, connected } = useTraceSocket();
  const liveRows = pairToolTraceEvents(events);
  const rows = liveRows;
  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Tool Trace" sub={connected ? "Real-time tool call log for this session" : "Trace socket disconnected — reconnecting…"}>
        <IconBtn icon={Filter} /><IconBtn icon={RefreshCw} />
      </ViewHeader>
      <ViewShell>
        {rows.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">No tool calls yet this session.</div>
        ) : (
          <div className="space-y-2">
            {rows.map(e => (
              <div key={e.id} className="rounded-xl border border-white/[0.06] overflow-hidden">
                <button className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/[0.02] transition-colors text-left"
                  onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
                  <div className={`w-2 h-2 rounded-full shrink-0 ${e.status === "ok" ? "bg-green-400" : "bg-red-400"}`} />
                  <span className="font-mono text-xs text-[#c8c0b7] flex-1">{e.tool}</span>
                  <span className="text-[10px] text-[#6b5f57]">{e.ts}</span>
                  <span className={`text-[10px] tabular-nums font-mono ${e.status === "error" ? "text-red-400" : "text-[#8a7f75]"}`}>{e.ms}ms</span>
                  <Badge label={e.status === "ok" ? "success" : "error"} variant={e.status === "ok" ? "ok" : "error"} />
                  <ChevronRight size={12} className={`text-[#3a342e] transition-transform ${expanded === e.id ? "rotate-90" : ""}`} />
                </button>
                <AnimatePresence initial={false}>
                  {expanded === e.id && (
                    <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} transition={{ duration: 0.18 }} className="overflow-hidden">
                      <div className="border-t border-white/[0.05] px-4 py-3 bg-[#141210] space-y-2">
                        <div><span className="text-[10px] text-[#4b4540]">args</span><pre className="text-[11px] font-mono text-[#8a7f75] mt-1">{e.args}</pre></div>
                        <div><span className="text-[10px] text-[#4b4540]">result</span><p className="text-[11px] text-[#c8c0b7] mt-1 whitespace-pre-wrap">{e.result}</p></div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            ))}
          </div>
        )}
      </ViewShell>
    </div>
  );
}

// ─── Short Memory view ─────────────────────────────────────────────────────────

function ShortMemoryView() {
  const { entries, loading, error, refresh } = useShortMemory();
  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Short Memory" sub="Facts extracted from the current session">
        <Badge label={`${entries.length} facts`} variant="accent" /><IconBtn icon={RefreshCw} onClick={refresh} />
      </ViewHeader>
      <ViewShell>
        {error && <div className="text-xs text-red-400 mb-3">{error}</div>}
        {loading ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">Loading short memory...</div>
        ) : entries.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">No short memory entries yet.</div>
        ) : (
          <div className="space-y-2">
            {entries.map(f => (
              <div key={f.id} className="flex items-start gap-3 px-4 py-3 rounded-xl border border-white/[0.06] hover:border-white/10 transition-colors">
                <div className="w-7 h-7 rounded-lg bg-[#c4644a]/10 flex items-center justify-center shrink-0 mt-px"><Zap size={12} className="text-[#c4644a]" /></div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-[#c8c0b7] leading-relaxed">{f.text}</p>
                  <div className="flex items-center gap-3 mt-1.5"><Badge label={f.source || "memory"} /><span className="text-[10px] text-[#4b4540]">{new Date(f.created_at).toLocaleString()}</span></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </ViewShell>
    </div>
  );
}

// ─── Long Memory view ──────────────────────────────────────────────────────────

function LongMemoryView() {
  const { entries, loading, error, refresh } = useLongMemory();
  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Long Memory" sub="Persistent knowledge about the user and their work">
        <Badge label={`${entries.length} entries`} variant="accent" /><IconBtn icon={RefreshCw} onClick={refresh} />
      </ViewHeader>
      <ViewShell>
        {error && <div className="text-xs text-red-400 mb-3">{error}</div>}
        {loading ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">Loading long memory...</div>
        ) : entries.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">No long memory entries yet.</div>
        ) : (
          <div className="space-y-2">
            {entries.map(m => (
              <div key={m.id} className="flex items-start gap-3 px-4 py-3 rounded-xl border border-white/[0.06] hover:border-white/10 transition-colors">
                <div className="w-7 h-7 rounded-lg bg-white/[0.04] flex items-center justify-center shrink-0 mt-px"><Database size={12} className="text-[#8a7f75]" /></div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {m.category && <Badge label={m.category} />}
                    {m.importance && <Badge label={m.importance} variant="accent" />}
                  </div>
                  <p className="text-xs text-[#c8c0b7] leading-relaxed mt-1">{m.text}</p>
                  <p className="text-[10px] text-[#3a342e] mt-1">Updated {new Date(m.updated_at ?? m.created_at).toLocaleString()}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </ViewShell>
    </div>
  );
}

// ─── Insights view (candidate memory: human-in-the-loop review) ─────────────

const INSIGHT_STATUS_FILTERS: { id: InsightStatusFilter; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "later", label: "Later" },
  { id: "rejected", label: "Rejected" },
  { id: "promoted", label: "Promoted" },
  { id: "all", label: "All" },
];

const INSIGHT_STATUS_BADGE: Record<string, string> = {
  pending: "accent",
  later: "warn",
  rejected: "error",
  promoted: "ok",
};

function InsightsView() {
  const [statusFilter, setStatusFilter] = useState<InsightStatusFilter>("pending");
  const { entries, loading, error, actionError, actingId, refresh, promote, reject, later, remove } =
    useInsights(statusFilter);

  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Insights" sub="Candidate long-term memories proposed by Siena — human approval required">
        <div className="flex gap-1">
          {INSIGHT_STATUS_FILTERS.map(f => (
            <button key={f.id} onClick={() => setStatusFilter(f.id)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-colors ${statusFilter === f.id ? "bg-[#c4644a]/12 text-[#c4644a]" : "text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04]"}`}>
              {f.label}
            </button>
          ))}
        </div>
        <Badge label={`${entries.length}`} variant="accent" />
        <IconBtn icon={RefreshCw} onClick={refresh} />
      </ViewHeader>
      <ViewShell>
        {error && <div className="text-xs text-red-400 mb-3">{error}</div>}
        {actionError && <div className="text-xs text-red-400 mb-3">{actionError}</div>}
        {loading ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">Loading insights…</div>
        ) : entries.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">
            No {statusFilter === "all" ? "" : `${statusFilter} `}candidate memories.
          </div>
        ) : (
          <div className="space-y-3">
            {entries.map(c => {
              const acting = actingId === c.id;
              // Backend only allows promote from "pending" (tools/candidate_memory_tools.py::promote_candidate
              // requires status == "pending" and 404s otherwise); reject/later/delete have no such
              // restriction. Only offer buttons the backend will actually accept, so nothing here fails silently.
              const isPending = c.status === "pending";
              const isLater = c.status === "later";
              return (
                <div key={c.id} className="rounded-xl border border-white/[0.06] hover:border-white/10 transition-colors px-5 py-4">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge label={c.status} variant={INSIGHT_STATUS_BADGE[c.status] ?? "neutral"} />
                      {c.category && <Badge label={c.category} />}
                      {c.confidence != null && <Badge label={`confidence ${Math.round(c.confidence * 100)}%`} variant="accent" />}
                    </div>
                    <span className="text-[10px] text-[#3a342e] shrink-0">
                      {new Date(c.updated_at ?? c.created_at).toLocaleString()}
                    </span>
                  </div>

                  <div className="space-y-1.5 mb-3">
                    <p className="text-xs text-[#6b5f57]"><span className="font-semibold text-[#8a7f75]">Observation: </span>{c.observation}</p>
                    <p className="text-xs text-[#6b5f57]"><span className="font-semibold text-[#8a7f75]">Insight: </span>{c.insight}</p>
                    <p className="text-xs text-[#6b5f57]"><span className="font-semibold text-[#8a7f75]">Reflection: </span>{c.reflection}</p>
                    <p className="text-xs text-[#c8c0b7] leading-relaxed border-l-2 border-[#c4644a]/25 pl-2 mt-2">{c.proposed_memory}</p>
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    <div className="flex gap-2">
                      {isPending && (
                        <button onClick={() => promote(c.id)} disabled={acting}
                          className="px-2.5 py-1.5 rounded-lg text-[10px] font-medium border border-[#c4644a]/30 text-[#c4644a] hover:bg-[#c4644a]/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                          {acting ? "Saving…" : "Save to memory"}
                        </button>
                      )}
                      {isPending && (
                        <button onClick={() => later(c.id)} disabled={acting}
                          className="px-2.5 py-1.5 rounded-lg text-[10px] font-medium border border-white/[0.08] text-[#8a7f75] hover:text-[#c8c0b7] hover:border-white/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                          Later
                        </button>
                      )}
                      {(isPending || isLater) && (
                        <button onClick={() => reject(c.id)} disabled={acting}
                          className="px-2.5 py-1.5 rounded-lg text-[10px] font-medium border border-white/[0.08] text-[#8a7f75] hover:text-red-400 hover:border-red-400/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                          Reject
                        </button>
                      )}
                    </div>
                    <button onClick={() => remove(c.id)} disabled={acting} title="Delete permanently"
                      className="p-1.5 rounded-lg text-[#3a342e] hover:text-red-400 hover:bg-red-400/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </ViewShell>
    </div>
  );
}

// ─── Logs view ─────────────────────────────────────────────────────────────────

const LEVEL_STYLE: Record<string, string> = { INFO: "text-[#7dd3fc]", WARN: "text-amber-400", ERROR: "text-red-400" };

function LogsView() {
  const [filter, setFilter] = useState("ALL");
  const { entries, loading, error, refresh } = useLogs();
  const levels = ["ALL", "INFO", "WARN", "ERROR"];
  // Backend returns entries oldest-first (raw JSONL append order) — newest
  // should render on top. Sort by ts desc when present; entries without a
  // parseable ts fall back to reversed array order (still newest-first,
  // since the backend's own order is oldest-first) instead of being left in
  // backend order or crashing on a missing field.
  const ordered = useMemo(() => {
    const indexed = entries.map((e, i) => ({ e, i }));
    indexed.sort((a, b) => {
      const at = typeof a.e.ts === "string" ? a.e.ts : "";
      const bt = typeof b.e.ts === "string" ? b.e.ts : "";
      if (at && bt) return at === bt ? b.i - a.i : at > bt ? -1 : 1;
      if (at || bt) return at ? -1 : 1;
      return b.i - a.i;
    });
    return indexed.map(x => x.e);
  }, [entries]);
  const shown = filter === "ALL" ? ordered : ordered.filter(e => String(e.level ?? "INFO").toUpperCase() === filter);
  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Logs" sub="Runtime log stream">
        <div className="flex gap-1">
          {levels.map(l => (
            <button key={l} onClick={() => setFilter(l)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-colors ${filter === l ? "bg-[#c4644a]/12 text-[#c4644a]" : "text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04]"}`}>{l}</button>
          ))}
        </div>
        <IconBtn icon={RefreshCw} onClick={refresh} />
      </ViewHeader>
      <div className="flex-1 overflow-y-auto [scrollbar-width:none] px-4 py-3">
        {error && <div className="text-xs text-red-400 mb-3">{error}</div>}
        {loading ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">Loading logs...</div>
        ) : shown.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">No log entries yet.</div>
        ) : (
          <div className="font-mono space-y-0.5">
            {shown.map((e, i) => {
              const level = String(e.level ?? "INFO").toUpperCase();
              const rawMessage = e.console_message ?? e.content ?? e.error ?? e.event ?? "";
              const message = typeof rawMessage === "string" ? rawMessage : safeTraceJson(rawMessage);
              return (
                <div key={`${e.ts ?? i}-${e.event ?? "log"}`} className="flex gap-3 py-1.5 px-2 rounded hover:bg-white/[0.02] transition-colors">
                  <span className="text-[11px] text-[#3a342e] tabular-nums shrink-0">{formatTraceTs(e.ts)}</span>
                  <span className={`text-[11px] font-semibold w-10 shrink-0 ${LEVEL_STYLE[level] ?? "text-[#7dd3fc]"}`}>{level}</span>
                  <span className="text-[11px] text-[#6b5f57] w-28 shrink-0 truncate">{e.event}</span>
                  <span className="text-[11px] text-[#c8c0b7] break-words">{message || JSON.stringify(e)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Models view ───────────────────────────────────────────────────────────────

// Phase 4D — human-readable label for each routing_mode, matching
// config.MODEL_REGISTRY's routing_mode values exactly (see core/model_router.py).
const ROUTING_MODE_LABEL: Record<string, { text: string; variant: string }> = {
  auto: { text: "Default", variant: "accent" },
  auto_for_code: { text: "Code specialist (auto)", variant: "ok" },
  explicit_only: { text: "Explicit reviewer", variant: "warn" },
  manual_only: { text: "Manual only", variant: "warn" },
  tool: { text: "Tool", variant: "neutral" },
};

function ModelsView() {
  const { data, loading, error, refresh, setActiveChatModel, switching, activeModelError } = useModels();
  const models = data?.models ?? [];

  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Models" sub="Model registry and specialist routing (Phase 4D/4E)"><IconBtn icon={RefreshCw} onClick={refresh} /></ViewHeader>
      <ViewShell>
        {error && <div className="text-xs text-red-400 mb-3">{error}</div>}
        {activeModelError && <div className="text-xs text-red-400 mb-3">{activeModelError}</div>}
        {loading && models.length === 0 ? (
          <div className="text-xs text-[#6b5f57] text-center py-8">Loading models…</div>
        ) : (
          <div className="space-y-3">
            {models.map(m => {
              const modeInfo = ROUTING_MODE_LABEL[m.routing_mode] ?? { text: m.routing_mode, variant: "neutral" };
              // Manual active chat model switch (Phase 4E) is only offered
              // for the two models POST /api/models/active actually allows
              // (main_chat + manual_heavy_model) — never for
              // code_specialist/reviewer_critic/ocr/translator entries.
              const canBeActiveChatModel = m.routing_mode === "auto" || m.routing_mode === "manual_only";
              return (
                <div key={m.name} className={`rounded-xl border px-5 py-4 ${m.is_active_chat_model ? "border-[#c4644a]/25 bg-[#c4644a]/04" : "border-white/[0.06] hover:border-white/10"} transition-colors`}>
                  <div className="flex items-start justify-between mb-2 gap-3">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-[#f0ebe3] font-mono">{m.name}</span>
                        <Badge label={m.status} variant={m.status === "installed" ? "loaded" : m.status === "missing" ? "error" : "neutral"} />
                        <Badge label={modeInfo.text} variant={modeInfo.variant} />
                        {m.is_active_chat_model && <Badge label="Active" variant="accent" />}
                        {m.is_last_used && <Badge label="last used" variant="ok" />}
                        {!m.enabled && <Badge label="disabled" variant="neutral" />}
                      </div>
                      <span className="text-xs text-[#6b5f57] mt-0.5">{m.role}</span>
                    </div>
                    {canBeActiveChatModel && !m.is_active_chat_model && (
                      <button
                        onClick={() => setActiveChatModel(m.name)}
                        disabled={switching || m.status !== "installed"}
                        className="shrink-0 px-2.5 py-1.5 rounded-lg text-[10px] font-medium border border-white/[0.08] text-[#8a7f75] hover:text-[#c4644a] hover:border-[#c4644a]/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {switching ? "Switching…" : "Set as active chat model"}
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-[#8a7f75] leading-relaxed">{m.description}</p>
                </div>
              );
            })}
          </div>
        )}
      </ViewShell>
    </div>
  );
}

// ─── Runtime view ──────────────────────────────────────────────────────────────

function RuntimeView() {
  const { status, loading, error, refresh } = useRuntimeStatus();

  const services = [
    { name: "Ollama", status: status ? (status.ollama_status.connected ? "connected" : "degraded") : "degraded", addr: status?.ollama_host ?? "—", icon: Cpu },
    { name: "Tool runtime", status: "connected", addr: status ? `${status.registered_tools.length} tools loaded` : "—", icon: Layers },
    { name: "Runtime models", status: "connected", addr: status?.active_chat_model ?? "—", icon: Brain },
  ];

  const environment = status
    ? [
        ["Primary model", status.primary_model],
        ["Code model", status.code_model],
        ["Active chat model", status.active_chat_model],
        ["Last used model", status.last_used_model ?? "n/a (no chat turn yet)"],
        ["Last used role", status.last_used_role ?? "n/a"],
        ["Ollama host", status.ollama_host],
        ["Log level", status.log_level],
        ["Max iterations", String(status.max_iterations)],
        ["Max context messages", String(status.max_context_messages)],
      ]
    : [["Runtime", loading ? "Loading…" : "n/a"]];

  return (
    <div className="flex flex-col h-full">
      <ViewHeader title="Runtime" sub={error ? "Backend unreachable — showing last known state" : "System health and service connections"}>
        <IconBtn icon={RefreshCw} onClick={refresh} />
      </ViewHeader>
      <ViewShell>
        <div className="space-y-5">
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-[#1e1b18] rounded-xl border border-white/[0.06] px-4 py-3">
              <div className="text-[10px] text-[#6b5f57] uppercase tracking-wider">CPU</div>
              <div className="text-xl font-bold text-[#f0ebe3] mt-1">
                {status?.cpu_percent != null ? `${status.cpu_percent}%` : "n/a"}
              </div>
              <div className="mt-2 h-1 bg-white/[0.05] rounded-full overflow-hidden">
                <div className="h-full bg-[#c4644a] rounded-full" style={{ width: `${status?.cpu_percent ?? 0}%` }} />
              </div>
            </div>
            <div className="bg-[#1e1b18] rounded-xl border border-white/[0.06] px-4 py-3">
              <div className="text-[10px] text-[#6b5f57] uppercase tracking-wider">RAM</div>
              <div className="text-xl font-bold text-[#f0ebe3] mt-1">
                {status?.ram_percent != null ? `${status.ram_percent}%` : "n/a"}
              </div>
              <div className="mt-2 h-1 bg-white/[0.05] rounded-full overflow-hidden">
                <div className="h-full bg-[#c4644a] rounded-full" style={{ width: `${status?.ram_percent ?? 0}%` }} />
              </div>
              {status?.ram_total_gb != null && (
                <div className="text-[10px] text-[#4b4540] mt-1">
                  {status.ram_used_gb} / {status.ram_total_gb} GB
                </div>
              )}
            </div>
            <div className="bg-[#1e1b18] rounded-xl border border-white/[0.06] px-4 py-3">
              <div className="text-[10px] text-[#6b5f57] uppercase tracking-wider">VRAM</div>
              {status?.vram_supported ? (
                <>
                  <div className="text-xl font-bold text-[#f0ebe3] mt-1">{status.vram_percent}%</div>
                  <div className="mt-2 h-1 bg-white/[0.05] rounded-full overflow-hidden">
                    <div className="h-full bg-[#c4644a] rounded-full" style={{ width: `${status.vram_percent ?? 0}%` }} />
                  </div>
                  <div className="text-[10px] text-[#4b4540] mt-1">
                    {status.vram_used_gb} / {status.vram_total_gb} GB
                  </div>
                </>
              ) : (
                <>
                  <div className="text-sm font-semibold text-[#8a7f75] mt-1">Not available</div>
                  <div className="text-[10px] text-[#4b4540] mt-1.5 leading-relaxed">
                    {status?.vram_reason ?? (loading ? "Loading…" : "n/a")}
                  </div>
                </>
              )}
            </div>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-[#4b4540] font-semibold mb-2">Connected services</p>
            <div className="space-y-1.5">
              {services.map(s => {
                const Icon = s.icon;
                return (
                  <div key={s.name} className="flex items-center gap-3 px-4 py-2.5 rounded-xl border border-white/[0.05] hover:border-white/10 transition-colors">
                    <Icon size={14} className="text-[#6b5f57] shrink-0" />
                    <span className="text-xs font-medium text-[#c8c0b7] flex-1">{s.name}</span>
                    <span className="text-[10px] text-[#6b5f57] font-mono">{s.addr}</span>
                    <div className={`w-2 h-2 rounded-full ${s.status === "connected" ? "bg-green-400" : "bg-amber-400"}`} />
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.1em] text-[#4b4540] font-semibold mb-2">Environment</p>
            <div className="px-4 py-3 rounded-xl border border-white/[0.05] space-y-1.5">
              {environment.map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs">
                  <span className="text-[#4b4540]">{k}</span>
                  <span className="text-[#c8c0b7] font-mono">{v}</span>
                </div>
              ))}
            </div>
          </div>
          <ResourceLifecyclePanel />
        </div>
      </ViewShell>
    </div>
  );
}

// Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — honest visibility +
// safe manual controls only. No automatic keep_alive/TTL policy here; every
// action is a human clicking a button. Kept as its own component (not
// inlined into RuntimeView) since it owns its own action-in-flight/result
// state, separate from the passive useRuntimeStatus polling above it.
function ResourceLifecyclePanel() {
  const { status, loading, error, refresh } = useResourcesStatus();
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<{ text: string; isError: boolean } | null>(null);

  const runAction = async (key: string, fn: () => Promise<{ ok: boolean; message: string }>) => {
    setActionBusy(key);
    setActionMessage(null);
    try {
      const result = await fn();
      setActionMessage({ text: result.message, isError: !result.ok });
    } catch (err) {
      setActionMessage({ text: err instanceof Error ? err.message : "Action failed", isError: true });
    } finally {
      setActionBusy(null);
      await refresh();
    }
  };

  const handleStopTts = () =>
    runAction("stop-tts", async () => {
      const res = await sienaClient.stopTtsServer(false);
      return {
        ok: res.ok,
        message: res.message ?? (res.ok ? "TTS server stopped." : "Stop reported a warning — see status below."),
      };
    });

  const handleForceStopTts = () => {
    if (
      !window.confirm(
        "Force-stop the external tts-server.exe process? This only kills a process whose exe path matches Siena's expected qwentts.cpp build — never an unrelated same-named process.",
      )
    ) {
      return;
    }
    return runAction("force-stop-tts", async () => {
      const res = await sienaClient.stopTtsServer(true);
      return {
        ok: res.ok,
        message: res.ok
          ? `Force-stopped (pids: ${res.killed_pids?.join(", ") || "none"}).`
          : "Force stop could not kill the process (exe path mismatch?) — see status below.",
      };
    });
  };

  const handleUnloadTools = () =>
    runAction("unload-tools", async () => {
      const res = await sienaClient.unloadModels("tool_models");
      const failed = res.results.filter(r => !r.ok);
      return {
        ok: res.ok,
        message: res.ok
          ? `Unloaded: ${res.results.map(r => `${r.model}${r.note ? ` (${r.note})` : ""}`).join(", ") || "nothing loaded"}`
          : `Some models failed: ${failed.map(r => `${r.model} (${r.error})`).join("; ")}`,
      };
    });

  const handleUnloadAllNonChat = () => {
    if (
      !window.confirm(
        "Unload ALL non-chat models (tool models + the manual heavy model, if loaded)? The active chat model is never unloaded by this action.",
      )
    ) {
      return;
    }
    return runAction("unload-all", async () => {
      const res = await sienaClient.unloadModels("all_non_chat");
      const failed = res.results.filter(r => !r.ok);
      return {
        ok: res.ok,
        message: res.ok
          ? `Unloaded: ${res.results.map(r => `${r.model}${r.note ? ` (${r.note})` : ""}`).join(", ") || "nothing loaded"}`
          : `Some models failed: ${failed.map(r => `${r.model} (${r.error})`).join("; ")}`,
      };
    });
  };

  const tts = status?.external_processes.tts_server;
  const whisper = status?.external_processes.whisper_cli;
  const ttsExternalUnmanaged = !!tts && tts.running === true && !tts.managed_by_backend;
  const actionsDisabled = actionBusy !== null;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] uppercase tracking-[0.1em] text-[#4b4540] font-semibold">Resource lifecycle · manual (Phase 1)</p>
        <IconBtn icon={RefreshCw} onClick={refresh} />
      </div>
      {error && <div className="text-xs text-red-400 mb-2">{error}</div>}
      <div className="space-y-3">
        <div className="px-4 py-3 rounded-xl border border-white/[0.05]">
          <div className="text-[11px] font-semibold text-[#c8c0b7] mb-2">Ollama loaded models</div>
          {loading ? (
            <div className="text-xs text-[#6b5f57]">Loading…</div>
          ) : !status?.ollama_available ? (
            <div className="text-xs text-amber-400">Ollama unreachable{status?.ollama_error ? `: ${status.ollama_error}` : ""}</div>
          ) : status.ollama_loaded_models.length === 0 ? (
            <div className="text-xs text-[#6b5f57]">Nothing loaded right now.</div>
          ) : (
            <div className="space-y-1.5">
              {status.ollama_loaded_models.map(m => (
                <div key={m.name} className="flex justify-between text-xs">
                  <span className="text-[#c8c0b7] font-mono">{m.name}</span>
                  <span className="text-[#6b5f57]">
                    {m.processor} · {(m.size_bytes / 1e9).toFixed(1)}GB{m.context_length ? ` · ctx ${m.context_length}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className={`px-4 py-3 rounded-xl border ${ttsExternalUnmanaged ? "border-amber-400/25 bg-amber-400/[0.03]" : "border-white/[0.05]"}`}>
          <div className="flex items-center gap-2 mb-2">
            <div className="text-[11px] font-semibold text-[#c8c0b7]">TTS server (tts-server.exe)</div>
            {ttsExternalUnmanaged && <Badge label="External · unmanaged" variant="warn" />}
          </div>
          {tts ? (
            <div className="space-y-1 text-xs">
              <div className="flex justify-between"><span className="text-[#4b4540]">Running</span><span className="text-[#c8c0b7]">{tts.running == null ? "n/a" : tts.running ? "yes" : "no"}</span></div>
              <div className="flex justify-between"><span className="text-[#4b4540]">Managed by backend</span><span className="text-[#c8c0b7]">{tts.managed_by_backend ? "yes" : "no"}</span></div>
              {tts.pid != null && <div className="flex justify-between"><span className="text-[#4b4540]">PID</span><span className="text-[#c8c0b7] font-mono">{tts.pid}</span></div>}
              {tts.note && <div className="text-[10px] text-[#4b4540]">{tts.note}</div>}
            </div>
          ) : (
            <div className="text-xs text-[#6b5f57]">{loading ? "Loading…" : "n/a"}</div>
          )}
          <div className="flex gap-2 mt-2.5">
            <button
              onClick={handleStopTts}
              disabled={actionsDisabled}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-white/[0.04] text-[#c8c0b7] border border-white/[0.07] disabled:opacity-40 flex items-center gap-1.5"
            >
              <Square size={11} /> {actionBusy === "stop-tts" ? "Stopping…" : "Stop TTS server"}
            </button>
            {ttsExternalUnmanaged && (
              <button
                onClick={handleForceStopTts}
                disabled={actionsDisabled}
                className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-red-400/10 text-red-400 border border-red-400/20 disabled:opacity-40 flex items-center gap-1.5"
              >
                <AlertTriangle size={11} /> {actionBusy === "force-stop-tts" ? "Force stopping…" : "Force stop external"}
              </button>
            )}
          </div>
        </div>

        <div className="px-4 py-3 rounded-xl border border-white/[0.05]">
          <div className="text-[11px] font-semibold text-[#c8c0b7] mb-1.5">whisper-cli.exe</div>
          {whisper ? (
            <div className="text-xs text-[#c8c0b7]">
              {whisper.running ? `Running (pids: ${whisper.pids.join(", ")})` : "Not running"}
              <p className="text-[10px] text-[#4b4540] mt-0.5">{whisper.note}</p>
            </div>
          ) : (
            <div className="text-xs text-[#6b5f57]">{loading ? "Loading…" : "n/a"}</div>
          )}
        </div>

        <div className="px-4 py-3 rounded-xl border border-white/[0.05]">
          <div className="text-[11px] font-semibold text-[#c8c0b7] mb-2">Unload Ollama models</div>
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={handleUnloadTools}
              disabled={actionsDisabled}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-white/[0.04] text-[#c8c0b7] border border-white/[0.07] disabled:opacity-40 flex items-center gap-1.5"
            >
              <Trash2 size={11} /> {actionBusy === "unload-tools" ? "Unloading…" : "Unload tool models"}
            </button>
            <button
              onClick={handleUnloadAllNonChat}
              disabled={actionsDisabled}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-white/[0.04] text-[#c8c0b7] border border-white/[0.07] disabled:opacity-40 flex items-center gap-1.5"
            >
              <Trash2 size={11} /> {actionBusy === "unload-all" ? "Unloading…" : "Unload all non-chat"}
            </button>
          </div>
          <div className="text-[10px] text-[#4b4540] mt-1.5">Never unloads the active chat model. Avoid clicking while a message is generating (rejected with a 409 if one is).</div>
        </div>

        {actionMessage && (
          <div className={`text-xs px-3 py-2 rounded-lg ${actionMessage.isError ? "text-red-400 bg-red-400/[0.05]" : "text-green-400 bg-green-400/[0.05]"}`}>
            {actionMessage.text}
          </div>
        )}

        <div className="text-[10px] text-[#4b4540] px-1">
          Phase 1: manual visibility/control only — no automatic keep_alive/TTL policy yet ({status?.policy.phase ?? "manual_control_only"}).
        </div>
      </div>
    </div>
  );
}

// ─── Debug view ────────────────────────────────────────────────────────────────

const DEBUG_TABS = ["overview", "toolCalls", "lastRequest", "errors", "memory"] as const;
type DebugTab = typeof DEBUG_TABS[number];
const DEBUG_TAB_LABEL_KEYS: Record<DebugTab, string> = {
  overview: "debug.tabs.overview",
  toolCalls: "debug.tabs.toolCalls",
  lastRequest: "debug.tabs.lastRequest",
  errors: "debug.tabs.errors",
  memory: "debug.tabs.memory",
};

function NotConnectedYet({ label }: { label: string }) {
  const { t } = useUiPreferences();
  return (
    <div className="px-4 py-8 rounded-xl border border-white/[0.06] text-center">
      <Info size={14} className="text-[#4b4540] mx-auto mb-2" />
      <div className="text-xs font-semibold text-[#c8c0b7]">{t("common.notConnectedYet")}</div>
      <p className="text-[11px] text-[#6b5f57] mt-1">{label}</p>
    </div>
  );
}

// ─── Debug diagnostic export (0.2.0 release readiness pass) ───────────────────
// Assembles a bug-report-friendly snapshot purely from data the Debug page
// already has loaded — no extra fetches, no secrets, no attachment contents,
// no full conversation history (trace event `content` fields are truncated,
// and ollama_raw_response's full `raw` payload is dropped entirely).

const DEBUG_SECRET_KEY_PATTERN = /token|password|secret|api[_-]?key/i;

function redactSecretFields(obj: Record<string, unknown>): Record<string, unknown> {
  const clean: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    clean[k] = DEBUG_SECRET_KEY_PATTERN.test(k) ? "[redacted]" : v;
  }
  return clean;
}

function sanitizeTraceEventForExport(e: TraceEvent): TraceEvent {
  const clone: TraceEvent = { ...e };
  delete (clone as Record<string, unknown>).raw;
  if (typeof clone.content === "string" && clone.content.length > 160) {
    clone.content = `${clone.content.slice(0, 160)}…`;
  }
  return clone;
}

interface DebugReportInput {
  runtimeStatus: RuntimeStatus | null;
  runtimeError: string | null;
  settings: SettingsPayload | null;
  settingsError: string | null;
  activeConversationId?: string | null;
  traceEvents: TraceEvent[];
  errorEntries: TraceEvent[];
  resourcesStatus: ResourcesStatusResponse | null;
}

function buildDebugReport(input: DebugReportInput): Record<string, unknown> {
  return {
    generated_at: new Date().toISOString(),
    app_version: APP_VERSION,
    backend_base_url: API_BASE_URL,
    backend_reachable: !input.runtimeError,
    active_chat_model: input.runtimeStatus?.active_chat_model ?? null,
    last_used_model: input.runtimeStatus?.last_used_model ?? null,
    num_ctx: input.runtimeStatus?.num_ctx ?? null,
    settings_loaded: !input.settingsError && !!input.settings,
    settings_summary: input.settings ? redactSecretFields(input.settings as unknown as Record<string, unknown>) : null,
    active_conversation_id: input.activeConversationId ?? null,
    resources_status: input.resourcesStatus,
    recent_errors: input.errorEntries.slice(-20).map(sanitizeTraceEventForExport),
    recent_trace_events: input.traceEvents.slice(-50).map(sanitizeTraceEventForExport),
  };
}

function DebugView({ activeConversationId }: { activeConversationId: string | null }) {
  const { t } = useUiPreferences();
  const [tab, setTab] = useState<DebugTab>("overview");
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const { events: traceEvents, connected: traceConnected } = useTraceSocket();
  const { status: runtimeStatus, error: runtimeError, refresh: refreshRuntime } = useRuntimeStatus();
  const { settings, error: settingsError, refresh: refreshSettings } = useSettings();
  const { entries: debugShortMemory, refresh: refreshShortMemory } = useShortMemory();
  const { entries: debugLongMemory, refresh: refreshLongMemory } = useLongMemory();
  const { entries: pendingInsights, refresh: refreshInsights } = useInsights("pending");
  const { entries: logEntries, refresh: refreshLogs } = useLogs(200);
  const { status: resourcesStatus, refresh: refreshResources } = useResourcesStatus();
  const toolRows = pairToolTraceEvents(traceEvents);
  const lastRequest = useMemo(() => buildLastRequestSummary(traceEvents), [traceEvents]);
  const errorEntries = useMemo(
    () => logEntries.filter(e => (typeof e.level === "string" ? e.level.toUpperCase() : "") === "ERROR"),
    [logEntries],
  );

  const refreshAll = useCallback(() => {
    refreshRuntime();
    refreshSettings();
    refreshShortMemory();
    refreshLongMemory();
    refreshInsights();
    refreshLogs();
    refreshResources();
  }, [refreshRuntime, refreshSettings, refreshShortMemory, refreshLongMemory, refreshInsights, refreshLogs, refreshResources]);

  const flashCopied = (key: string) => {
    setCopyStatus(t(key));
    setTimeout(() => setCopyStatus(null), 2000);
  };

  const reportInput = (): DebugReportInput => ({
    runtimeStatus, runtimeError, settings, settingsError,
    activeConversationId, traceEvents, errorEntries, resourcesStatus,
  });

  const copyReport = async () => {
    await navigator.clipboard.writeText(JSON.stringify(buildDebugReport(reportInput()), null, 2));
    flashCopied("debug.export.copied");
  };

  const downloadReport = () => {
    const blob = new Blob([JSON.stringify(buildDebugReport(reportInput()), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `siena-debug-report-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyErrorEvent = async (e: TraceEvent) => {
    await navigator.clipboard.writeText(JSON.stringify(sanitizeTraceEventForExport(e), null, 2));
    flashCopied("debug.errors.copied");
  };

  return (
    <div className="flex flex-col h-full">
      <ViewHeader title={t("nav.debug")} sub={t("debug.subtitle")}>
        {copyStatus && <span className="text-[10px] text-green-400 mr-1">{copyStatus}</span>}
        <button onClick={copyReport} title={t("debug.export.copy")}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[11px] text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04] transition-colors">
          <Copy size={12} /> {t("debug.export.copy")}
        </button>
        <button onClick={downloadReport} title={t("debug.export.download")}
          className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-[11px] text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04] transition-colors">
          <Download size={12} /> {t("debug.export.download")}
        </button>
        <IconBtn icon={RefreshCw} onClick={refreshAll} />
      </ViewHeader>
      <div className="flex gap-1 px-5 py-2 border-b border-white/[0.05] shrink-0 overflow-x-auto [scrollbar-width:none]">
        {DEBUG_TABS.map(id => (
          <button key={id} onClick={() => setTab(id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${tab === id ? "bg-[#c4644a]/12 text-[#c4644a]" : "text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04]"}`}>{t(DEBUG_TAB_LABEL_KEYS[id])}</button>
        ))}
      </div>
      <ViewShell>
        <AnimatePresence mode="wait">
          <motion.div key={tab} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
            {tab === "overview" && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  {[
                    [t("debug.overview.backend"), runtimeError ? t("debug.overview.offline") : t("debug.overview.connected"), !runtimeError],
                    [t("debug.overview.ollama"), runtimeStatus?.ollama_status.connected ? t("debug.overview.connected") : t("debug.overview.unavailable"), !!runtimeStatus?.ollama_status.connected],
                    [t("debug.overview.activeModel"), runtimeStatus?.active_chat_model ?? "n/a", true],
                    [t("debug.overview.lastUsed"), runtimeStatus?.last_used_model ?? "n/a", true],
                    [t("debug.overview.tools"), runtimeStatus ? t("debug.overview.toolsLoaded", { count: runtimeStatus.registered_tools.length }) : "n/a", true],
                    [t("debug.overview.traceSocket"), traceConnected ? t("debug.overview.connected") : t("debug.overview.reconnecting"), traceConnected],
                    [t("debug.overview.contextWindow"), runtimeStatus ? String(runtimeStatus.num_ctx) : "n/a", true],
                    [t("debug.overview.settingsStatus"), settingsError ? t("debug.overview.settingsFailed") : t("debug.overview.settingsLoaded"), !settingsError],
                    [t("debug.overview.activeConversation"), activeConversationId ?? t("debug.overview.none"), true],
                    [t("debug.overview.appVersion"), APP_VERSION, true],
                    [t("debug.overview.mode"), t("debug.overview.modeLocal", { url: API_BASE_URL }), true],
                  ].map(([l, v, ok]) => (
                    <div key={l as string} className={`px-4 py-3 rounded-xl border ${ok ? "border-white/[0.06]" : "border-red-400/20 bg-red-400/04"}`}>
                      <div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{l}</div>
                      <div className={`text-sm font-semibold mt-1 truncate ${ok ? "text-[#f0ebe3]" : "text-red-400"}`}>{v}</div>
                    </div>
                  ))}
                </div>
                {runtimeError && <NotConnectedYet label={runtimeError} />}

                <div>
                  <p className="text-[10px] uppercase tracking-[0.1em] text-[#3a342e] font-semibold mb-2">{t("debug.runtime.title")}</p>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="px-4 py-3 rounded-xl border border-white/[0.06]">
                      <div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.runtime.ollamaModels")}</div>
                      <div className="text-sm font-semibold text-[#f0ebe3] mt-1">{resourcesStatus ? resourcesStatus.ollama_loaded_models.length : t("debug.runtime.unknown")}</div>
                    </div>
                    <div className="px-4 py-3 rounded-xl border border-white/[0.06]">
                      <div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.runtime.ttsServer")}</div>
                      <div className="text-sm font-semibold text-[#f0ebe3] mt-1">
                        {resourcesStatus?.external_processes.tts_server.running === true ? t("debug.runtime.running")
                          : resourcesStatus?.external_processes.tts_server.running === false ? t("debug.runtime.stopped")
                          : t("debug.runtime.unknown")}
                      </div>
                    </div>
                    <div className="px-4 py-3 rounded-xl border border-white/[0.06]">
                      <div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.runtime.whisper")}</div>
                      <div className="text-sm font-semibold text-[#f0ebe3] mt-1">
                        {resourcesStatus ? (resourcesStatus.external_processes.whisper_cli.running ? t("debug.runtime.running") : t("debug.runtime.stopped")) : t("debug.runtime.unknown")}
                      </div>
                    </div>
                  </div>
                  <p className="text-[10px] text-[#4b4540] mt-2">{t("debug.runtime.viewFull")}</p>
                </div>

                <div className="px-4 py-3 rounded-xl border border-white/[0.06]">
                  <div className="text-xs font-semibold text-[#c8c0b7] mb-1">{t("debug.export.title")}</div>
                  <p className="text-[10px] text-[#4b4540]">{t("debug.export.desc")}</p>
                </div>
              </div>
            )}
            {tab === "toolCalls" && (
              <div className="space-y-2">
                {toolRows.length === 0 ? (
                  <div className="text-xs text-[#6b5f57] text-center py-8">{t("debug.toolCalls.empty")}</div>
                ) : toolRows.map(e => (
                  <div key={e.id} className={`px-4 py-3 rounded-xl border ${e.status === "error" ? "border-red-400/20" : "border-white/[0.06]"}`}>
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full ${e.status === "ok" ? "bg-green-400" : "bg-red-400"}`} />
                      <span className="font-mono text-xs font-semibold text-[#f0ebe3]">{e.tool}</span>
                      <span className="text-[10px] text-[#4b4540] ml-auto">{e.ts}</span>
                      <span className="text-[10px] text-[#8a7f75] font-mono tabular-nums">{e.ms}ms</span>
                    </div>
                    <pre className="text-[11px] font-mono text-[#6b5f57] mb-1">{e.args}</pre>
                    <p className={`text-[11px] ${e.status === "error" ? "text-red-400" : "text-[#8a7f75]"}`}>{e.result}</p>
                  </div>
                ))}
              </div>
            )}
            {tab === "lastRequest" && (
              lastRequest === null ? (
                <div className="text-xs text-[#6b5f57] text-center py-8">{t("debug.lastRequest.empty")}</div>
              ) : (
                <div className="space-y-3">
                  <div className="px-4 py-3 rounded-xl border border-white/[0.06] space-y-2">
                    {lastRequest.userMessageTs && (
                      <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("debug.lastRequest.userMessageAt")}</span><span className="text-[#c8c0b7] font-mono">{formatTraceTs(lastRequest.userMessageTs)}</span></div>
                    )}
                    {lastRequest.conversationId && (
                      <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("debug.overview.activeConversation")}</span><span className="text-[#c8c0b7] font-mono truncate max-w-[60%]">{lastRequest.conversationId}</span></div>
                    )}
                    {(lastRequest.routeModel || lastRequest.routeRole || lastRequest.routeReason) && (
                      <div className="flex justify-between text-xs gap-3"><span className="text-[#4b4540] shrink-0">{t("debug.lastRequest.routing")}</span><span className="text-[#c8c0b7] font-mono text-right">{[lastRequest.routeModel, lastRequest.routeRole, lastRequest.routeReason].filter(Boolean).join(" · ")}</span></div>
                    )}
                    <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("debug.lastRequest.toolCalls")}</span><span className="text-[#c8c0b7] font-mono">{lastRequest.toolCalls.length > 0 ? lastRequest.toolCalls.map(tc => tc.name).join(", ") : t("debug.lastRequest.none")}</span></div>
                    {lastRequest.doneReason && (
                      <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("debug.lastRequest.doneReason")}</span><span className="text-[#c8c0b7] font-mono">{lastRequest.doneReason}</span></div>
                    )}
                    {lastRequest.durationMs != null && (
                      <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("debug.lastRequest.duration")}</span><span className="text-[#c8c0b7] font-mono">{lastRequest.durationMs}ms</span></div>
                    )}
                  </div>
                  {lastRequest.finalAnswerPreview && (
                    <div className="px-4 py-3 rounded-xl border border-white/[0.06]">
                      <div className="text-[10px] text-[#4b4540] uppercase tracking-wider mb-1">{t("debug.lastRequest.finalAnswer")}</div>
                      <p className="text-xs text-[#8a7f75] whitespace-pre-wrap">{lastRequest.finalAnswerPreview}</p>
                    </div>
                  )}
                </div>
              )
            )}
            {tab === "errors" && (
              <div className="space-y-2">
                {errorEntries.length === 0 ? (
                  <div className="text-xs text-[#6b5f57] text-center py-8">{t("debug.errors.empty")}</div>
                ) : errorEntries.slice(-50).reverse().map((e, i) => (
                  <div key={`${e.ts ?? i}-${i}`} className="px-4 py-3 rounded-xl border border-red-400/20">
                    <div className="flex items-center gap-2 mb-1.5">
                      <div className="w-1.5 h-1.5 rounded-full bg-red-400" />
                      <span className="font-mono text-xs font-semibold text-[#f0ebe3]">{e.event}</span>
                      <span className="text-[10px] text-[#4b4540] ml-auto">{formatTraceTs(e.ts)}</span>
                      <button onClick={() => copyErrorEvent(e)} className="text-[#4b4540] hover:text-[#8a7f75] transition-colors"><Copy size={11} /></button>
                    </div>
                    <p className="text-[11px] text-red-400">{String(e.error ?? e.console_message ?? e.reason ?? "")}</p>
                  </div>
                ))}
              </div>
            )}
            {tab === "memory" && (
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="px-4 py-3 rounded-xl border border-white/[0.06]"><div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.memory.shortMemory")}</div><div className="text-sm font-semibold text-[#f0ebe3] mt-1">{t("debug.memory.entries", { count: debugShortMemory.length })}</div></div>
                  <div className="px-4 py-3 rounded-xl border border-white/[0.06]"><div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.memory.longMemory")}</div><div className="text-sm font-semibold text-[#f0ebe3] mt-1">{t("debug.memory.entries", { count: debugLongMemory.length })}</div></div>
                  <div className="px-4 py-3 rounded-xl border border-white/[0.06]"><div className="text-[10px] text-[#4b4540] uppercase tracking-wider">{t("debug.memory.pendingInsights")}</div><div className="text-sm font-semibold text-[#f0ebe3] mt-1">{pendingInsights.length}</div></div>
                </div>
                <NotConnectedYet label={t("debug.memory.note")} />
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </ViewShell>
    </div>
  );
}

// ─── Settings ──────────────────────────────────────────────────────────────────

const SETTINGS_NAV: { id: SettingsSection; labelKey: string; icon: React.ElementType }[] = [
  { id: "appearance", labelKey: "settings.nav.appearance", icon: Moon },
  { id: "model", labelKey: "settings.nav.model", icon: Brain },
  { id: "startup", labelKey: "settings.nav.startup", icon: Zap },
  { id: "tools", labelKey: "settings.nav.tools", icon: CheckCircle },
  { id: "code", labelKey: "settings.nav.code", icon: Code2 },
  { id: "voice", labelKey: "settings.nav.voice", icon: Volume2 },
  { id: "language", labelKey: "settings.nav.language", icon: Globe },
  { id: "presence", labelKey: "settings.nav.presence", icon: Sparkles },
  { id: "developer", labelKey: "settings.nav.developer", icon: Terminal },
];

function SettingsView() {
  const [active, setActive] = useState<SettingsSection>("appearance");
  const { t } = useUiPreferences();
  return (
    <div className="flex h-full">
      <div className="w-48 shrink-0 border-r border-white/[0.05] bg-[#141210] flex flex-col">
        <div className="px-4 py-3.5 border-b border-white/[0.05]">
          <h2 className="text-sm font-semibold text-[#f0ebe3]">{t("nav.settings")}</h2>
          <p className="text-[10px] text-[#3a342e] mt-px">Siena v2 · v{APP_VERSION}</p>
        </div>
        <div className="flex-1 py-2 px-2 space-y-px overflow-y-auto [scrollbar-width:none]">
          {SETTINGS_NAV.map(({ id, labelKey, icon: Icon }) => (
            <button key={id} onClick={() => setActive(id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-all text-left ${active === id ? "bg-[#c4644a]/10 text-[#c4644a] border border-[#c4644a]/18" : "text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04] border border-transparent"}`}>
              <Icon size={13} />{t(labelKey)}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-7 py-6 [scrollbar-width:none]">
        <AnimatePresence mode="wait">
          <motion.div key={active} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.13 }} className="space-y-5 max-w-xl">
            {active === "appearance" && <AppearanceSettings />}
            {active === "model" && <ModelSettings />}
            {active === "startup" && <StartupSettings />}
            {active === "tools" && <ToolSettings />}
            {active === "code" && <CodeSettings />}
            {active === "voice" && <VoiceSettings />}
            {active === "language" && <LanguageSettings />}
            {active === "presence" && <PresenceSettings />}
            {active === "developer" && <DeveloperSettings />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

const THEME_LABEL_KEYS: Record<string, string> = { dark: "settings.appearance.themeDark", light: "settings.appearance.themeLight", system: "settings.appearance.themeSystem" };
const FONT_SIZE_LABEL_KEYS: Record<string, string> = { small: "settings.appearance.fontSizeSmall", default: "settings.appearance.fontSizeDefault", large: "settings.appearance.fontSizeLarge" };
const DENSITY_LABEL_KEYS: Record<string, string> = { comfortable: "settings.appearance.densityComfortable", compact: "settings.appearance.densityCompact" };

function AppearanceSettings() {
  const { prefs, loading, saveError, save, t } = useUiPreferences();
  const [pendingField, setPendingField] = useState<string | null>(null);

  const set = useCallback(async (field: string, value: unknown) => {
    setPendingField(field);
    await save({ [field]: value } as Partial<SettingsPayload>);
    setPendingField(null);
  }, [save]);

  return (<>
    <SectionHeader title={t("settings.appearance.title")} desc={t("settings.appearance.desc")} />
    {saveError && <div className="text-xs text-red-400">{saveError}</div>}
    <SettingsCard title={t("settings.appearance.theme")}>
      <div className="flex gap-1.5">
        {(["dark", "light", "system"] as const).map(th => (
          <button key={th} onClick={() => set("appearance_theme", th)} disabled={loading || pendingField === "appearance_theme"}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.appearanceTheme === th ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
            {t(THEME_LABEL_KEYS[th])}
          </button>
        ))}
      </div>
    </SettingsCard>
    <SettingsCard title={t("settings.appearance.accentColor")}>
      <div className="flex gap-3">
        {[{ name: "sienna", color: "#c4644a" }, { name: "slate", color: "#64748b" }, { name: "forest", color: "#4a7c59" }, { name: "amber", color: "#b45309" }, { name: "violet", color: "#7c3aed" }].map(c => (
          <button key={c.name} onClick={() => set("accent_color", c.name)} disabled={loading || pendingField === "accent_color"}
            title={c.name}
            className={`w-5 h-5 rounded-full transition-all disabled:opacity-50 ${prefs.accentColor === c.name ? "ring-2 ring-white/25 ring-offset-2 ring-offset-[#1e1b18]" : "opacity-60 hover:opacity-90"}`}
            style={{ backgroundColor: c.color }} />
        ))}
      </div>
    </SettingsCard>
    <SettingsCard title={t("settings.appearance.typography")}>
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs text-[#8a7f75]">{t("settings.appearance.fontSize")}</span>
        <div className="flex gap-1.5">
          {(["small", "default", "large"] as const).map(f => (
            <button key={f} onClick={() => set("ui_font_size", f)} disabled={loading || pendingField === "ui_font_size"}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.uiFontSize === f ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
              {t(FONT_SIZE_LABEL_KEYS[f])}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs text-[#8a7f75]">{t("settings.appearance.density")}</span>
        <div className="flex gap-1.5">
          {(["comfortable", "compact"] as const).map(d => (
            <button key={d} onClick={() => set("ui_density", d)} disabled={loading || pendingField === "ui_density"}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.uiDensity === d ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
              {t(DENSITY_LABEL_KEYS[d])}
            </button>
          ))}
        </div>
      </div>
      <Toggle label={t("settings.appearance.showTimestamps")} checked={prefs.showMessageTimestamps} onChange={v => set("show_message_timestamps", v)} disabled={loading || pendingField === "show_message_timestamps"} />
      <Toggle label={t("settings.appearance.showTypingAnimation")} sub={t("settings.appearance.showTypingAnimationSub")} checked={prefs.showTypingAnimation} onChange={v => set("show_typing_animation", v)} disabled={loading || pendingField === "show_typing_animation"} />
      <Toggle label={t("settings.appearance.copyBeforeClear")} sub={t("settings.appearance.copyBeforeClearSub")} checked={prefs.copyBeforeClearChat} onChange={v => set("copy_before_clear_chat", v)} disabled={loading || pendingField === "copy_before_clear_chat"} />
    </SettingsCard>
  </>);
}

function ModelSettings() {
  const { settings, loading, saving, error, saveError, save } = useSettings();
  const { t } = useUiPreferences();
  const [numCtx, setNumCtx] = useState(settings?.num_ctx ?? 32768);
  const [numPredict, setNumPredict] = useState(settings?.num_predict ?? 2048);
  const [maxContextMessages, setMaxContextMessages] = useState(settings?.max_context_messages ?? 40);
  const [requestTimeoutSeconds, setRequestTimeoutSeconds] = useState(settings?.request_timeout_seconds ?? 120);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    setNumCtx(settings.num_ctx);
    setNumPredict(settings.num_predict);
    setMaxContextMessages(settings.max_context_messages);
    setRequestTimeoutSeconds(settings.request_timeout_seconds);
  }, [settings]);

  return (<>
    <SectionHeader title={t("settings.model.title")} desc={t("settings.model.desc")} />
    <SettingsCard title={t("settings.model.activeModel")}>
      {error && <div className="text-xs text-red-400">{error}</div>}
      {loading ? <div className="text-xs text-[#6b5f57]">{t("settings.model.loadingSettings")}</div> : (
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("settings.model.primaryModel")}</span><span className="text-[#c8c0b7] font-mono">{settings?.primary_model ?? "n/a"}</span></div>
          <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("settings.model.codeModel")}</span><span className="text-[#c8c0b7] font-mono">{settings?.code_model ?? "n/a"}</span></div>
          <div className="text-[10px] text-[#6b5f57]">{t("settings.model.manualSwitchNote")}</div>
        </div>
      )}
    </SettingsCard>
    <SettingsCard title={t("settings.model.generationDefaults")}>
      <NumberSetting label={t("settings.model.contextWindow")} value={numCtx} onChange={setNumCtx} min={512} />
      <NumberSetting label={t("settings.model.maxTokens")} value={numPredict} onChange={setNumPredict} min={-1} />
      <NumberSetting label={t("settings.model.maxContextMessages")} value={maxContextMessages} onChange={setMaxContextMessages} min={1} />
      <NumberSetting label={t("settings.model.requestTimeout")} value={requestTimeoutSeconds} onChange={setRequestTimeoutSeconds} min={1} />
      {saveError && <div className="text-xs text-red-400">{saveError}</div>}
      {saveStatus && !saveError && <div className="text-xs text-green-400">{saveStatus}</div>}
      <button
        onClick={async () => {
          setSaveStatus(null);
          const ok = await save({
            num_ctx: numCtx,
            num_predict: numPredict,
            max_context_messages: maxContextMessages,
            request_timeout_seconds: requestTimeoutSeconds,
          });
          setSaveStatus(ok ? t("settings.model.savedToBackend") : null);
        }}
        disabled={saving || loading}
        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[#c4644a]/12 text-[#c4644a] border border-[#c4644a]/20 disabled:opacity-50"
      >
        {saving ? t("common.saving") : t("settings.model.saveButton")}
      </button>
    </SettingsCard>
  </>);
}

const STARTUP_PAGE_LABEL_KEYS: Record<string, string> = { chat: "settings.startup.pageChat", runtime: "settings.startup.pageRuntime", settings: "settings.startup.pageSettings" };

function StartupSettings() {
  const { prefs, loading, save, t } = useUiPreferences();
  const [pending, setPending] = useState(false);

  const setStartupPage = async (page: StartupPage) => {
    setPending(true);
    await save({ startup_page: page });
    setPending(false);
  };

  return (<>
    <SectionHeader title={t("settings.startup.title")} desc={t("settings.startup.desc")} />
    <SettingsCard title={t("settings.startup.page")}>
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs text-[#8a7f75]">{t("settings.startup.screenToOpen")}</span>
        <div className="flex gap-1.5">
          {(["chat", "runtime", "settings"] as const).map(p => (
            <button key={p} onClick={() => setStartupPage(p)} disabled={loading || pending}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.startupPage === p ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
              {t(STARTUP_PAGE_LABEL_KEYS[p])}
            </button>
          ))}
        </div>
      </div>
    </SettingsCard>
    <LocalOnlyNotice label={t("settings.startup.deferredBanner")} />
    <SettingsCard title={t("settings.startup.preloadWarmupTitle")}>
      <div className="text-xs text-[#6b5f57]">{t("common.notImplemented")}</div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.startup.preloadWarmupDesc")}</div>
    </SettingsCard>
    <SettingsCard title={t("settings.startup.launchAtLoginTitle")}>
      <div className="text-xs text-[#6b5f57]">{t("common.notImplemented")}</div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.startup.launchAtLoginDesc")}</div>
    </SettingsCard>
  </>);
}

// Shared by ToolSettings/CodeSettings/VoiceSettings/DeveloperSettings below
// — a single settings save button style with Saved/Saving/Error feedback,
// so each real control doesn't have to re-implement the same three lines.
function SettingsSaveStatus({ saving, saveError, saveStatus }: { saving: boolean; saveError: string | null; saveStatus: string | null }) {
  const { t } = useUiPreferences();
  if (saveError) return <div className="text-xs text-red-400">{saveError}</div>;
  if (saving) return <div className="text-xs text-[#6b5f57]">{t("common.saving")}</div>;
  if (saveStatus) return <div className="text-xs text-green-400">{saveStatus}</div>;
  return null;
}

function ToolSettings() {
  const { settings, loading, saving, error, saveError, save } = useSettings();
  const { t } = useUiPreferences();
  const [enableOcr, setEnableOcr] = useState(true);
  const [enableVision, setEnableVision] = useState(true);
  const [enableTranslator, setEnableTranslator] = useState(true);
  const [enableReviewer, setEnableReviewer] = useState(true);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    setEnableOcr(settings.enable_ocr);
    setEnableVision(settings.enable_image_understanding);
    setEnableTranslator(settings.enable_translator);
    setEnableReviewer(settings.enable_reviewer_explicit);
  }, [settings]);

  const persist = async (patch: Partial<SettingsPayload>) => {
    setSaveStatus(null);
    const ok = await save(patch);
    setSaveStatus(ok ? t("settings.tools.savedLive") : null);
  };

  return (<>
    <SectionHeader title={t("settings.tools.title")} desc={t("settings.tools.desc")} action={<Badge label={t("common.badge.persisted")} variant="ok" />} />
    {error && <div className="text-xs text-red-400">{error}</div>}
    <SettingsCard title={t("settings.tools.visionDocs")}>
      <Toggle label={t("settings.tools.ocr")} sub={t("settings.tools.ocrSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={enableOcr} disabled={loading || saving}
        onChange={(v) => { setEnableOcr(v); void persist({ enable_ocr: v }); }} />
      <Toggle label={t("settings.tools.vision")} sub={t("settings.tools.visionSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={enableVision} disabled={loading || saving}
        onChange={(v) => { setEnableVision(v); void persist({ enable_image_understanding: v }); }} />
      <SettingsSaveStatus saving={saving} saveError={saveError} saveStatus={saveStatus} />
    </SettingsCard>
    <SettingsCard title={t("settings.tools.language")}>
      <Toggle label={t("settings.tools.translator")} sub={t("settings.tools.translatorSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={enableTranslator} disabled={loading || saving}
        onChange={(v) => { setEnableTranslator(v); void persist({ enable_translator: v }); }} />
    </SettingsCard>
    <SettingsCard title={t("settings.tools.modelRouting")}>
      <Toggle label={t("settings.tools.reviewer")} sub={t("settings.tools.reviewerSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={enableReviewer} disabled={loading || saving}
        onChange={(v) => { setEnableReviewer(v); void persist({ enable_reviewer_explicit: v }); }} />
      <div className="text-[10px] text-[#4b4540]">{t("settings.tools.routingNote")}</div>
    </SettingsCard>
    <ToolCapabilityStatusCard />
  </>);
}

// Read-only replacement for the old File system / Network / Memory
// decorative toggle cards — those switches never gated anything real, and
// Siena's actual capability surface is exactly the registered-tools list
// the backend reports (api/server.py's ToolRegistry), so this shows that
// list live instead of a fake permission layer.
function ToolCapabilityStatusCard() {
  const { status } = useRuntimeStatus();
  const { t } = useUiPreferences();
  return (<>
    <LocalOnlyNotice label={t("settings.tools.capabilityBanner")} />
    <SettingsCard title={t("settings.tools.registeredTools")}>
      {status ? (
        status.registered_tools.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {status.registered_tools.map(tool => (
              <span key={tool.name} className="px-2 py-1 rounded-md text-[10px] font-mono bg-white/[0.04] border border-white/[0.06] text-[#c8c0b7]">{tool.name}</span>
            ))}
          </div>
        ) : <div className="text-xs text-[#6b5f57]">{t("settings.tools.noToolsRegistered")}</div>
      ) : (
        <div className="text-xs text-[#6b5f57]">{t("common.loading")}</div>
      )}
      <div className="text-[10px] text-[#4b4540] mt-1">{t("settings.tools.managedNote")}</div>
    </SettingsCard>
  </>);
}

function CodeSettings() {
  const { settings, loading, saving, error, saveError, save } = useSettings();
  const { t } = useUiPreferences();
  const [enableCodeAuto, setEnableCodeAuto] = useState(true);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    setEnableCodeAuto(settings.enable_code_specialist_auto);
  }, [settings]);

  return (<>
    <SectionHeader title={t("settings.code.title")} desc={t("settings.code.desc")} />
    {error && <div className="text-xs text-red-400">{error}</div>}
    <SettingsCard title={t("settings.code.specialistRouting")}>
      <Toggle
        label={t("settings.code.autoRoute")}
        sub={t("settings.code.autoRouteSub", { model: settings?.code_model ?? "n/a" })}
        badge={<Badge label={t("common.badge.persistedLive")} variant="ok" />}
        checked={enableCodeAuto}
        disabled={loading || saving}
        onChange={async (v) => {
          setEnableCodeAuto(v);
          setSaveStatus(null);
          const ok = await save({ enable_code_specialist_auto: v });
          setSaveStatus(ok ? t("settings.code.savedLive") : null);
        }}
      />
      <SettingsSaveStatus saving={saving} saveError={saveError} saveStatus={saveStatus} />
    </SettingsCard>
    <CodeDisplaySettings />
    <CodeVisibilitySettings />
  </>);
}

function CodeVisibilitySettings() {
  const { prefs, loading, save, t } = useUiPreferences();
  const [pendingField, setPendingField] = useState<string | null>(null);

  const set = useCallback(async (field: string, value: unknown) => {
    setPendingField(field);
    await save({ [field]: value } as Partial<SettingsPayload>);
    setPendingField(null);
  }, [save]);

  return (<>
    <SettingsCard title={t("settings.code.highlighting")}>
      <Toggle label={t("settings.code.syntaxHighlighting")} checked={prefs.codeSyntaxHighlighting} onChange={v => set("code_syntax_highlighting", v)} disabled={loading || pendingField === "code_syntax_highlighting"} />
      <Toggle label={t("settings.code.showLineNumbers")} checked={prefs.codeShowLineNumbers} onChange={v => set("code_show_line_numbers", v)} disabled={loading || pendingField === "code_show_line_numbers"} />
      <Toggle label={t("settings.code.showLanguageBadge")} checked={prefs.codeShowLanguageBadge} onChange={v => set("code_show_language_badge", v)} disabled={loading || pendingField === "code_show_language_badge"} />
    </SettingsCard>
    <SettingsCard title={t("settings.code.actions")}>
      <Toggle label={t("settings.code.copyButton")} checked={prefs.codeShowCopyButton} onChange={v => set("code_show_copy_button", v)} disabled={loading || pendingField === "code_show_copy_button"} />
      <Toggle label={t("settings.code.collapseButton")} checked={prefs.codeShowCollapseButton} onChange={v => set("code_show_collapse_button", v)} disabled={loading || pendingField === "code_show_collapse_button"} />
      <Toggle label={t("settings.code.saveButton")} sub={t("settings.code.saveButtonSub")} checked={prefs.codeShowSaveButton} onChange={v => set("code_show_save_button", v)} disabled={loading || pendingField === "code_show_save_button"} />
      <div className="text-[10px] text-[#4b4540]">{t("settings.code.applyRemovedNote")}</div>
    </SettingsCard>
  </>);
}

function CodeDisplaySettings() {
  const { prefs, loading, save, t } = useUiPreferences();
  const [pendingField, setPendingField] = useState<string | null>(null);

  const set = useCallback(async (field: string, value: unknown) => {
    setPendingField(field);
    await save({ [field]: value } as Partial<SettingsPayload>);
    setPendingField(null);
  }, [save]);

  return (
    <SettingsCard title={t("settings.code.fontWrap")}>
      <div className="flex items-center justify-between gap-4">
        <span className="text-xs text-[#8a7f75]">{t("settings.code.codeFontSize")}</span>
        <div className="flex gap-1.5">
          {(["small", "default", "large"] as const).map(f => (
            <button key={f} onClick={() => set("code_font_size", f)} disabled={loading || pendingField === "code_font_size"}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.codeFontSize === f ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
              {t(FONT_SIZE_LABEL_KEYS[f])}
            </button>
          ))}
        </div>
      </div>
      <Toggle label={t("settings.code.wordWrap")} sub={t("settings.code.wordWrapSub")} checked={prefs.codeLineWrap} onChange={v => set("code_line_wrap", v)} disabled={loading || pendingField === "code_line_wrap"} />
    </SettingsCard>
  );
}

function VoiceSettings() {
  const { settings, loading, saving, error, saveError, save } = useSettings();
  const { status: voiceStatus, error: voiceStatusError } = useVoiceStatus();
  const { t } = useUiPreferences();
  const [sttLanguage, setSttLanguage] = useState("ru");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [autoSpeakDefault, setAutoSpeakDefault] = useState(
    () => typeof window !== "undefined" && window.localStorage.getItem(AUTO_SPEAK_DEFAULT_KEY) === "1",
  );

  useEffect(() => {
    if (settings) setSttLanguage(settings.stt_language);
  }, [settings]);

  const persistSttLanguage = async (lang: string) => {
    setSttLanguage(lang);
    setSaveStatus(null);
    const ok = await save({ stt_language: lang });
    setSaveStatus(ok ? t("common.saved") : null);
  };

  return (<>
    <SectionHeader title={t("settings.voice.title")} desc={t("settings.voice.desc")} />
    <SettingsCard title={t("settings.voice.statusReadOnly")}>
      {voiceStatusError && <div className="text-xs text-red-400">{voiceStatusError}</div>}
      {voiceStatus ? (
        <div className="space-y-1.5 text-xs">
          <div className="flex justify-between">
            <span className="text-[#4b4540]">{t("settings.voice.stt")}</span>
            <span className="text-[#c8c0b7] font-mono">
              {voiceStatus.stt_provider ?? "n/a"} · {voiceStatus.stt_available ? t("common.available") : voiceStatus.stt_reason ?? t("common.unavailable")}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-[#4b4540]">{t("settings.voice.tts")}</span>
            <span className="text-[#c8c0b7] font-mono">
              {voiceStatus.tts_provider} · {voiceStatus.tts_available ? t("common.available") : t("common.unavailable")}
              {voiceStatus.tts_fallback_provider ? ` (fallback: ${voiceStatus.tts_fallback_provider})` : ""}
            </span>
          </div>
        </div>
      ) : (
        <div className="text-xs text-[#6b5f57]">{t("settings.voice.loadingStatus")}</div>
      )}
    </SettingsCard>
    <SettingsCard title={t("settings.voice.stt")}>
      {error && <div className="text-xs text-red-400">{error}</div>}
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs text-[#a89f96]">{t("settings.voice.defaultRecognitionLanguage")}</span>
          <p className="text-[10px] text-[#4b4540] mt-px">{t("settings.voice.defaultRecognitionLanguageSub")}</p>
        </div>
        <select
          value={sttLanguage}
          onChange={e => persistSttLanguage(e.target.value)}
          disabled={loading || saving}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-36"
        >
          <option value="auto">{t("settings.language.autoDetect")}</option>
          <option value="ru">{t("settings.language.russian")} (ru)</option>
          <option value="en">{t("settings.language.english")} (en)</option>
        </select>
      </div>
      <SettingsSaveStatus saving={saving} saveError={saveError} saveStatus={saveStatus} />
    </SettingsCard>
    <SettingsCard title={t("settings.voice.speakCard")}>
      <Toggle
        label={t("settings.voice.autoSpeak")}
        sub={t("settings.voice.autoSpeakSub")}
        badge={<Badge label={t("common.badge.localOnly")} variant="neutral" />}
        checked={autoSpeakDefault}
        onChange={(v) => {
          setAutoSpeakDefault(v);
          window.localStorage.setItem(AUTO_SPEAK_DEFAULT_KEY, v ? "1" : "0");
        }}
      />
      <VoiceStreamButtonToggle />
    </SettingsCard>
    <VoiceProfileCard />
  </>);
}

function VoiceStreamButtonToggle() {
  const { prefs, loading, save, t } = useUiPreferences();
  const [pending, setPending] = useState(false);
  return (
    <Toggle
      label={t("settings.voice.showStreamButton")}
      sub={t("settings.voice.showStreamButtonSub")}
      badge={<Badge label={t("common.badge.exp")} variant="warn" />}
      checked={prefs.showExperimentalStreamButton}
      disabled={loading || pending}
      onChange={async (v) => { setPending(true); await save({ show_experimental_stream_button: v }); setPending(false); }}
    />
  );
}

// Real, backend-persisted voice profile picker (voice/voice_profiles.py) —
// distinct from storage/settings.json, so it uses sienaClient directly
// rather than useSettings()/useUiPreferences(). Activating a profile here
// changes future Speak/Stream synthesis immediately (VoiceService reads
// voice_profile_store.get_active_profile() live, no restart needed).
function VoiceProfileCard() {
  const { t } = useUiPreferences();
  const [profiles, setProfiles] = useState<VoiceProfile[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, active] = await Promise.all([
        sienaClient.listVoiceProfiles(),
        sienaClient.getActiveVoiceProfile(),
      ]);
      setProfiles(list.profiles);
      setActiveId(active.id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load voice profiles");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const activate = async (id: string) => {
    if (id === activeId) return;
    setPendingId(id);
    try {
      const activated = await sienaClient.setActiveVoiceProfile(id);
      setActiveId(activated.id);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to activate voice profile");
    } finally {
      setPendingId(null);
    }
  };

  return (
    <SettingsCard title={t("settings.voice.profile")}>
      {error && <div className="text-xs text-red-400">{error}</div>}
      {!profiles ? (
        <div className="text-xs text-[#6b5f57]">{t("settings.voice.loadingProfiles")}</div>
      ) : (
        <div className="space-y-1.5">
          {profiles.map(p => (
            <button key={p.id} onClick={() => activate(p.id)} disabled={pendingId !== null}
              className={`w-full text-left px-3 py-2 rounded-lg border transition-colors disabled:opacity-50 ${p.id === activeId ? "bg-[#c4644a]/12 border-[#c4644a]/25" : "border-white/[0.06] hover:border-[#c4644a]/20 hover:bg-[#c4644a]/04"}`}>
              <div className="flex items-center justify-between">
                <span className={`text-xs font-medium ${p.id === activeId ? "text-[#c4644a]" : "text-[#c8c0b7]"}`}>{p.name}</span>
                {p.id === activeId && <Badge label={t("common.active")} variant="ok" />}
                {pendingId === p.id && <span className="text-[10px] text-[#6b5f57]">{t("common.activating")}</span>}
              </div>
              <div className="text-[10px] text-[#4b4540] mt-0.5">{p.speaker} · {p.language}</div>
            </button>
          ))}
        </div>
      )}
      <div className="text-[10px] text-[#4b4540]">{t("settings.voice.profileNote")}</div>
    </SettingsCard>
  );
}

function LanguageSettings() {
  const { settings, loading: settingsLoading, saving, save: saveSettings } = useSettings();
  const { prefs, loading: prefsLoading, save: savePrefs, t } = useUiPreferences();
  const [pending, setPending] = useState<string | null>(null);

  const setInterfaceLanguage = async (lang: "en" | "ru") => {
    setPending("interface");
    await savePrefs({ interface_language: lang });
    setPending(null);
  };
  const setInputLanguage = async (lang: string) => {
    setPending("input");
    await saveSettings({ stt_language: lang });
    setPending(null);
  };
  const setResponseLanguage = async (lang: "auto" | "ru" | "en") => {
    setPending("response");
    await savePrefs({ preferred_response_language: lang });
    setPending(null);
  };
  const applyPreset = async (input: string, response: "auto" | "ru" | "en") => {
    setPending("preset");
    await Promise.all([saveSettings({ stt_language: input }), savePrefs({ preferred_response_language: response })]);
    setPending(null);
  };

  const loading = settingsLoading || prefsLoading || saving || pending !== null;

  const PRESETS = [
    { nameKey: "settings.language.presetEnglishOnly", descKey: "settings.language.presetEnglishOnlyDesc", input: "en", response: "en" as const },
    { nameKey: "settings.language.presetRussianOnly", descKey: "settings.language.presetRussianOnlyDesc", input: "ru", response: "ru" as const },
    { nameKey: "settings.language.presetMixedEnRu", descKey: "settings.language.presetMixedEnRuDesc", input: "en", response: "ru" as const },
    { nameKey: "settings.language.presetMixedRuEn", descKey: "settings.language.presetMixedRuEnDesc", input: "ru", response: "en" as const },
  ];

  return (<>
    <SectionHeader title={t("settings.language.title")} desc={t("settings.language.desc")} />
    <div className="px-4 py-3 rounded-xl border border-[#c4644a]/20 bg-[#c4644a]/04 text-xs text-[#c4644a] mb-2">{t("settings.language.banner")}</div>
    <SettingsCard title={t("settings.language.interface")}>
      <div className="flex gap-2">
        {(["en", "ru"] as const).map(lang => (
          <button key={lang} onClick={() => setInterfaceLanguage(lang)} disabled={loading || pending === "interface"}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${prefs.interfaceLanguage === lang ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
            {lang === "en" ? t("settings.language.english") : t("settings.language.russian")} ({lang})
          </button>
        ))}
      </div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.language.interfaceDesc")}</div>
    </SettingsCard>
    <SettingsCard title={t("settings.language.conversation")}>
      <div className="flex items-center justify-between">
        <div><span className="text-xs text-[#a89f96]">{t("settings.language.preferredInput")}</span><p className="text-[10px] text-[#4b4540] mt-px">{t("settings.language.preferredInputSub")}</p></div>
        <select value={settings?.stt_language ?? "auto"} onChange={e => setInputLanguage(e.target.value)} disabled={loading}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-36">
          <option value="auto">{t("settings.language.autoDetect")}</option>
          <option value="ru">{t("settings.language.russian")} (ru)</option>
          <option value="en">{t("settings.language.english")} (en)</option>
        </select>
      </div>
      <div className="flex items-center justify-between">
        <div><span className="text-xs text-[#a89f96]">{t("settings.language.preferredResponse")}</span><p className="text-[10px] text-[#4b4540] mt-px">{t("settings.language.preferredResponseSub")}</p></div>
        <select value={prefs.preferredResponseLanguage} onChange={e => setResponseLanguage(e.target.value as "auto" | "ru" | "en")} disabled={loading}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-36">
          <option value="auto">{t("settings.language.autoNoPreference")}</option>
          <option value="ru">{t("settings.language.russian")} (ru)</option>
          <option value="en">{t("settings.language.english")} (en)</option>
        </select>
      </div>
    </SettingsCard>
    <SettingsCard title={t("settings.language.presets")}>
      <div className="grid grid-cols-2 gap-2">
        {PRESETS.map(p => (
          <button key={p.nameKey} onClick={() => applyPreset(p.input, p.response)} disabled={loading}
            className="text-left px-3 py-2.5 rounded-lg border border-white/[0.06] hover:border-[#c4644a]/25 hover:bg-[#c4644a]/04 transition-colors disabled:opacity-50">
            <div className="text-xs font-medium text-[#c8c0b7]">{t(p.nameKey)}</div>
            <div className="text-[10px] text-[#4b4540] mt-px">{t(p.descKey)}</div>
          </button>
        ))}
      </div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.language.presetsNote")}</div>
    </SettingsCard>
    <TranslatorSettingsCard />
  </>);
}

// Phase 4C — explicit-only translator settings. Like every other Settings
// screen in this app (see AppearanceSettings/VoiceSettings), this is local
// UI state only — there's no shared settings store or backend persistence
// for any Settings screen yet, so these toggles don't yet drive the actual
// Translate button/backend defaults (config.py is the real source of truth
// for ENABLE_TRANSLATOR/TRANSLATOR_MODEL/etc. today).
function TranslatorSettingsCard() {
  const { t } = useUiPreferences();
  const [preserveFormatting, setPreserveFormatting] = useState(
    () => typeof window !== "undefined" && window.localStorage.getItem(TRANSLATE_PRESERVE_FORMATTING_KEY) !== "0",
  );
  return (
    <SettingsCard title={t("settings.language.translator")}>
      <div className="text-[10px] text-[#4b4540]">{t("settings.language.translatorDupNote")}</div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#8a7f75]">{t("settings.language.preferredModel")}</span>
        <Badge label="translategemma:4b" variant="accent" />
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#8a7f75]">{t("settings.language.fallbackModel")}</span>
        <div className="flex items-center gap-2"><Badge label="qwen3.5:9b" variant="warn" /><span className="text-[10px] text-[#4b4540]">{t("settings.language.fallbackModelSub")}</span></div>
      </div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.language.sourceTargetNote")}</div>
      <Toggle
        label={t("settings.language.preserveFormatting")}
        sub={t("settings.language.preserveFormattingSub")}
        badge={<Badge label={t("common.badge.localOnly")} variant="neutral" />}
        checked={preserveFormatting}
        onChange={(v) => {
          setPreserveFormatting(v);
          window.localStorage.setItem(TRANSLATE_PRESERVE_FORMATTING_KEY, v ? "1" : "0");
        }}
      />
    </SettingsCard>
  );
}

// Presence layer (0.2.1, Phase 1) — real, persisted, applied live (see
// presence/presence_service.py). Every control here is used by the backend
// or the Presence Card below; nothing decorative.
function PresenceSettings() {
  const { settings, loading, saving, saveError, save } = useSettings();
  const { t } = useUiPreferences();
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  const persist = async (patch: Partial<SettingsPayload>) => {
    setSaveStatus(null);
    const ok = await save(patch);
    setSaveStatus(ok ? t("common.saved") : null);
  };

  if (!settings) {
    return <div className="text-xs text-[#6b5f57]">{t("common.loading")}</div>;
  }

  return (<>
    <SectionHeader title={t("settings.presence.title")} desc={t("settings.presence.desc")} action={<Badge label={t("common.badge.persisted")} variant="ok" />} />
    <SettingsCard title={t("settings.presence.title")}>
      <Toggle label={t("settings.presence.enable")} sub={t("settings.presence.enableSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={settings.enable_presence} disabled={loading || saving}
        onChange={(v) => void persist({ enable_presence: v })} />
      <Toggle label={t("settings.presence.showCard")} sub={t("settings.presence.showCardSub")}
        checked={settings.show_presence_card} disabled={loading || saving}
        onChange={(v) => void persist({ show_presence_card: v })} />
      <Toggle label={t("settings.presence.allowProactive")} sub={t("settings.presence.allowProactiveSub")} badge={<Badge label={t("common.badge.live")} variant="ok" />}
        checked={settings.allow_proactive_presence_messages} disabled={loading || saving}
        onChange={(v) => void persist({ allow_proactive_presence_messages: v })} />
      <SettingsSaveStatus saving={saving} saveError={saveError} saveStatus={saveStatus} />
    </SettingsCard>
    <SettingsCard title={t("settings.presence.idleMinutes")}>
      <div className="flex items-center justify-between">
        <div><span className="text-xs text-[#a89f96]">{t("settings.presence.idleMinutes")}</span><p className="text-[10px] text-[#4b4540] mt-px">{t("settings.presence.idleMinutesSub")}</p></div>
        <input type="number" min={1} value={settings.presence_idle_minutes} disabled={loading || saving}
          onChange={(e) => { const v = Number(e.target.value); if (v >= 1) void persist({ presence_idle_minutes: v }); }}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-20 text-right" />
      </div>
      <div className="flex items-center justify-between">
        <div><span className="text-xs text-[#a89f96]">{t("settings.presence.maxMessagesPerHour")}</span><p className="text-[10px] text-[#4b4540] mt-px">{t("settings.presence.maxMessagesPerHourSub")}</p></div>
        <input type="number" min={0} value={settings.presence_max_messages_per_hour} disabled={loading || saving}
          onChange={(e) => { const v = Number(e.target.value); if (v >= 0) void persist({ presence_max_messages_per_hour: v }); }}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-20 text-right" />
      </div>
    </SettingsCard>
    <SettingsCard title={t("settings.presence.quietHours")}>
      <Toggle label={t("settings.presence.quietHours")} sub={t("settings.presence.quietHoursSub")}
        checked={settings.presence_quiet_hours_enabled} disabled={loading || saving}
        onChange={(v) => void persist({ presence_quiet_hours_enabled: v })} />
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#a89f96]">{t("settings.presence.quietHoursStart")}</span>
        <input type="time" value={settings.presence_quiet_hours_start} disabled={loading || saving}
          onChange={(e) => void persist({ presence_quiet_hours_start: e.target.value })}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none" />
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#a89f96]">{t("settings.presence.quietHoursEnd")}</span>
        <input type="time" value={settings.presence_quiet_hours_end} disabled={loading || saving}
          onChange={(e) => void persist({ presence_quiet_hours_end: e.target.value })}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none" />
      </div>
    </SettingsCard>
    <SettingsCard title={t("settings.presence.style")}>
      <div className="flex gap-2">
        {(["calm", "playful", "minimal"] as const).map(style => (
          <button key={style} onClick={() => void persist({ presence_style: style })} disabled={loading || saving}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${settings.presence_style === style ? "bg-[#c4644a]/12 text-[#c4644a] border-[#c4644a]/25" : "text-[#6b5f57] border-white/[0.06] hover:text-[#c8c0b7]"}`}>
            {t(`settings.presence.style${style.charAt(0).toUpperCase()}${style.slice(1)}`)}
          </button>
        ))}
      </div>
    </SettingsCard>
  </>);
}

function DeveloperSettings() {
  const { settings, loading, saving, error, saveError, save } = useSettings();
  const { t } = useUiPreferences();
  const [logLevel, setLogLevel] = useState("info");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    if (settings) setLogLevel(settings.log_level);
  }, [settings]);

  const persistLogLevel = async (level: string) => {
    setLogLevel(level);
    setSaveStatus(null);
    const ok = await save({ log_level: level });
    setSaveStatus(ok ? t("common.saved") : null);
  };

  const electronRows: [string, string][] = [
    [t("settings.developer.contextIsolation"), t("common.enabled")],
    [t("settings.developer.nodeIntegration"), t("common.disabled")],
    [t("settings.developer.sandbox"), t("common.enabled")],
    [t("settings.developer.ipcBridge"), t("settings.developer.ipcBridgeValue")],
    [t("settings.developer.devTools"), t("settings.developer.devToolsValue")],
  ];
  const aboutRows: [string, string][] = [
    [t("settings.developer.version"), APP_VERSION],
    [t("settings.developer.electronPinned"), "^33.0.0"],
    [t("settings.developer.backend"), "Python · FastAPI · Ollama · local"],
  ];

  return (<>
    <SectionHeader title={t("settings.developer.title")} desc={t("settings.developer.desc")} />
    <SettingsCard title={t("settings.developer.logging")}>
      {error && <div className="text-xs text-red-400">{error}</div>}
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs text-[#a89f96]">{t("settings.developer.logLevel")}</span>
          <p className="text-[10px] text-[#4b4540] mt-px">{t("settings.developer.logLevelSub")}</p>
        </div>
        <select
          value={logLevel}
          onChange={e => persistLogLevel(e.target.value)}
          disabled={loading || saving}
          className="bg-[#2a2520] border border-white/[0.07] text-xs text-[#c8c0b7] rounded-lg px-2 py-1.5 outline-none w-28"
        >
          <option value="debug">debug</option>
          <option value="info">info</option>
          <option value="warn">warn</option>
          <option value="error">error</option>
        </select>
      </div>
      <SettingsSaveStatus saving={saving} saveError={saveError} saveStatus={saveStatus} />
    </SettingsCard>
    <LocalOnlyNotice label={t("settings.developer.electronBanner")} />
    <SettingsCard title={t("settings.developer.electronTitle")}>
      <div className="space-y-1.5">
        {electronRows.map(([k, v]) => (
          <div key={k} className="flex justify-between text-xs gap-4"><span className="text-[#4b4540] shrink-0">{k}</span><span className="text-[#c8c0b7] font-mono text-right">{v}</span></div>
        ))}
      </div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.developer.verboseLoggingNote")}</div>
    </SettingsCard>
    <SettingsCard title={t("settings.developer.localApiTitle")}>
      <div className="space-y-1.5">
        <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("settings.developer.port")}</span><span className="text-[#c8c0b7] font-mono">{t("settings.developer.portValue")}</span></div>
        <div className="flex justify-between text-xs"><span className="text-[#4b4540]">{t("settings.developer.auth")}</span><span className="text-[#c8c0b7] font-mono">{t("settings.developer.authValue")}</span></div>
      </div>
      <div className="text-[10px] text-[#4b4540]">{t("settings.developer.localApiNote")}</div>
    </SettingsCard>
    <SettingsCard title={t("settings.developer.about")}>
      <div className="space-y-1.5">
        {aboutRows.map(([k, v]) => (
          <div key={k} className="flex justify-between text-xs"><span className="text-[#4b4540]">{k}</span><span className="text-[#c8c0b7] font-mono">{v}</span></div>
        ))}
      </div>
    </SettingsCard>
  </>);
}

// ─── Model status widget ───────────────────────────────────────────────────────

const MODEL_LABELS: Record<ModelState, string> = { idle: "Ready", thinking: "Thinking…", generating: "Generating…", tool: "Using tools…" };
const MODEL_COLORS: Record<ModelState, string> = { idle: "text-green-400", thinking: "text-[#c4644a]", generating: "text-[#c4644a]", tool: "text-amber-400" };

function ModelStatusWidget({ state }: { state: ModelState }) {
  const { status } = useRuntimeStatus();
  const modelLabel = status?.active_chat_model ?? status?.primary_model ?? "Backend offline";
  return (
    <div className="px-3 py-2 border-t border-white/[0.05]">
      <div className="flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-white/[0.04] transition-colors cursor-default">
        <div className="relative shrink-0">
          <Cpu size={13} className="text-[#6b5f57]" />
          <motion.div className={`absolute -top-px -right-px w-1.5 h-1.5 rounded-full ${state==="idle"?"bg-green-400":"bg-[#c4644a]"}`}
            animate={{ opacity: [1, 0.4, 1] }} transition={{ duration: 2, repeat: Infinity }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-medium text-[#c8c0b7] truncate">{modelLabel}</div>
          <div className={`text-[10px] ${MODEL_COLORS[state]}`}>{MODEL_LABELS[state]}</div>
        </div>
        <Activity size={11} className="text-[#3a342e] shrink-0" />
      </div>
    </div>
  );
}

// ─── Presence card (0.2.1, Phase 1) ────────────────────────────────────────
// Local, lightweight, opt-in status indicator — never calls the model.
// Gated on both enable_presence and show_presence_card; renders nothing
// (not even a disabled placeholder) when either is off, so a disabled
// Presence layer stays fully invisible outside Settings, per task scope.

const PRESENCE_DOT: Record<string, string> = {
  available: "bg-green-400",
  idle: "bg-[#6b5f57]",
  listening: "bg-[#7dd3fc]",
  thinking: "bg-amber-400",
  speaking: "bg-[#c4644a]",
  quiet: "bg-[#3a342e]",
  offline: "bg-[#3a342e]",
  error: "bg-red-400",
};

function PresenceCard() {
  const { settings } = useSettings();
  const { status, quiet, wake, say } = usePresence();
  const { t } = useUiPreferences();
  const [sayText, setSayText] = useState<string | null>(null);
  const [saying, setSaying] = useState(false);

  if (!settings?.enable_presence || !settings?.show_presence_card || !status) return null;

  const isQuiet = status.state === "quiet";
  const stateLabel = sayText ?? t(`presence.state.${status.state}`);

  const handleSay = async () => {
    setSaying(true);
    const result = await say();
    setSayText(result.throttled ? t("presence.say.throttled") : result.message);
    setSaying(false);
  };

  return (
    <div className="px-3 pt-2">
      <div className="rounded-lg border border-white/[0.06] px-2.5 py-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <Sparkles size={12} className="text-[#c4644a] shrink-0" />
          <span className="text-[11px] font-medium text-[#c8c0b7] truncate">{t("presence.card.title")}</span>
          <span className={`ml-auto w-1.5 h-1.5 rounded-full shrink-0 ${PRESENCE_DOT[status.state] ?? "bg-[#3a342e]"}`} />
        </div>
        <div className="text-[10px] text-[#6b5f57] truncate">{stateLabel}</div>
        <div className="flex gap-1.5">
          {isQuiet ? (
            <button onClick={() => void wake()}
              className="flex-1 px-2 py-1 rounded-md text-[10px] font-medium border border-[#c4644a]/25 text-[#c4644a] hover:bg-[#c4644a]/10 transition-colors">
              {t("presence.button.wake")}
            </button>
          ) : (
            <button onClick={() => void quiet()}
              className="flex-1 px-2 py-1 rounded-md text-[10px] font-medium border border-white/[0.08] text-[#8a7f75] hover:text-[#c8c0b7] hover:border-white/20 transition-colors">
              {t("presence.button.quiet")}
            </button>
          )}
          <button onClick={() => void handleSay()} disabled={saying}
            className="flex-1 px-2 py-1 rounded-md text-[10px] font-medium border border-white/[0.08] text-[#8a7f75] hover:text-[#c8c0b7] hover:border-white/20 transition-colors disabled:opacity-40">
            {t("presence.button.say")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Sidebar ───────────────────────────────────────────────────────────────────

function Sidebar({ open, conversations, conversationsUnavailable, activeConversationId, onSelectConversation, onNewChat, view, setView, modelState }: {
  open: boolean;
  conversations: { id: string; title: string }[];
  conversationsUnavailable: boolean;
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  view: MainView; setView: (v: MainView) => void; modelState: ModelState;
}) {
  const { t } = useUiPreferences();
  return (
    <motion.aside animate={{ width: open ? 224 : 0, opacity: open ? 1 : 0 }} transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
      className="shrink-0 flex flex-col overflow-hidden border-r border-white/[0.05] bg-[#141210]">
      <div className="w-[224px] flex flex-col h-full">
        <div className="flex items-center gap-2.5 px-4 h-12 border-b border-white/[0.05]">
          <div className="w-6 h-6 rounded-md bg-gradient-to-br from-[#c4644a] to-[#7a3420] flex items-center justify-center shadow-lg">
            <span className="text-[13px] font-bold text-white leading-none">S</span>
          </div>
          <div><span className="text-xs font-bold text-[#f0ebe3] tracking-tight">Siena</span><span className="text-[9px] text-[#3a342e] ml-1.5">v2</span></div>
          <button onClick={onNewChat} className="ml-auto w-6 h-6 rounded-md hover:bg-white/[0.06] flex items-center justify-center transition-colors">
            <Plus size={12} className="text-[#6b5f57]" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto [scrollbar-width:none] py-2">
          <div className="px-2 space-y-px">
            {NAV_PRIMARY.map(({ id, labelKey, icon: Icon }) => (
              <button key={id} onClick={() => setView(id)}
                className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium transition-all ${view===id?"bg-[#c4644a]/10 text-[#c4644a] border border-[#c4644a]/18":"text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04] border border-transparent"}`}>
                <Icon size={13} />{t(labelKey)}
              </button>
            ))}
          </div>
          <AnimatePresence>
            {view === "chat" && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.18 }}>
                <div className="px-4 pt-3 pb-1"><span className="text-[9px] uppercase tracking-[0.12em] text-[#2e2a26] font-semibold">{t("sidebar.recentSessions")}</span></div>
                <div className="px-2 space-y-px">
                  {conversations.length === 0 ? (
                    <div className="px-2.5 py-1.5 text-[11px] text-[#3a342e]">
                      {conversationsUnavailable ? t("sidebar.backendUnreachable") : t("sidebar.noConversations")}
                    </div>
                  ) : conversations.map(s => (
                    <button key={s.id} onClick={() => onSelectConversation(s.id)}
                      className={`w-full text-left px-2.5 py-1.5 rounded-lg text-xs transition-colors ${activeConversationId===s.id?"text-[#c4644a]":"text-[#4b4540] hover:text-[#8a7f75]"}`}>
                      <span className="truncate block">{s.title}</span>
                    </button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="px-2 py-2 border-t border-white/[0.05] space-y-px">
          {NAV_SECONDARY.map(({ id, labelKey, icon: Icon }) => (
            <button key={id} onClick={() => setView(id)}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium transition-all ${view===id?"bg-[#c4644a]/10 text-[#c4644a] border border-[#c4644a]/18":"text-[#6b5f57] hover:text-[#c8c0b7] hover:bg-white/[0.04] border border-transparent"}`}>
              <Icon size={13} />{t(labelKey)}
            </button>
          ))}
        </div>
        <PresenceCard />
        <ModelStatusWidget state={modelState} />
      </div>
    </motion.aside>
  );
}

// ─── Splash screen ─────────────────────────────────────────────────────────────

const READINESS_STEPS = [
  { id: "backend", label: "Backend reachable" },
  { id: "runtime", label: "Runtime status loaded" },
  { id: "conversations", label: "Conversations loaded" },
  { id: "models", label: "Models loaded" },
  { id: "settings", label: "Settings loaded" },
  { id: "ready", label: "Ready" },
] as const;

type ReadinessStepId = typeof READINESS_STEPS[number]["id"];
type ReadinessStepState = "pending" | "loading" | "ok" | "error";

function SplashScreen({ onDone }: { onDone: () => void }) {
  const [states, setStates] = useState<Record<ReadinessStepId, ReadinessStepState>>(() =>
    Object.fromEntries(READINESS_STEPS.map(s => [s.id, "pending"])) as Record<ReadinessStepId, ReadinessStepState>,
  );
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const reset = () => {
      setError(null);
      setStates(Object.fromEntries(READINESS_STEPS.map(s => [s.id, "pending"])) as Record<ReadinessStepId, ReadinessStepState>);
    };
    const mark = (id: ReadinessStepId, value: ReadinessStepState) => {
      if (!cancelled) setStates(prev => ({ ...prev, [id]: value }));
    };
    const fail = (id: ReadinessStepId, message: string) => {
      mark(id, "error");
      if (!cancelled) setError(message);
    };

    const run = async () => {
      reset();
      let currentStep: ReadinessStepId = "backend";
      try {
        currentStep = "backend";
        mark("backend", "loading");
        await sienaClient.getRuntimeStatus();
        mark("backend", "ok");
        currentStep = "runtime";
        mark("runtime", "loading");
        mark("runtime", "ok");

        currentStep = "conversations";
        mark("conversations", "loading");
        await sienaClient.listConversations();
        mark("conversations", "ok");

        currentStep = "models";
        mark("models", "loading");
        await sienaClient.getModels();
        mark("models", "ok");

        currentStep = "settings";
        mark("settings", "loading");
        await sienaClient.getSettings();
        mark("settings", "ok");

        currentStep = "ready";
        mark("ready", "ok");
        window.setTimeout(() => {
          if (!cancelled) onDone();
        }, 250);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Backend readiness check failed";
        fail(currentStep, message);
      }
    };

    void run();
    return () => { cancelled = true; };
  }, [attempt, onDone]);

  const completed = READINESS_STEPS.filter(s => states[s.id] === "ok").length;
  const progress = Math.round((completed / READINESS_STEPS.length) * 100);

  return (
    <div className="w-full h-full flex flex-col items-center justify-center bg-[#0f0e0c] relative overflow-hidden">
      <motion.div className="absolute w-[600px] h-[600px] rounded-full pointer-events-none"
        style={{ background: "radial-gradient(circle, rgba(196,100,74,0.07) 0%, transparent 65%)" }}
        animate={{ scale: [1, 1.04, 1] }} transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }} />
      <motion.div initial={{ opacity: 0, scale: 0.75, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }} className="flex flex-col items-center gap-5 mb-14">
        <div className="relative">
          <div className="w-[72px] h-[72px] rounded-[20px] bg-gradient-to-br from-[#c4644a] via-[#9e4c35] to-[#6b2e1e] flex items-center justify-center shadow-2xl">
            <span className="text-[36px] font-bold text-white leading-none">S</span>
          </div>
          <motion.div className="absolute inset-0 rounded-[20px]" style={{ boxShadow: "0 0 48px rgba(196,100,74,0.28)" }}
            animate={{ opacity: [0.6, 1, 0.6] }} transition={{ duration: 2.5, repeat: Infinity }} />
        </div>
        <div className="text-center">
          <h1 className="text-[26px] font-bold text-[#f0ebe3] tracking-tight leading-none">Siena <span className="text-[#4b4540] text-lg font-medium">v2</span></h1>
          <p className="text-xs text-[#3a342e] mt-2 tracking-wide">Your local AI companion</p>
        </div>
      </motion.div>
      <div className="flex flex-col items-center gap-5 w-64">
        <div className="space-y-2 w-full">
          {READINESS_STEPS.map(({ id, label }) => {
            const state = states[id];
            return (
            <motion.div key={id} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.18 }} className="flex items-center gap-3">
              <div className={`w-4 h-4 rounded-full flex items-center justify-center shrink-0 ${state === "ok" ? "bg-green-400/15" : state === "error" ? "bg-red-400/15" : "bg-[#c4644a]/15"}`}>
                {state === "ok" ? <Check size={9} className="text-green-400" /> : state === "error" ? <AlertTriangle size={9} className="text-red-400" /> : state === "loading" ? <motion.div className="w-1.5 h-1.5 rounded-full bg-[#c4644a]" animate={{ opacity:[1,0.3,1] }} transition={{ duration:1, repeat:Infinity }} /> : <div className="w-1.5 h-1.5 rounded-full bg-[#3a342e]" />}
              </div>
              <span className={`text-xs ${state === "ok" ? "text-[#3a342e]" : state === "error" ? "text-red-400" : "text-[#8a7f75]"}`}>{label}</span>
            </motion.div>
          );})}
        </div>
        <div className="w-full h-px bg-white/[0.04] rounded-full overflow-hidden">
          <motion.div className="h-full bg-gradient-to-r from-[#7a3420] via-[#c4644a] to-[#d4795e]"
            animate={{ width: `${progress}%` }} transition={{ duration: 0.35, ease: "easeOut" }} />
        </div>
        <span className="text-[10px] text-[#2e2a26] tabular-nums">{progress}%</span>
        {error && (
          <div className="w-full rounded-xl border border-red-400/15 bg-red-400/05 px-3 py-2 text-center">
            <div className="text-xs text-red-400">Backend unavailable</div>
            <div className="text-[10px] text-[#6b5f57] mt-1 break-words">{error}</div>
            <button onClick={() => setAttempt(a => a + 1)} className="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-red-400/20 text-[10px] text-red-400 hover:bg-red-400/10 transition-colors">
              <RefreshCw size={10} /> Retry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <UiPreferencesProvider>
      <RuntimeStatusProvider>
        <TraceSocketProvider>
          <AppShell />
        </TraceSocketProvider>
      </RuntimeStatusProvider>
    </UiPreferencesProvider>
  );
}

function AppShell() {
  const [appView, setAppView] = useState<AppView>("splash");
  const [view, setView] = useState<MainView>("chat");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [modelState, setModelState] = useState<ModelState>("idle");
  const { prefs, settingsLoaded } = useUiPreferences();
  const appliedStartupPageRef = useRef(false);
  const chatTranscriptRef = useRef<() => string>(() => "");
  const [clearChatNotice, setClearChatNotice] = useState<string | null>(null);
  const {
    conversations, activeConversationId, error: conversationsError,
    createConversation, activateConversation,
  } = useConversations();

  const activeConversation = conversations.find(c => c.id === activeConversationId);

  // Startup page (Settings Pass 2) — applied exactly once, right after the
  // real settings have loaded, so it doesn't yank the user back to a
  // different view every time they later change the setting mid-session.
  useEffect(() => {
    if (!settingsLoaded || appliedStartupPageRef.current) return;
    appliedStartupPageRef.current = true;
    setView(prefs.startupPage);
  }, [settingsLoaded, prefs.startupPage]);

  const handleNewChat = useCallback(async () => {
    if (prefs.copyBeforeClearChat) {
      const transcript = chatTranscriptRef.current();
      if (transcript.trim()) {
        try {
          await navigator.clipboard.writeText(transcript);
          setClearChatNotice("Conversation copied to clipboard.");
        } catch {
          setClearChatNotice("Couldn't copy to clipboard — clearing anyway.");
        }
        setTimeout(() => setClearChatNotice(null), 3000);
      }
    }
    try {
      await createConversation();
    } catch {
      // Backend unreachable — leave current conversation untouched.
    }
    setView("chat");
  }, [createConversation, prefs.copyBeforeClearChat]);

  const handleSelectConversation = useCallback(async (id: string) => {
    try {
      await activateConversation(id);
    } catch {
      // Backend unreachable — keep the previously active conversation.
    }
    setView("chat");
  }, [activateConversation]);

  return (
    <div className="w-full h-full bg-[#0f0e0c] overflow-hidden" style={{ fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif" }}>
      <AnimatePresence mode="wait">
        {appView === "splash" ? (
          <motion.div key="splash" className="absolute inset-0" exit={{ opacity: 0, scale: 0.97 }} transition={{ duration: 0.4, ease: "easeIn" }}>
            <SplashScreen onDone={() => setAppView("main")} />
          </motion.div>
        ) : (
          <motion.div key="main" className="absolute inset-0 flex bg-[#1a1714]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35 }}>
            <button onClick={() => setSidebarOpen(o => !o)}
              className="absolute top-3.5 left-3.5 z-20 w-7 h-7 flex items-center justify-center rounded-lg text-[#3a342e] hover:text-[#6b5f57] hover:bg-white/[0.04] transition-colors">
              <Menu size={14} />
            </button>
            <Sidebar open={sidebarOpen}
              conversations={conversations} conversationsUnavailable={!!conversationsError}
              activeConversationId={activeConversationId}
              onSelectConversation={handleSelectConversation}
              onNewChat={handleNewChat}
              view={view} setView={setView} modelState={modelState} />
            <main className="flex-1 min-w-0 flex flex-col">
              <AnimatePresence mode="wait">
                <motion.div key={view} className="flex-1 min-h-0 flex flex-col"
                  initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}>
                  {view === "chat" && (
                    <ChatView
                      activeConversationId={activeConversationId}
                      activeConversationTitle={activeConversation?.title}
                      modelState={modelState} setModelState={setModelState}
                      onNewChat={handleNewChat}
                      transcriptRef={chatTranscriptRef}
                    />
                  )}
                  {view === "tool-trace" && <ToolTraceView />}
                  {view === "short-memory" && <ShortMemoryView />}
                  {view === "long-memory" && <LongMemoryView />}
                  {view === "insights" && <InsightsView />}
                  {view === "logs" && <LogsView />}
                  {view === "models" && <ModelsView />}
                  {view === "runtime" && <RuntimeView />}
                  {view === "debug" && <DebugView activeConversationId={activeConversationId} />}
                  {view === "settings" && <SettingsView />}
                </motion.div>
              </AnimatePresence>
            </main>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {clearChatNotice && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="absolute top-3.5 left-1/2 -translate-x-1/2 z-30 px-3 py-1.5 rounded-lg border border-white/[0.08] bg-[#221e1b] text-xs text-[#c8c0b7] shadow-lg"
          >
            {clearChatNotice}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
