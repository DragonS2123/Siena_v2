# Siena v2 — Developer Handoff Document

**Version:** 0.9.4-beta  
**Build:** 2025-07-06  
**Stack:** React + Tailwind CSS → Electron 33 / Node 20 (target)  
**Backend:** Python 3.12 · Ollama · llama.cpp  
**Document scope:** UI behaviour, component inventory, integration contracts, implementation checklist.

---

## Table of Contents

1. [Design tokens and typography](#1-design-tokens-and-typography)
2. [Application shell and navigation](#2-application-shell-and-navigation)
3. [Splash / startup screen](#3-splash--startup-screen)
4. [Chat view](#4-chat-view)
5. [Chat composer](#5-chat-composer)
6. [Attachment and clipboard system](#6-attachment-and-clipboard-system)
7. [Assistant response feedback](#7-assistant-response-feedback)
8. [Voice Orb and streaming voice states](#8-voice-orb-and-streaming-voice-states)
9. [Inspector panel](#9-inspector-panel)
10. [Tool Trace view](#10-tool-trace-view)
11. [Short Memory view](#11-short-memory-view)
12. [Long Memory view](#12-long-memory-view)
13. [Logs view](#13-logs-view)
14. [Models view](#14-models-view)
15. [Runtime view](#15-runtime-view)
16. [Debug view](#16-debug-view)
17. [Settings](#17-settings)
18. [Placeholder and stub inventory](#18-placeholder-and-stub-inventory)
19. [Required frontend events (IPC → UI)](#19-required-frontend-events-ipc--ui)
20. [Required backend events (UI → Python)](#20-required-backend-events-ui--python)
21. [Electron integration notes](#21-electron-integration-notes)
22. [Implementation checklist](#22-implementation-checklist)

---

## 1. Design tokens and typography

### Color palette

| Token | Value | Usage |
|---|---|---|
| `--background` | `#1a1714` | Main app background |
| `--card` | `#221e1b` | Panel / card surface |
| `--surface-elevated` | `#1e1b18` | Composer, input fields |
| `--surface-deep` | `#141210` | Sidebar, splash |
| `--code-bg` | `#0f0e0c` | Code block background |
| `--primary` | `#c4644a` | Terracotta accent, active states |
| `--primary-dim` | `rgba(196,100,74,0.12)` | Selected nav items, user bubbles |
| `--foreground` | `#f0ebe3` | Cream — primary text |
| `--foreground-mid` | `#c8c0b7` | Secondary text |
| `--foreground-muted` | `#8a7f75` | Labels, captions |
| `--foreground-dim` | `#6b5f57` | De-emphasised text |
| `--foreground-ghost` | `#3a342e` | Placeholder text |
| `--border` | `rgba(255,255,255,0.07)` | Hairline borders |
| `--border-active` | `rgba(196,100,74,0.25)` | Active/selected borders |
| `--green` | `#86c98e` | Code strings, success states |
| `--blue` | `#7dd3fc` | Code functions |
| `--purple` | `#c084fc` | Code keywords |
| `--amber-code` | `#fbbf24` | Code types/classes |
| `--orange-code` | `#e6956a` | Code numbers |
| `--coral-code` | `#fb923c` | Code decorators |
| `--state-ok` | `#4ade80` | Online / success dot |
| `--state-warn` | `#f59e0b` | Warning / fallback |
| `--state-error` | `#ef4444` | Error states |

### Typography

| Role | Family | Weight | Size |
|---|---|---|---|
| UI / body | Plus Jakarta Sans | 400–700 | 11–14px |
| Code | JetBrains Mono | 400–500 | 13px |
| Splash wordmark | Plus Jakarta Sans | 700 | 26px |

### Radius and spacing

- Base radius: `0.75rem` (12px)
- Sidebar width (open): `224px`
- Inspector panel width: `240px`
- Composer max height before scroll: `200px`

---

## 2. Application shell and navigation

### Top-level views

```
AppView: "splash" | "main"
```

The splash screen is shown on launch. After the warm-up sequence completes it transitions to the main shell with a fade + scale exit.

### Main shell layout

```
┌──────────────────────────────────────────────────────────┐
│ [☰ toggle]  [Sidebar 224px]  [Main content area flex-1]  │
└──────────────────────────────────────────────────────────┘
```

The sidebar toggle button (`☰`) is positioned absolutely at `top: 14px, left: 14px, z-index: 20`. Clicking it animates the sidebar width between `224px` and `0` via `motion`.

### Sidebar navigation order

**Primary nav** (top section, scrollable):

| Position | Label | Icon | View key |
|---|---|---|---|
| 1 | Chat | MessageSquare | `chat` |
| 2 | Tool Trace | Workflow | `tool-trace` |
| 3 | Short Memory | Zap | `short-memory` |
| 4 | Long Memory | Database | `long-memory` |
| 5 | Logs | ScrollText | `logs` |
| 6 | Models | Cpu | `models` |
| 7 | Runtime | Activity | `runtime` |

When `chat` is the active view, a collapsible **Recent sessions** list appears below the primary nav. It shows the 5 most recent session titles. Clicking a session sets the active session ID and navigates to chat.

**Secondary nav** (bottom section, below a border divider):

| Position | Label | Icon | View key |
|---|---|---|---|
| 8 | Debug | Bug | `debug` |
| 9 | Settings | Settings | `settings` |

> **Important:** Debug must appear above Settings. This order is intentional and must be preserved.

**Model status widget** (bottom of sidebar, always visible):

- Shows active model name (`Llama 3.2 · 7B Q4`)
- Shows model state label (`Ready`, `Thinking…`, `Generating…`, `Using tools…`)
- Shows an animated pulse dot: green when idle, terracotta when active
- Receives `modelState` prop from parent (`idle | thinking | generating | tool`)

### View transitions

Each view switch animates with `opacity: 0 → 1`, `duration: 0.12s`. Uses `AnimatePresence mode="wait"` so the exiting view fades before the entering view appears.

---

## 3. Splash / startup screen

### Purpose

Shown while the Python backend initialises local models. Duration is driven by actual backend readiness events. The current UI uses a timed demo sequence (520ms per step).

### Loading steps (in order)

```
1. Initializing Siena
2. Loading local models
3. Warming up inference engine
4. Preparing memory
5. Starting tools
6. Initializing voice services
7. Ready
```

Each completed step shows a green check icon. The current/active step shows a pulsing terracotta dot. Steps appear sequentially with a slide-in animation (`x: -10 → 0, opacity: 0 → 1`).

### Visual elements

- Background: `#0f0e0c`
- Radial ambient glow: `rgba(196,100,74,0.07)`, 600px, animates scale `1 → 1.04 → 1` over 5s
- Logo: 72×72 rounded square (`border-radius: 20px`), gradient `#c4644a → #9e4c35 → #6b2e1e`, letter "S" in white bold 36px
- Logo glow: `box-shadow: 0 0 48px rgba(196,100,74,0.28)`, pulses opacity `0.6 → 1 → 0.6`
- Wordmark: "Siena" bold 26px + "v2" muted 18px
- Sub-label: "Your local AI companion" ghost-colored
- Progress bar: 1px height, gradient `#7a3420 → #c4644a → #d4795e`, animates `width` based on completed steps
- Progress percentage: tabular-nums, ghost-colored

### Integration stub

Replace `setInterval` demo timer with IPC events:
- `backend:stepComplete(stepIndex)` → advance the step
- `backend:ready()` → trigger transition to main shell

---

## 4. Chat view

### Layout

```
┌─────────────────────────────────────┬──────────────┐
│  Chat header (title + model status) │              │
├─────────────────────────────────────┤  Inspector   │
│  Message list (scrollable)          │  Panel       │
│                                     │  (240px,     │
│                                     │  optional)   │
├─────────────────────────────────────┤              │
│  Composer                           │              │
└─────────────────────────────────────┴──────────────┘
```

### Chat header

- Shows active session title
- Animated green pulse dot + "Llama 3.2 · 7B Q4 · local · ready"
- Right buttons: Search (stub), Hash (stub), Inspector toggle (`PanelRight` icon, active state = terracotta bg)

### Message types

**User message:**
- Right-aligned, avatar "U" in terracotta
- Bubble: `bg-[#c4644a]/12`, `border-[#c4644a]/18`, `rounded-2xl rounded-tr-sm`
- Attachments (if any) appear as chips **above** the bubble, right-aligned

**Assistant message:**
- Left-aligned, avatar "S" in graphite
- No bubble — plain text on the background
- Code blocks appear inline below the text
- Feedback row appears below (see §7)

**Thinking indicator:**
- Three terracotta dots with staggered `opacity` + `scale` pulse animation
- Appears in place of the next assistant message while `thinking === true`

### Message animations

Each message animates in: `opacity: 0 → 1`, `y: 10 → 0`, `duration: 0.22s`. Delay is `index × 0.03s`, capped at `0.15s`.

### Scroll behaviour

`bottomRef` div at the end of the message list. `scrollIntoView({ behavior: "smooth" })` is called whenever `messages` or `thinking` changes.

---

## 5. Chat composer

### Default state

Single-line input. Contains (left to right):

1. **Attachment button** (`Paperclip`) — opens attachment menu popup
2. **Textarea** — auto-resizing, placeholder "Ask Siena anything…"
3. **Mic button** (`Mic`) — triggers voice mode
4. **Send button** (`Send`) — terracotta circle, disabled when empty

### Auto-resize behaviour

- On every value change: `textarea.style.height = "auto"` then `height = min(scrollHeight, 200px)`
- When `scrollHeight > 200px`: `overflowY = "auto"` (internal scroll)
- The outer `motion.div` has `layout` prop — height changes animate smoothly at `0.22s`

### Keyboard behaviour

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift + Enter` | Insert newline, composer grows upward |

### Disabled state

When `thinking === true`: textarea is `disabled`, placeholder changes to "Siena is responding…", send button opacity drops to 25%.

### Composer states summary

| State | Visual change |
|---|---|
| Default empty | Single-line, ghost placeholder |
| Typing | Textarea grows with content |
| Max height | Internal scroll activates |
| Attachment present | Chip area slides in above input |
| Voice active | Voice panel slides in above input |
| Voice + attachment | Both sections stack above input |
| Thinking | Textarea disabled, reduced opacity |
| Error (voice) | Mic button turns red |

### Footer hints

Left: `⏎ send · ⇧⏎ newline · ⌘V paste image`  
Right: context-sensitive — shows `STT active · Whisper` or `TTS streaming` during voice, otherwise `Llama 3.2 · local · 4 096 ctx`

---

## 6. Attachment and clipboard system

### Attachment types

```ts
type AttachmentType = "image" | "document" | "code" | "text";

interface Attachment {
  id: string;
  type: AttachmentType;
  name: string;
  size: string;        // formatted: "3.2 KB"
  lang?: string;       // code only: "python" | "typescript" | "go" | "text"
  dataUrl?: string;    // image only: base64 data URL
  preview?: string;    // code/text: first 180 chars
}
```

### Attachment menu

Opens above the `Paperclip` button as an animated popup (`y: 6 → 0, scale: 0.97 → 1`). Closes on outside click.

| Option | Accept string | Icon |
|---|---|---|
| Image | `image/*` | ImageIcon (blue) |
| Document | `.pdf,.doc,.docx,.txt,.md` | FileText (amber) |
| Code / text file | `.py,.js,.ts,.tsx,.rs,.go,.cpp,.java,.sh,.json,.yaml` | FileCode (purple) |
| Project folder | — | FolderOpen — labelled "dev" badge (stub) |

File selection uses a hidden `<input type="file">`. Type and accept string are set via `useRef` before `.click()` is called.

### Clipboard paste handling

Handled via `onPaste` on the textarea.

| Clipboard content | Behaviour |
|---|---|
| Image (`image/*` MIME) | `preventDefault()`, read as DataURL via `FileReader`, create image `Attachment`, show chip |
| Text ≤ 400 chars | Default browser paste into textarea |
| Text > 400 chars (plain) | `preventDefault()`, create `text` attachment chip with 180-char preview |
| Text > 400 chars (code detected) | `preventDefault()`, create `code` attachment chip with language badge and preview |

**Code detection** (`isLikelyCode`): checks for leading keywords (`import`, `from`, `def`, `class`, `const`, `function`, etc.) or structural heuristics (braces + colons, 4+ lines with indentation).

**Language detection** (`detectLang`): regex against leading tokens — returns `"python"`, `"typescript"`, `"go"`, or `"text"`.

### Attachment chip rendering

**Image chip:** thumbnail (48×48 crop) + filename + size + remove button.

**Code/text/document chip:** type icon (color-coded) + filename + `lang · size` + optional expand/collapse for preview + remove button.

Chips appear in a horizontally-wrapping flex row inside the composer, separated from the input by a hairline border. The chip area animates in/out with `height: 0 → auto`.

### Attachments in sent messages

When a message is sent with attachments:
- User message bubbles show attachment chips **above** the text bubble, right-aligned
- Image attachments use the compact chip format (thumbnail + name)
- Code/document attachments use the collapsed chip format

### Backend stubs required

- File content reading for code/document attachments
- Multimodal image handling (pass `dataUrl` or file reference to the model)
- Attachment metadata extraction (MIME type, encoding)
- Pasted text/code storage for context injection

---

## 7. Assistant response feedback

A row of action buttons appears **below every assistant message** on hover (`group-hover`). Does not appear under user messages.

### Buttons (left to right)

| Button | Icon | Default state | Active state |
|---|---|---|---|
| Like | ThumbsUp | Ghost | Green bg + green icon |
| Dislike | ThumbsDown | Ghost | Red bg + red icon |
| — | Divider | `1px h-3 white/7%` | — |
| Copy response | Copy | Ghost | Switches to Check + "Copied" for 2s |
| Retry | RotateCcw | Ghost | — (stub) |
| Save to memory | BookmarkPlus | Ghost | Terracotta when saved |

### Behaviour

- Like/Dislike are mutually exclusive toggles (clicking the active one deselects)
- Copy writes `message.content` to clipboard
- Retry: **stub** — requires `message:regenerate(messageId)` IPC
- Save: **stub** — requires `memory:saveMessage(messageId)` IPC

### Visual

Opacity `0` by default, `1` on `group-hover` of the message container. Transition `150ms`. Buttons are `11px` text with matching icons at `12px`. No background by default; hover shows `white/4%` bg.

---

## 8. Voice Orb and streaming voice states

### Voice state type

```ts
type VoiceState =
  | "idle"
  | "listening"
  | "speaking-user"
  | "transcribing"
  | "thinking"
  | "speaking-siena"
  | "fallback"
  | "error-mic"
  | "error-tts";
```

### State descriptions

| State | Trigger | Primary label | Sub-label |
|---|---|---|---|
| `idle` | Default | — | — |
| `listening` | Mic button pressed | Listening… | Whisper active · speak now |
| `speaking-user` | VAD detects voice | Hearing you… | Whisper STT · live input |
| `transcribing` | Speech ends | Transcribing… | Whisper processing |
| `thinking` | Transcription ready | Processing… | Siena is thinking |
| `speaking-siena` | TTS starts | Siena is speaking… | faster_qwen3-tts streaming |
| `fallback` | Silero active | Siena is speaking… | Silero fallback active |
| `error-mic` | Mic permission denied | Microphone unavailable | Check permissions |
| `error-tts` | TTS provider crashed | TTS provider failed | Check Voice settings |

### Voice Orb SVG anatomy

The orb is a `72×72` SVG (`viewBox="0 0 72 72"`, `cx=36, cy=36, r=32`).

**Layers (bottom to top):**

1. **Radial gradient fill** — ambient coloured fill, visible only when `isActive`. Siena states = terracotta-tinted; user states = cream-tinted.
2. **Base ring** — `r=32`, `stroke: rgba(240,235,227,0.09)`, `strokeWidth: 1`
3. **Inner ring** — `r=23`, `stroke: rgba(240,235,227,0.04)`, `strokeWidth: 0.5`
4. **Rotating arc group** — `motion.g` with continuous `rotate: [0, 360]` animation. Speed varies by state:
   - `speaking-user`: 2.5s (most reactive)
   - `transcribing`: 3.5s
   - `speaking-siena` / `fallback`: 5s
   - `listening`: 6.5s (calmest)
5. **Arc circle** — `motion.circle` inside the group. `strokeDasharray = 201.06` (full circumference). `strokeDashoffset` animated to `circ × (1 - clamp(amplitude, 0.07, 0.6))`. Transition `0.11s easeOut`. The arc is offset by `rotate(-90 36 36)` so it starts at 12 o'clock.
6. **Error cross** — two 45° lines replacing the arc on error states.
7. **Center dot** — `r=2.5` circle. Terracotta when Siena is speaking, cream otherwise. Pulses `scale: 1 → 1.5 → 1` continuously while active.

### Arc colour by state

| State | Arc stroke |
|---|---|
| `idle` | `rgba(240,235,227,0.2)` |
| `listening` | `rgba(240,235,227,0.55)` |
| `speaking-user` | `rgba(240,235,227,0.88)` |
| `transcribing` | `rgba(196,100,74,0.65)` |
| `thinking` | `rgba(196,100,74,0.42)` |
| `speaking-siena` | `#c4644a` (solid terracotta) |
| `fallback` | `#d4975e` (warm amber-sienna) |
| `error-mic` / `error-tts` | `#ef4444` |

### Amplitude

The amplitude value (0–1) drives arc length. Source:

| State | Current (stub) | Production source |
|---|---|---|
| `speaking-user` | Random walk: `prev + (rand - 0.36) × 0.45`, clamped `0.18–0.95` | `voice:amplitude` event from Whisper VAD |
| `speaking-siena` / `fallback` | Sine: `0.48 + 0.32 × sin(t × 1.4)` | `tts:amplitude` event from TTS playback |
| `listening` | Sine: `0.28 + 0.22 × sin(t × 0.65)` | `voice:amplitude` idle level |
| All others | `0` | — |

Interval: 45ms (~22fps). In production, replace `setInterval` with IPC event listeners.

### Voice panel placement

The voice panel is **inside the composer box**, above the input row. It animates in with `height: 0 → auto, opacity: 0 → 1` when `voiceState !== "idle"`.

**Panel layout:**
```
[VoiceOrb 72px] [VoiceStateText flex-1] [Stop button]
```

The `VoiceStateText` component cross-fades between states using `AnimatePresence mode="wait"`. The `transcribing` state additionally shows three staggered pulsing dots below the sub-label.

### Mic button states

| Voice state | Button appearance |
|---|---|
| `idle` | Ghost icon (`#4b4540`) |
| `listening` / `speaking-user` | Cream icon + cream bg + expanding pulse ring |
| `speaking-siena` / `fallback` | Terracotta icon + terracotta bg |
| `error-*` | Red icon + red bg |

The pulse ring: absolute span, `border: 1px solid rgba(255,255,255,0.3)`, `scale: 1 → 1.6, opacity: 0.5 → 0`, `duration: 1.1s, repeat: Infinity`.

### Demo voice flow (stub, to be replaced)

```
Click mic → listening (1.1s) → speaking-user (2.7s) →
transcribing (1.6s) → thinking (1.3s) → speaking-siena (3.3s) → idle
```

Russian demo query is inserted into the textarea when transcription completes: "Как работает asyncio.gather с обработкой ошибок?"

### STT integration: Whisper

- Provider: Whisper (local process, `base.en` model by default)
- Input: raw PCM audio from selected microphone
- Output: transcription text
- Events expected from backend: `voice:amplitude`, `voice:transcribing`, `voice:transcriptionReady`
- Settings: input device, recognition language (en/ru/auto), VAD sensitivity, push-to-talk vs continuous, auto-send

### TTS integration: faster_qwen3-tts + Silero

**Primary:** `faster_qwen3-tts`  
**Fallback:** `Silero` (activates automatically when GPU unavailable or primary fails)

When fallback is active, `voiceState` is set to `"fallback"` (amber arc) instead of `"speaking-siena"` (terracotta arc). The sub-label changes to "Silero fallback active".

Events expected from backend: `tts:startStreaming`, `tts:audioChunk`, `tts:amplitude`, `tts:fallbackActivated`, `tts:finished`

---

## 9. Inspector panel

An optional right-side panel in the chat view. Width: `240px`. Toggle with the `PanelRight` icon button in the chat header.

### Sections

**Tool activity** — list of recent tool calls (last 4) with coloured status dot, tool name (monospace), and duration in ms.

**Delegation** — shows the most recent model delegation event: delegated model name, task type, duration.

**Context** — four key-value rows:
- Model name
- Token usage (`1 284 / 4 096`)
- Memory fact count
- Truncated session title

A token usage progress bar (terracotta) appears below the context rows, width proportional to `tokens_used / context_window`.

### Stub

All data is demo values. Production: subscribe to session context updates via IPC.

---

## 10. Tool Trace view

### Purpose

Chronological log of all tool calls in the current session.

### Layout

List of expandable cards. Each card:

- Status dot (green = success, red = error)
- Tool name (monospace)
- Timestamp (`HH:MM:SS`)
- Duration in ms (red text on error)
- Status badge ("success" / "error")
- Expand chevron

**Expanded state** (animated `height: 0 → auto`):

- `args` label + pre-formatted JSON arguments
- `result` label + result description or error message

### Header actions

Filter button (stub) and Refresh button (stub).

### Stub

All data is demo. Production: subscribe to `toolTrace:event` IPC events. The expand/collapse state is local UI state only.

---

## 11. Short Memory view

### Purpose

Facts extracted from the current conversation session by the memory extraction pipeline.

### Layout

List of fact cards. Each card:

- Zap icon in terracotta circle
- Fact text
- Source badge (chat / code / explicit / inferred)
- Timestamp
- Confidence percentage (right-aligned)
- Confidence bar (terracotta, width = `conf × 100%`)

### Header

Badge showing fact count. Refresh button (stub).

### Stub

All facts are demo data. Production: subscribe to `memory:shortTermUpdate` IPC events.

---

## 12. Long Memory view

### Purpose

Persistent knowledge about the user stored across sessions (vector database or similar).

### Layout

List of memory entry cards. Each card:

- Database icon in graphite circle
- Topic heading
- Detail text
- "Updated YYYY-MM-DD" sub-label
- Strength percentage (right-aligned)
- Strength bar (muted grey, width = `strength × 100%`)

### Header

Badge showing entry count. Search button (stub). Filter button (stub).

### Stub

All entries are demo data. Production: query `memory:getLongTermEntries` IPC on mount.

---

## 13. Logs view

### Log entry format

```
HH:MM:SS  LEVEL  source  message text
```

Monospace font, 11px. Rendered as a dense list with `hover:bg-white/2%` row highlight.

### Level colours

| Level | Colour |
|---|---|
| INFO | `#7dd3fc` (sky blue) |
| WARN | `#f59e0b` (amber) |
| ERROR | `#ef4444` (red) |

### Filter bar

Segmented buttons: ALL / INFO / WARN / ERROR. Clicking filters the visible entries (client-side). Active filter button has terracotta background.

### Header actions

Filter segmented control + Refresh button (stub).

### Stub

All log entries are demo data. Production: subscribe to `logs:entry` IPC events and append to local state. Implement virtual scrolling for large log volumes.

---

## 14. Models view

### Purpose

Displays all locally available models with their status and configuration.

### Model card layout

Each model renders as a card with:

- Model name (e.g. "Llama 3.2 7B Q4")
- Status badge: `loaded` (green), `standby` (amber), `idle` (neutral)
- Role and size on the sub-line
- Generation speed (right-aligned, shown only for `loaded` model)
- 3-column stat grid: Temperature / Context / Status
- "Load model" button for non-loaded models (stub)

**Active model card** has a terracotta-tinted border and subtle terracotta background fill.

### Models in demo

| Name | Role | Size | Status |
|---|---|---|---|
| Llama 3.2 7B Q4 | Primary | 4.7 GB | loaded |
| CodeLlama 7B Q4 | Code specialist | 3.9 GB | standby |
| Llama 3.2 13B Q4 | Long context | 8.1 GB | idle |
| Mistral 7B v0.3 | Fallback | 4.1 GB | idle |

### Stub

All data is demo. Production: call `models:getRegistry` IPC on mount. "Load model" triggers `models:load(modelName)`.

---

## 15. Runtime view

### Sections

**Resource meters** — 3-column grid:

| Metric | Demo value | Bar |
|---|---|---|
| RAM | 6.1 GB | 38% terracotta |
| VRAM | n/a | 0% |
| CPU | 24% | 24% terracotta |

**Connected services** — list rows with icon, name, address/description, status dot.

| Service | Demo address | Status dot |
|---|---|---|
| Ollama | localhost:11434 | green |
| Whisper STT | local process | green |
| faster_qwen3-tts | local · CPU | amber (degraded) |
| Silero TTS | fallback active | green |
| Tool runtime | 12 tools loaded | green |
| Memory service | 5 facts · session | green |

**Environment table** — key/value pairs (monospace values):

```
Siena      v0.9.4-beta
Python     3.12.4
llama.cpp  b4200
Ollama     0.3.12
Platform   Linux · x86_64
Build      2025-07-06
```

### Stub

All values are demo. Production: call `runtime:getStatus` IPC on mount and subscribe to `runtime:statusUpdate`.

---

## 16. Debug view

### Purpose

First-class debugging workspace for Siena v2. Positioned above Settings in the sidebar. Not a generic logs page — provides structured inspection across multiple dimensions.

### Tabs

```
Overview | Tool Calls | Delegation | Timing | Memory | Payload
```

Tabs switch with `AnimatePresence mode="wait"`, `y: 4 → 0, opacity: 0 → 1, duration: 0.12s`.

### Overview tab

- 2-column grid of health cards: Session status / Model / Tool errors / Memory / Generation speed / Delegations
- Error cards have red border + red text
- Warning banner: amber border, `AlertTriangle` icon, describes faster_qwen3-tts CPU fallback
- Error banner: red border, `X` icon, describes write_file permission denial

### Tool Calls tab

Each tool call renders as a card:
- Status dot + tool name (monospace bold) + timestamp + duration
- Pre-formatted args line
- Result line (red text on error)

### Delegation tab

One card per delegation event:
- Delegated model name + task badge + status badge
- Duration (right-aligned)
- Task description
- 3-column sub-grid: Delegated at / Transition / Result

### Timing tab

Horizontal bar chart of pipeline phases. Bar width = `phase_ms / max_ms × 100%`. Bars > 1000ms are terracotta, others are grey. Total time shown in header.

**Phases in order:**
1. Prompt build (12ms demo)
2. Context injection (8ms)
3. Memory lookup (34ms)
4. First token (412ms)
5. Full generation (27 840ms)
6. Memory extraction (88ms)
7. Response render (6ms)

Bars animate in with `width: 0 → final, duration: 0.4s easeOut`.

### Memory tab

- Extraction pipeline checklist (green check icons per step)
- List of extracted facts with confidence scores (monospace)

### Payload tab

- "last request" badge + timestamp + token count
- Dark code block (`#0f0e0c` bg) with raw JSON of the last Ollama request (model, messages array, options, stream flag)

### Stub

All data is demo. Production: all tabs must subscribe to session debug events via IPC.

---

## 17. Settings

### Navigation

Left sidebar (`192px`): list of sections. Active section: terracotta bg + terracotta text + terracotta border. Content pane: right of sidebar, `max-width: 560px`, scrollable.

Section transitions: `opacity: 0 → 1, y: 5 → 0, duration: 0.13s`.

### Sections

#### Appearance

- Theme toggle: Dark / Light / System (Dark selected; Light and System are stubs)
- Accent colour picker: 5 colour dots (sienna, slate, forest, amber, violet). Currently purely visual — selecting a different colour does not retheme the app.
- Font size slider: 12–18px range, default 14px (stub)
- Compact message layout toggle (stub)
- Show message timestamps toggle (default on, stub)

#### Model settings

- Active model selector: shows all 4 models as selectable rows with status badges (stub — no actual switching)
- Temperature slider: 0–2 range, default 0.7 (stub)
- Max tokens slider: 256–8192 range, default 2048 (stub)
- Context window slider: 1024–16384 range, default 4096 (stub)

#### Startup & preload

- Preload model on startup toggle (default on)
- Show startup loading screen toggle (default on)
- Keep model warm when idle toggle (default on)
- Launch at system login toggle (default off)
- Startup model selector: dropdown (stub)
- Idle timeout slider: 1–60 min, default 15

#### Tool permissions

- File system: Read (on) / Write (off) / Execute terminal (off) / Clipboard (on)
- Network: Web search (off) / Local services (on) / Delegate models (on)
- Memory: Short-term extraction (on) / Long-term writes (on) / Cross-session recall (on)

#### Code rendering

- Syntax highlighting (on) / Line numbers (on) / Word wrap (off) / Language badge (on)
- Code actions: Copy (on) / Collapse-expand (on) / Apply patch (on) / Save snippet (on) / Confirm before applying (on)
- Font family selector: JetBrains Mono / Fira Code / Cascadia Code (stub)
- Font size slider: 10–16px, default 13px (stub)

#### Voice

See §8 for full detail.

**New in this section vs prior version:**
- Enable streaming STT (replaces simple enable toggle)
- Show voice visualizer toggle (default on)
- Visualizer sensitivity slider (1–10, default 7)
- TTS streaming playback toggle (default on) with sub-description
- Fallback to Silero on faster_qwen3-tts failure toggle (default on)
- Live orb preview at top of section (static demo: `speaking-siena` state at amplitude 0.62)
- Voice state indicator grid showing all named states with their coloured dots

#### Language

- Interface language buttons: English (en) / Russian (ru)
- Preferred conversation language dropdown (en/ru/auto)
- Preferred input language dropdown (en/ru/auto)
- Preferred output language dropdown (en/ru/auto)
- STT recognition language dropdown (en/ru/auto)
- TTS speech language dropdown (en/ru)
- Language preset grid: English only / Russian only / Mixed EN→RU / Mixed RU→EN

#### Developer

- Electron integration: IPC bridge (on) / Native chrome (off) / DevTools shortcut (on) / Verbose logging (off)
- Local API port input: default 11434
- Auth token input: password type
- About table: Version / Build / Python / Electron target / llama.cpp / Backend

---

## 18. Placeholder and stub inventory

The following UI elements are currently hardcoded with demo data or have no wired backend behaviour. Each must be replaced before production.

### Demo data stubs

| Location | What is stubbed | Replace with |
|---|---|---|
| Chat messages | 5 hardcoded `INITIAL_MESSAGES` | Load session history from `chat:getHistory(sessionId)` |
| Session list | 5 hardcoded `SESSIONS` | Load from `sessions:getAll()` IPC |
| Tool Trace | `TOOL_EVENTS` array | Subscribe to `toolTrace:event` IPC |
| Short Memory | `SHORT_MEMORY` array | Subscribe to `memory:shortTermUpdate` IPC |
| Long Memory | `LONG_MEMORY` array | Call `memory:getLongTermEntries()` on mount |
| Logs | `LOG_ENTRIES` array | Subscribe to `logs:entry` IPC |
| Models | `MODEL_DATA` array | Call `models:getRegistry()` on mount |
| Runtime stats | RAM / VRAM / CPU values | Call `runtime:getStatus()` on mount |
| Debug timing | `TIMING_DATA` | Subscribe to `debug:timingEvent` IPC |
| Debug payload | Hardcoded JSON | Capture last real Ollama request payload |
| Inspector context | Hardcoded token count, session name | Derive from active session state |

### Voice stubs

| Stub | Replace with |
|---|---|
| `setInterval` amplitude simulation | `voice:amplitude` / `tts:amplitude` IPC events |
| Demo voice sequence (`setTimeout` chain) | Real IPC: `voice:startListening`, backend state transitions |
| Demo transcription text (Russian query) | `voice:transcriptionReady` payload |
| `startVoice` / `stopVoice` | `voice:startListening` / `voice:stopListening` IPC calls |

### Settings stubs

| Setting | Status |
|---|---|
| Theme switching (Light / System) | UI only — no actual theme application |
| Accent colour picker | UI only — no CSS variable update |
| All sliders (font size, temperature, etc.) | UI only — no persistence |
| All toggles | UI only — no persistence |
| Model "Load model" button | No backend call |
| Language preset buttons | UI only |
| Startup model dropdown | UI only |
| File picker "Project folder" option | Stub — no folder selection |

### Feedback stubs

| Button | Status |
|---|---|
| Retry | No `message:regenerate` call |
| Save to memory | No `memory:saveMessage` call |
| Like / Dislike | Local state only — no persistence |

### Search / Filter stubs

- Chat header Search and Hash buttons: no-op
- Tool Trace Filter button: no-op
- Long Memory Search and Filter buttons: no-op
- Logs Refresh button: no-op
- Models Refresh button: no-op
- Runtime Refresh button: no-op
- Debug Refresh button: no-op

---

## 19. Required frontend events (IPC → UI)

Events the Python/Electron backend must emit to the renderer process.

### Session and chat

```
chat:historyLoaded       { sessionId, messages[] }
chat:messageAppended     { message }
sessions:loaded          { sessions[] }
sessions:created         { session }
```

### Inference

```
inference:thinking       { sessionId }
inference:generating     { sessionId, tokenCount }
inference:complete       { sessionId, message }
inference:modelState     { state: "idle"|"thinking"|"generating"|"tool" }
```

### Voice / STT

```
voice:startListening     {}
voice:stopListening      {}
voice:amplitude          { level: number }        // 0–1, 22+ fps
voice:transcribing       {}
voice:transcriptionReady { text: string }
voice:error              { code: "mic-unavailable"|"permission-denied"|string }
```

### TTS

```
tts:startStreaming       { provider: "faster_qwen3-tts"|"silero" }
tts:audioChunk           { chunk: ArrayBuffer }
tts:amplitude            { level: number }        // 0–1, 22+ fps
tts:fallbackActivated    { reason: string }
tts:finished             {}
tts:error                { code: string }
```

### Tool trace

```
toolTrace:event          { id, tool, status, ms, ts, args, result }
toolTrace:cleared        {}
```

### Memory

```
memory:shortTermUpdate   { facts[] }
memory:longTermUpdate    { entries[] }
```

### Logs

```
logs:entry               { id, level, src, msg, ts }
logs:cleared             {}
```

### Models and runtime

```
models:registryLoaded    { models[] }
models:statusChanged     { modelName, status }
models:loaded            { modelName }
runtime:statusLoaded     { ram, vram, cpu, services[] }
runtime:statusUpdate     { ram, vram, cpu }
```

### Debug

```
debug:timingEvent        { phases[] }
debug:delegationEvent    { event }
debug:payloadCaptured    { payload }
debug:warningAdded       { warning }
debug:errorAdded         { error }
```

### Startup

```
backend:stepComplete     { stepIndex: 0–6 }
backend:ready            {}
```

---

## 20. Required backend events (UI → Python)

Actions the renderer sends to the Python/Electron backend.

### Chat

```
chat:send                { sessionId, text, attachments[] }
chat:newSession          {}
chat:loadSession         { sessionId }
message:regenerate       { messageId }            // stub
```

### Memory

```
memory:saveMessage       { messageId }            // stub
memory:deleteEntry       { entryId }
```

### Voice

```
voice:startListening     {}
voice:stopListening      {}
voice:setDevice          { deviceId }
voice:setLanguage        { lang }
```

### TTS

```
tts:stop                 {}
tts:setDevice            { deviceId }
tts:setVoice             { voiceId }
```

### Models

```
models:load              { modelName }
models:unload            { modelName }
models:getRegistry       {}
```

### Feedback

```
feedback:like            { messageId }
feedback:dislike         { messageId }
feedback:copy            { messageId }
```

### Settings

```
settings:save            { section, values }
settings:load            {}
```

---

## 21. Electron integration notes

### Window configuration

- Frame: `frame: false` or custom titlebar — Native chrome toggle in Developer settings controls this
- Minimum size: `minWidth: 860, minHeight: 560`
- Background colour: `#0f0e0c` (matches splash background, prevents white flash on load)
- The sidebar toggle (`☰`) must remain accessible — do not place a native drag region over it

### IPC bridge

Use `ipcRenderer.on` / `ipcRenderer.send` via a preload script. The UI expects a bridge object on `window.siena` or equivalent:

```ts
window.siena = {
  send: (channel: string, payload?: unknown) => void,
  on: (channel: string, listener: (payload: unknown) => void) => void,
  off: (channel: string, listener: (...args: unknown[]) => void) => void,
}
```

The Developer settings "Enable IPC bridge" toggle controls whether the bridge is active. When disabled (e.g. browser dev mode), the app must fall back to demo data gracefully.

### File picker

The current implementation uses `<input type="file">` with programmatic `.click()`. This works in Electron's renderer process with default security settings. If `contextIsolation: true` is required, replace with `dialog.showOpenDialog` via IPC.

### Clipboard

`navigator.clipboard` is used for paste detection and copy-to-clipboard. Requires `clipboard-read` permission in Electron's web preferences:

```js
webPreferences: {
  contextIsolation: true,
  nodeIntegration: false,
  // Note: clipboard access is granted by default in Electron renderer
}
```

### Voice and audio

Microphone access requires `navigator.mediaDevices.getUserMedia`. Electron must be launched with the appropriate Chromium flags or the user must grant mic permission via the OS prompt. Handle the `NotAllowedError` by emitting `voice:error({ code: "permission-denied" })` which sets `voiceState = "error-mic"`.

### DevTools shortcut

The "DevTools shortcut (⌘⌥I)" toggle in Developer settings should wire to `webContents.openDevTools()` / `closeDevTools()` via IPC when enabled.

### Fonts

Plus Jakarta Sans and JetBrains Mono are loaded from Google Fonts. For offline/air-gapped deployments, bundle the font files locally and update `src/styles/fonts.css` to use local `@font-face` declarations instead of the Google Fonts `@import`.

---

## 22. Implementation checklist

### Phase 1 — Electron shell

- [ ] Configure `BrowserWindow` with correct min size and background colour
- [ ] Implement preload script exposing `window.siena` IPC bridge
- [ ] Set up `contextIsolation: true` + CSP headers
- [ ] Wire native window chrome toggle to `frame` option or custom titlebar
- [ ] Confirm `<input type="file">` works, or replace with `dialog.showOpenDialog`
- [ ] Verify Google Fonts load; add local font fallback for offline use

### Phase 2 — Backend connection

- [ ] Implement `backend:stepComplete` events during Python initialisation
- [ ] Implement `backend:ready` event to trigger splash → main transition
- [ ] Connect `inference:*` events to Ollama streaming responses
- [ ] Implement `inference:modelState` updates so sidebar model widget is live

### Phase 3 — Chat

- [ ] Replace `INITIAL_MESSAGES` with `chat:getHistory` IPC call
- [ ] Replace `SESSIONS` with `sessions:getAll` IPC call
- [ ] Wire `chat:send` IPC to Python inference pipeline
- [ ] Wire `message:regenerate` for Retry button
- [ ] Wire `memory:saveMessage` for Save button
- [ ] Persist like/dislike feedback via `feedback:like` / `feedback:dislike`

### Phase 4 — Attachments

- [ ] Wire file reads: when a code/document attachment is sent, read file contents and include in context
- [ ] Wire image attachments to multimodal model input
- [ ] Implement attachment metadata extraction on the Python side
- [ ] Store pasted text/code attachments in session context

### Phase 5 — Voice

- [ ] Implement `voice:startListening` → Whisper STT pipeline
- [ ] Emit `voice:amplitude` events at ≥22fps from VAD
- [ ] Emit `voice:transcribing` when speech ends, `voice:transcriptionReady` with text
- [ ] Implement `tts:startStreaming` → faster_qwen3-tts pipeline
- [ ] Emit `tts:audioChunk` for streaming playback
- [ ] Emit `tts:amplitude` events at ≥22fps from TTS playback level
- [ ] Implement automatic fallback to Silero and emit `tts:fallbackActivated`
- [ ] Emit `tts:finished` when playback ends
- [ ] Wire microphone permission errors to `voice:error` → `error-mic` state
- [ ] Wire TTS failure to `voice:error` → `error-tts` state
- [ ] Replace `setInterval` amplitude simulation with real event listeners
- [ ] Replace demo `setTimeout` voice sequence with real state machine

### Phase 6 — Tool Trace and Debug

- [ ] Emit `toolTrace:event` for every tool call from the Python side
- [ ] Wire Tool Trace view to live events (replace `TOOL_EVENTS` demo data)
- [ ] Emit `debug:timingEvent` per request with real phase timings
- [ ] Emit `debug:delegationEvent` for model delegation events
- [ ] Capture and emit `debug:payloadCaptured` for the last Ollama request
- [ ] Wire Debug Overview health cards to live status

### Phase 7 — Memory and Logs

- [ ] Emit `memory:shortTermUpdate` after each extraction cycle
- [ ] Emit `memory:longTermUpdate` after long-term writes
- [ ] Emit `logs:entry` for all Python log output routed through IPC
- [ ] Implement log level filtering on the renderer side (already built)

### Phase 8 — Models and Runtime

- [ ] Call `models:getRegistry` on Models view mount
- [ ] Wire "Load model" button to `models:load` IPC + spinner state
- [ ] Emit `models:statusChanged` as models load/unload
- [ ] Call `runtime:getStatus` on Runtime view mount
- [ ] Emit `runtime:statusUpdate` periodically (suggested: every 5s)
- [ ] Wire Silero fallback status to Runtime services list

### Phase 9 — Settings persistence

- [ ] Implement `settings:save` and `settings:load` IPC via Electron store or JSON file
- [ ] Apply saved theme preference on startup
- [ ] Apply saved font size to CSS variable
- [ ] Apply saved model settings to inference defaults
- [ ] Apply saved voice settings to STT/TTS pipeline on startup
- [ ] Apply saved tool permissions to Python tool registry

### Phase 10 — Polish and QA

- [ ] Verify all `AnimatePresence` transitions work correctly at 60fps in Electron
- [ ] Test composer auto-resize across all attachment + voice combinations
- [ ] Verify SVG voice orb renders correctly in Chrome (Electron's renderer)
- [ ] Confirm `transformOrigin` on SVG `motion.g` works in production build
- [ ] Test clipboard paste (image and large text) in Electron context
- [ ] Verify font loading: Plus Jakarta Sans + JetBrains Mono present before first paint
- [ ] Test with sidebar closed (width = 0) — ensure main content fills correctly
- [ ] Test minimum window size (860×560) — ensure no layout breakage
- [ ] QA voice state transitions end-to-end with real Whisper + TTS
- [ ] QA Silero fallback path specifically (GPU-absent machine)
- [ ] Verify Debug is above Settings in sidebar on all builds

---

*Document generated from the Siena v2 React UI codebase — `src/app/App.tsx`. All component behaviour described here reflects the current implementation. Cross-reference the source file for exact class names, animation parameters, and data structures.*
