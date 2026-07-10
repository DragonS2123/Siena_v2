# Siena v2 — Developer Handoff (v2, actual contract)

**Status:** living document, describes the system as it actually runs today.
**Supersedes:** `HANDOFF.md` (kept as a historical record of the original
Electron/IPC mockup spec — do not use it as a contract anymore, see
`NEXTDO.md` for the audit that established this split).

This document exists because the real implementation moved substantially
past the original `HANDOFF.md` (REST+WS instead of Electron IPC, several new
subsystems, and a different, honest set of "not connected yet" gaps). Where
this document and `HANDOFF.md` disagree, **this document is correct**.

---

## 1. Actual architecture

```
┌─────────────────────────────┐        HTTP + WebSocket        ┌──────────────────────────┐
│ Electron shell (window only) │ ─────────────────────────────▶ │ FastAPI backend           │
│  └─ React/Vite renderer      │ ◀───────────────────────────── │ (api/server.py, uvicorn)  │
└─────────────────────────────┘                                 └──────────────────────────┘
```

- **Frontend:** React + Vite, rendered inside a plain Electron `BrowserWindow`
  (`Siena v2 Control Panel UI/electron/main.cjs`, 52 lines) — or directly in a
  browser at the Vite dev server, since nothing in the renderer depends on
  Electron APIs. There is **no preload script, no `contextIsolation`
  configuration, no `window.siena` IPC bridge** anywhere in the codebase.
  Electron is a window shell, nothing more.
- **All app data** (chat, memory, models, settings, trace, logs, insights,
  voice) flows over plain `fetch()` calls to `http://127.0.0.1:8000` from
  `src/api/sienaClient.ts`, plus one WebSocket (`ws://127.0.0.1:8000/ws/trace`)
  for live tool-trace events. This is a deliberate simplification, not a
  temporary stub — do not introduce an Electron IPC bridge unless a concrete
  need for a native-only capability (file dialogs, system tray, etc.) actually
  comes up.
- **Backend:** Python 3.12, FastAPI (`api/server.py`), Ollama for all local
  models, SQLite for memory stores, JSONL for logs/trace.
- Polling/streaming is centralized: `RuntimeStatusProvider` (single 5s
  `setInterval` for `/api/runtime/status`) and `TraceSocketProvider` (single
  `GET /api/trace/recent` + single `/ws/trace` connection with reconnect) wrap
  the whole app once in `App()`; no screen opens its own poll or socket.

---

## 2. How to run

**Backend:**
```
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```
Or, portably (0.2.0 — resolves the venv automatically, no hardcoded path):
```
scripts\start_backend.ps1
```
`start_backend.bat` at the repo root also works — it delegates to the
`.ps1` script when PowerShell is available, with a graceful inline
fallback (`.venv-faster-qwen3-tts` → `py` → `python`) if not. Neither uses
`--reload` by default.

> **Do not use `--reload` for normal use.** It's convenient while actively
> editing backend code, but reloads/restarts worker state (in-memory trace
> hub, spawned `tts-server.exe` subprocess handle, active-chat-model
> override) whenever a watched file changes — including files touched by
> unrelated tooling. Pass `-Reload` to `scripts\start_backend.ps1`, or add
> `--reload` to the raw uvicorn command, only when you're actively
> developing the backend itself.

**WebSocket dependency note:** `/ws/trace` requires uvicorn to have an actual
ASGI websocket implementation available. `requirements.txt` pins
`uvicorn[standard]>=0.30.0`, which pulls in `websockets` (and `wsproto` is an
acceptable alternative backend) automatically — if you ever see the frontend
silently failing to connect to `/ws/trace` (falls back to polling
`/api/trace/recent` only), the first thing to check is whether uvicorn was
installed as plain `uvicorn` instead of `uvicorn[standard]`.

**UI (desktop):**
```
cd "Siena v2 Control Panel UI"
npm run desktop          # vite build && electron .
```
Other scripts: `npm run dev` (Vite dev server only, browser at
`http://127.0.0.1:5173`), `npm run desktop:dev` (Electron pointed at the dev
server, for hot reload while iterating on UI code), `npm run build` (typecheck
+ production bundle, no Electron).

---

## 3. Backend endpoints (actual, as of this document)

All under `http://127.0.0.1:8000`, plus one WebSocket.

| Endpoint | Purpose |
|---|---|
| `GET /api/runtime/status` | Model config, Ollama connectivity, registered tools, active chat model — polled every 5s by `RuntimeStatusProvider` |
| `GET /api/models` | Model registry (role, routing_mode, install status) |
| `GET/POST /api/models/active` | Manual active-chat-model switch (restricted to `qwen3.5:9b`/`qwen3.5:27b`, runtime-only, not persisted) |
| `GET/POST /api/settings` | Live-mutates backend process settings (primary/code model, ctx, timeouts, log level). The 7 fields listed in §5 below are now **persisted to `storage/settings.json`** and reloaded on startup; everything else accepted by this endpoint (`ollama_host`, `max_iterations`, `delegate_timeout_seconds`) is still runtime-only and resets on restart |
| `GET/POST /api/conversations`, `GET /api/conversations/{id}`, `POST /api/conversations/{id}/activate` | Conversation CRUD + session switching |
| `GET/POST /api/session/new`, `GET /api/session/current` | Session bootstrap |
| `POST /api/chat` | Main chat turn — text + attachments in, `ChatResponse` (answer, ocr_results, vision_results, model routing info) out |
| `POST /api/chat` lifecycle update | Also accepts `conversation_id` and targets that conversation directly. If `chat_lock` is active, returns `409` instead of queueing another generation |
| `GET /api/attachments/{attachment_id}` | Uploaded attachment metadata lookup |
| `GET /api/attachments/{attachment_id}/content` | Uploaded attachment bytes, served only from internal attachment storage |
| `GET /api/game/nucleares/status` | Read-only Nucleares game-simulation telemetry bridge. Reads the local Nucleares webserver over HTTP only; no game writes or commands |
| `GET /api/trace/recent` | Last N trace events (JSONL-backed) |
| `WS /ws/trace` | Live trace event stream (same event shape as `/api/trace/recent`) |
| `POST /api/trace/client-event` | Lets the frontend log purely client-side events (attachment add/remove, etc.) into the same trace stream |
| `GET/DELETE /api/memory/short` | Short-term (session) memory facts |
| `GET /api/memory/long` | Long-term memory, with keyword+fuzzy search (`memory/search.py`) |
| `POST /api/memory/long` | Feedback row "Save-to-memory" — explicit human-confirmed save, `{text, source, conversation_id, message_id}`, same `LongMemoryStore.save()` as the `long_memory_save` tool |
| `GET /api/insights` | Candidate-memory review queue (`status=pending\|later\|rejected\|promoted`, default `pending`) |
| `POST /api/insights/{id}/promote` | Human approves a candidate → writes it into long-term memory |
| `POST /api/insights/{id}/reject` | Human rejects (soft — status flips to `rejected`, row kept) |
| `POST /api/insights/{id}/later` | Human defers (status flips to `later`) |
| `DELETE /api/insights/{id}` | Hard delete of a candidate row, any status |
| `GET /api/voice/status` | `tts_provider`, `tts_available`, `tts_fallback_provider`, plus `stt_provider`/`stt_available`/`stt_reason`/`stt_model`/`stt_backend_hint` — the latter honestly describe the whisper.cpp path (see §5), not the dormant faster-whisper one. `stt_available` is exactly what the Composer's real mic button polls to decide whether it's enabled |
| `POST /api/voice/tts/start` \| `/stop` \| `/test` | Explicit control of the `qwen3_tts_ggml_vulkan` provider's `tts-server.exe` subprocess — bypasses the silent Silero fallback so a broken server is never hidden |
| `POST /api/voice/tts/stream` | Experimental raw PCM streaming proxy (qwen3_tts_ggml_vulkan-only, no fallback) — now wired to the experimental "Stream" button in the feedback row (see §5) |
| `POST /api/voice/stt/transcribe` | STT via whisper.cpp — `multipart/form-data` (`file`, optional `language`). **Real**, called from two places: the Composer's mic button (Phase 2 push-to-talk — text lands in the composer input, never auto-sent) and the experimental Voice Conversation Mode (Phase 3 — auto-sent through the normal `/api/chat` flow; see §5 for both) |
| `GET /api/voice/profiles`, `/api/voice/profiles/active` | Qwen3-TTS voice/speaker profile management |
| `POST /api/translate` | Standalone translation (translategemma:4b), never called automatically by chat |
| `GET /api/logs/recent` | Recent log entries for the Logs view |

Contract source of truth is always `api/server.py` — this table is a
snapshot, keep it in sync manually when routes change (there is no schema
generation).

---

## 4. UI sections (actual)

Sidebar, top to bottom: **Chat · Tool Trace · Short Memory · Long Memory ·
Insights · Logs · Models · Runtime** — then, below a divider, **Debug ·
Settings**.

`Insights` is new versus the original `HANDOFF.md` nav list — it did not exist
in the original mockup at all.

---

## 5. What is actually connected

- **Chat attachment persistence / in-flight lifecycle (Pass 1 + Pass 1B,
  smoke-confirmed)**:
  - Uploaded attachments are copied into internal storage under
    `storage/attachments/uploaded/<conversation_id>/<attachment_id>.<ext>`.
    `storage/attachments/generated/` is reserved for future generated-image
    work, but is not used yet.
  - Attachment metadata lives in the SQLite `conversation_attachments` table.
    Conversation history returns that metadata, and the frontend restores
    old attachment chips without needing the original local `File` object or
    original source path.
  - Attachment routes:
    `GET /api/attachments/{attachment_id}` returns metadata, and
    `GET /api/attachments/{attachment_id}/content` returns the stored bytes
    from internal attachment storage.
  - `/api/chat` accepts `conversation_id` and targets that conversation
    directly. The user turn is saved immediately with
    `metadata.status="processing"` before OCR/Vision/model work.
  - OCR/Vision summaries are persisted into attachment metadata so image
    chips survive conversation switch/reload with their OCR/Vision status
    and previews. A completed user turn stores `assistant_message_id`; a
    failed user turn stores `metadata.status="failed"` and `metadata.error`.
  - Frontend send and Retry pass `conversation_id`. If a response belongs to
    a conversation that is no longer active, the frontend refuses to mutate
    the visible message list; the persisted result appears when that
    conversation is reloaded/reactivated.
  - Conversation reload restores attachments, OCR/Vision chip metadata, and
    `processing`/`completed`/`failed` state.
  - Global single-generation policy: if `chat_lock` is active, `/api/chat`
    returns `409` ("chat generation already in progress"). No queue exists
    yet.
  - Manual Electron smoke confirmed that switching chats during
    OCR/Vision/model generation no longer loses attachments or appends the
    result to the wrong chat.

- **Nucleares Game Bridge Phase 1 (read-only backend endpoint,
  smoke-confirmed)**:
  - New endpoint: `GET /api/game/nucleares/status`.
  - This is for **Nucleares game simulation telemetry only**, not
    real-world nuclear operation. Siena does **not** control Nucleares in
    this phase.
  - The bridge reads the local Nucleares webserver via HTTP only. Default
    base URL is `http://localhost:8785`. On this machine Nucleares binds to
    IPv6 localhost (`::1`), so `127.0.0.1` may fail even when
    `localhost` works.
  - Host/port fallback order: `localhost`, `[::1]`, `127.0.0.1`; ports
    `8785`, `8786`, `8787`, `8080`, `8000`.
  - Discovery parses root HTML links/text matching `?variable=NAME`, filters
    the placeholder key `VARNAME`, then reads selected variables with
    read-only `GET /?variable=NAME`. It never writes values to the game and
    exposes no game commands.
  - Normalized telemetry includes whichever of these keys are available:
    `ambient_temperature`, `alarms_active`, `ao_agent_status`,
    `ao_agent_diagnostics_json`, `condenser_temperature`,
    `condenser_pressure`, `condenser_circulation_pump_active`,
    `condenser_circulation_pump_speed`, `core_pressure`,
    `pressurizer_pressure`, and `pressurizer_temperature`.
  - Trace events: `nucleares_status_requested`,
    `nucleares_status_connected`, `nucleares_status_completed`,
    `nucleares_status_failed`.
  - Verification: `py_compile` passed; `pytest` passed
    (`143 passed, 1 warning`). Manual smoke after backend restart:
    `Invoke-RestMethod http://127.0.0.1:8000/api/game/nucleares/status`
    returned `connected=true`.
  - Phase 1 known limitations at the time: backend endpoint only, no frontend
    panel yet; no commands/writes to the game; no async polling daemon.
  - **Phase 2: backend-only on-demand chat context injection
    (smoke-confirmed)**:
    - `/api/chat` detects explicit Nucleares/station/reactor telemetry
      questions and calls the existing read-only `nucleares_client.status()`
      service directly. It does not call Siena's own HTTP endpoint from the
      backend.
    - When triggered, `/api/chat` injects a compact
      `[NUCLEARES_GAME_CONTEXT]...[/NUCLEARES_GAME_CONTEXT]` block into the
      model-visible prompt only. The injected context is **not persisted**
      into the user message text.
    - It does not write to Nucleares, does not control the game, does not
      poll continuously, and does not inject telemetry for unrelated chat.
    - Trace events for the successful manual smoke were:
      `nucleares_context_injection_requested`,
      `nucleares_context_injected`, `model_response`, `final_answer`.
    - Manual smoke after backend restart: user asked
      `"Сиена, что сейчас со станцией в Nucleares?"`; Siena answered using
      live Nucleares telemetry, including `operation_mode SHUTDOWN`,
      core/reactor temperature around `18.7°C`, ambient temperature around
      `22°C`, low/minimal pressures, inactive condenser circulation pump
      (`speed 0`), and no active alarms/diagnostic faults visible. TTS then
      synthesized the answer successfully.
    - An earlier attempt showed `nucleares_context_unavailable` / not
      reachable, but a later successful run confirmed the bridge works. If
      this repeats often, investigate transient reachability/timing between
      the backend and the Nucleares webserver.
    - Validation: `py_compile` passed; `pytest` passed
      (`151 passed, 1 warning`); manual chat smoke passed after backend
      restart.
    - Current known limitations: no frontend Nucleares panel yet; no game
      controls/writes; no async polling daemon; no automatic injection for
      unrelated chat. This remains only Nucleares game simulation telemetry,
      not real-world nuclear operation guidance.

- **Chat** — real `/api/chat`, real conversation history/switching, markdown +
  fenced-code-block rendering with newline preservation.
- **Conversations** (session list, new/switch) — real.
- **Tool Trace** — live via `TraceSocketProvider` (`/ws/trace` when the
  websocket backend is available, `/api/trace/recent` as the baseline/replay
  source either way). Renders structured tool-result summaries, never
  `[object Object]`.
- **Short Memory / Long Memory** — real, including long-memory keyword+fuzzy
  search (`memory/search.py`).
- **Insights (candidate memory)** — real end to end: `candidate_memory_create`
  (model-only, cannot resolve its own candidates) → `/api/insights*` →
  Promote/Later/Reject/Delete buttons in the UI, all hitting the backend.
- **Logs** — real, client-side level filter.
- **Models** — real registry + working "Set as active chat model" action
  (restricted to the two allowed manual models).
- **Qwen3-TTS Vulkan backend** — `voice/qwen_tts_ggml_vulkan.py` +
  `/api/voice/tts/*` endpoints are real and tested
  (`scripts/test_qwen_ggml_vulkan.py`, PASS as of last run: 24kHz mono WAV,
  RTF ≈ 0.15). `/api/voice/tts/start|stop|test` are debug-only endpoints
  (bypass the Silero fallback on purpose) — still not reachable from the UI.
- **TTS playback — "Speak" (assistant messages only)** — real, via
  `POST /api/voice/synthesize` (`VoiceService.synthesize`, primary
  `qwen3_tts_ggml_vulkan` with automatic Silero fallback) +
  `GET /api/voice/audio/{filename}`, played through a plain
  `HTMLAudioElement` (`src/hooks/useSpeech.ts`). Per-message Speak/Stop
  button in the feedback row shows idle/preparing/speaking/error; an amber
  note appears if Silero fallback actually spoke (never silently hidden).
  `Stop` only pauses local playback — it does not call
  `/api/voice/tts/stop`, so a running `tts-server.exe` stays warm for the
  next Speak click. Verified live end-to-end (Electron + raw CDP, no mocked
  states) — see §8 for the one open Phase-2 item.
  **Lifecycle (stabilized, live-verified with a real backend-down/synthesize-failure
  run):**
  - `useSpeech.ts` enforces at most one active playback (and at most one
    in-flight `synthesize` request) at a time. Every `speak()` call starts
    by tearing down whatever came before it — no overlap is possible.
  - Clicking Speak on a new message aborts the previous in-flight
    `/api/voice/synthesize` request (`AbortController`) and stops/detaches
    the previous `<audio>` element, even if the previous call was still in
    the "preparing" (pre-audio) phase — verified live: switching Speak
    mid-preparation instantly reverts the first message's button to idle.
  - Stop pauses the audio, resets `currentTime`, and aborts a pending
    synthesize request if one is still in flight — it never calls
    `/api/voice/tts/stop`, so `tts-server.exe` is left warm.
  - Switching the active conversation (or starting a new chat) makes
    `ChatView` stop playback and reset the active-speech message id —
    verified live: Speak mid-playback, then switch conversations, audio
    stops immediately.
  - `preparing`/`speaking`/`error` are tied to the specific assistant
    message id that triggered them (`speech.activeMessageId`); every other
    message's button always renders as idle.
  - A backend-down or failed `/api/voice/synthesize` call surfaces
    `error` + the real fetch error text on that exact message's button
    ("Speak failed" + reason) without affecting the rest of the chat — a
    real bug here (the error path was clearing `activeMessageId`, so the
    error silently never rendered on any button) was found by this live
    test and fixed.
- **Auto-speak** — `autoSpeak` toggle in the Chat header (speaker icon),
  frontend-only local state, **off by default**. When on, the newly
  arrived assistant reply is spoken automatically exactly once — guarded by
  `lastAutoSpokenMessageIdRef` so the same message id is never re-triggered
  by a re-render/poll. Turning `autoSpeak` off does **not** stop audio
  already playing (a deliberate choice, not an oversight — nothing else in
  this pass interrupts in-progress playback just because a toggle changed).
  The per-session toggle itself still isn't persisted (flipping it mid-session
  doesn't write anywhere). Its **default** on a fresh `ChatView` mount,
  however, now reads from `localStorage` (Settings unfreeze pass, §5) — set
  via the Voice section's "Auto-speak new assistant replies by default"
  control, a frontend-only preference since the backend has no concept of
  auto-speak at all.
- **`POST /api/voice/tts/stream` (experimental, backend)** — real. Proxies
  raw PCM chunks straight from `tts-server.exe`'s `response_format=pcm`
  (`voice/qwen_tts_ggml_vulkan.py`'s `stream_pcm()`), qwen3_tts_ggml_vulkan-only
  with **no Silero fallback** — an inactive/wrong provider gets an honest
  `501`, never a fake stream. Response is `application/octet-stream` (raw
  s16le/24kHz/mono, no WAV header) with diagnostic headers
  `X-Siena-TTS-Provider`, `X-Siena-TTS-Format`, `X-Siena-TTS-Sample-Rate`,
  `X-Siena-TTS-Channels`. Nothing is written to disk. Trace events:
  `tts_stream_requested` → `tts_stream_server_ready` → `tts_stream_started`
  → `tts_stream_first_chunk` → `tts_stream_completed` (or
  `tts_stream_failed` / `tts_stream_client_disconnected`). Built on top of
  `scripts/probe_qwen_tts_streaming.py`'s finding that raw pcm+stream
  survived 36 direct requests against `tts-server.exe`; live-verified again
  at the Siena-backend level via `scripts/test_qwen_tts_stream_endpoint.py`
  — short/medium Russian text, 3 back-to-back repeats, empty-text 400, full
  trace-event ordering — all PASS, tts-server stayed up throughout. Does
  not touch `/api/voice/synthesize`, `useSpeech.ts`, or the WAV-per-request
  path in any way.
  - **Known limitation, confirmed by live testing (not just theorized):**
    an ASGI-level disconnect watcher (`await request.is_disconnected()`,
    polled from a background task) was tried in `api/server.py` and
    confirmed to **not** detect an aborted client connection — neither a
    raw TCP socket close nor a real Chromium `fetch()` +
    `AbortController.abort()` ever flipped it to `True` while the backend
    generator was blocked reading from `tts-server.exe`. Starlette runs the
    plain sync generator behind `StreamingResponse` in a worker thread with
    no way to interrupt a blocking call already in progress. The watcher
    was removed again rather than left in as non-functional complexity.
    `tts_stream_client_disconnected` is now a **frontend-reported** event
    (`useStreamingSpeech.ts` calls `POST /api/trace/client-event` the
    instant it aborts) — reliable for trace visibility, but it cannot (and
    does not try to) stop the backend's already-in-flight upstream
    generation, which keeps running until `tts-server.exe` finishes that
    utterance on its own and logs `tts_stream_completed`, even though
    nobody is listening anymore.
- **Streaming TTS playback (UI) — "Stream" button, experimental** — real.
  `FeedbackRow` has a second, clearly-marked-experimental button (`Waves`
  icon + amber "exp" badge) next to the stable "Speak" button, for
  assistant messages. Wired through a new `src/hooks/useStreamingSpeech.ts`
  (mirrors `useSpeech.ts`'s lifecycle-safety discipline: one active
  stream/AudioContext at a time, `AbortController`-based cancellation, full
  teardown on every new `streamSpeak()`/`stop()`/unmount) calling the new
  `sienaClient.streamSpeech()` (`POST /api/voice/tts/stream` with the
  `AbortSignal`, reading `X-Siena-TTS-Sample-Rate`/`-Channels`/`-Format`
  headers with `24000`/`1`/`"pcm"` fallback defaults if a header is ever
  missing).
  - **PCM playback**: each incoming chunk is decoded (`DataView`-based
    s16le → Float32, not an `Int16Array` view, to avoid alignment
    assumptions about network chunk boundaries) into its own small
    `AudioBuffer` and scheduled back-to-back on a running
    `AudioBufferSourceNode.start(when)` cursor — real incremental Web Audio
    API playback (audio starts as soon as the first chunk is scheduled, not
    after the whole response finishes), without ScriptProcessorNode
    (deprecated) or a full AudioWorklet module (would need a separate
    bundled worklet file + extra Vite wiring, not justified for an
    experimental/dev-only path yet).
  - **States**: idle/preparing/streaming/stopping/error, exposed alongside
    `activeMessageId`, `error`, and live `diagnostics`
    (`firstChunkMs`/`totalBytes`/`estimatedDurationSec`/`chunkCount`) shown
    inline under the button while streaming.
  - **Mutual exclusion (safe variant, chosen deliberately)**: the stable WAV
    Speak and the experimental Stream Speak are never allowed to run at the
    same time in either direction — clicking either one first fully stops
    whatever the other was doing (`speech.stop()`/`streaming.stop()`),
    never disables a button or lets two audio pipelines run together. Also
    applies to auto-speak (stops any active stream before speaking a new
    reply) and to conversation switching (both are stopped in `ChatView`'s
    existing conversation-switch effect).
  - **Stop**: aborts the in-flight `fetch`, stops/closes the `AudioContext`
    immediately (verified live: mid-stream Stop reverts the UI to idle
    right away), and reports `tts_stream_client_disconnected` itself (see
    the known-limitation note above for why the backend can't reliably
    detect this on its own). Never calls anything that would trigger a
    Silero fallback — an unavailable/failed stream is always an honest
    error on the specific message, chat stays alive.
  - **Live-verified end to end** (Electron + real CDP, not mocked): sent a
    message, confirmed stable WAV Speak still reaches
    preparing→speaking→idle unaffected; clicked Stream Speak, watched it
    reach `streaming` with real growing diagnostics
    (`first chunk ~1.1s · nnn KB · ~n.ns` estimated duration); clicked Stop
    mid-stream, audio stopped and button reverted to idle immediately;
    switching to WAV Speak on the same message mid-stream correctly stopped
    the stream and started WAV instead; killed the backend and clicked
    Stream Speak — honest "Stream failed: Failed to fetch" rendered on that
    exact message, chat stayed fully usable; confirmed mic/STT stayed
    hard-disabled throughout (untouched, see §7); confirmed full
    `tts_stream_requested → tts_stream_server_ready → tts_stream_started →
    tts_stream_first_chunk → tts_stream_client_disconnected` trace chain
    after a mid-stream Stop.
  - **A real bug was found and fixed during this live testing**: an earlier
    version of `useStreamingSpeech.ts` depended on `status`/`activeMessageId`
    state directly inside the disconnect-reporting callback, which gave
    `stop()` a new identity on every status change; since `streaming.stop`
    sits in `ChatView`'s conversation-switch effect's dependency array,
    that identity churn re-ran the effect immediately after
    `streamSpeak()` called `setStatus("preparing")`, which called `stop()`
    on the stream that had *just* started — aborting it before a single
    chunk ever rendered. Fixed by mirroring status/activeMessageId into
    plain refs so `stop`/`streamSpeak`/`reportDisconnectIfActive` all keep
    stable identities across renders (matching `useSpeech.ts`'s existing
    pattern).
- **STT via whisper.cpp — backend (Phase 1) + real mic recording UI (Phase 2)**
  — **mic/STT is no longer frozen.** The composer's mic button is real: it
  records from the actual microphone, sends the audio to
  `POST /api/voice/stt/transcribe`, and inserts the recognized text into the
  composer input. See §7 for what this changes about the old "frozen"
  guidance (short version: it no longer applies to mic/STT).
  - **Backend (Phase 1)**: a brand-new, standalone STT path via
    `voice/whisper_cpp_stt.py` (`WhisperCppSTTProvider`) — completely
    separate from `voice/stt.py`'s `WhisperSTTProvider` (faster-whisper),
    which is untouched and still dormant (the `faster_whisper` package isn't
    installed). Built from
    [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) commit
    `6fc7c33b4c3a2cec83e4b65abd5e96a890480375` with Vulkan support
    (`external/whisper.cpp/build/bin/Release/whisper-cli.exe`), model
    `external/whisper.cpp/models/ggml-base.bin` (multilingual "base", 147.37 MB)
    — full build/probe log in `storage/stt_probe/whisper_cpp_build_probe.txt`.
  - **AMD/Vulkan limitation (confirmed via live crash reproduction, not
    just theorized)**: on this machine/build, `whisper-cli.exe` on the
    Vulkan backend with its *default* decode settings (beam-size 5,
    best-of 5) segfaults 100% of the time — reproduced on two different
    audio files/languages, repeatedly. Forcing **greedy decode**
    (`-bs 1 -bo 1`, `config.WHISPER_CPP_BEAM_SIZE`/`WHISPER_CPP_BEST_OF`)
    avoids the crash entirely and is fast (~230–540 ms including model
    load), so the service always forces greedy decode — do not raise
    those values above 1 without re-testing against a real crash
    reproduction first.
  - **CPU fallback**: if the Vulkan call exits non-zero,
    `WhisperCppSTTProvider` automatically retries once with `-ng` (CPU),
    still at greedy decode, and reports `backend: "cpu_fallback"` in the
    response so callers know which path actually produced the text — same
    "never silently hide which provider spoke" discipline as the TTS
    Silero fallback.
  - Accepts `.wav` only (`multipart/form-data`: `file`, optional
    `language`, default `config.WHISPER_CPP_LANGUAGE = "ru"`) — no
    ffmpeg/webm/opus conversion implemented, a non-`.wav` upload is an
    honest `400`. Real audio duration is enforced via `wave.open()`
    (`config.WHISPER_CPP_MAX_AUDIO_SECONDS = 60`), not just upload byte
    size. `GET /api/voice/status`'s `stt_provider`/`stt_available`/
    `stt_reason`/`stt_model`/`stt_backend_hint` fields honestly describe
    this provider instead of the dormant faster-whisper one.
  - Trace events (backend): `stt_transcribe_requested` →
    `stt_transcribe_started` → `stt_transcribe_completed` (or
    `stt_transcribe_failed`), plus
    `stt_cpu_fallback_started`/`_completed`/`_failed` when the Vulkan→CPU
    retry path is exercised.
  - Backend live-verified via `scripts/test_whisper_cpp_stt_endpoint.py`:
    `external/whisper.cpp/samples/jfk.wav` (real human speech, English)
    transcribed correctly ("And so my fellow Americans, ask not what your
    country can do for you, ask what you can do for your country.") in
    321 ms on the Vulkan backend; empty-file and non-`.wav` uploads
    correctly `400`; full `stt_transcribe_*` trace chain confirmed. Full
    report in `storage/stt_probe/api_stt_endpoint_report.json`.
  - **Frontend mic recording (Phase 2)**: `src/hooks/useVoiceRecorder.ts` —
    `getUserMedia({ audio: true })` → Web Audio API (`AudioContext` +
    `ScriptProcessorNode`, deliberately **not** `MediaRecorder`/webm/opus,
    since the backend only accepts `.wav` and has no ffmpeg conversion
    step) → downmix to mono → resample to 16kHz (whisper.cpp's native
    `WHISPER_SAMPLE_RATE`) → hand-rolled WAV PCM16 header →
    `sienaClient.transcribeSpeech()` → `POST /api/voice/stt/transcribe`.
    `ScriptProcessorNode` is deprecated but used anyway, for the same
    reason `useStreamingSpeech.ts` avoids `AudioWorklet` for TTS playback —
    a full worklet module isn't worth the extra bundling/Vite wiring here.
    Max recording length 60s (auto-stops and transcribes at the limit,
    mirroring `config.WHISPER_CPP_MAX_AUDIO_SECONDS`); minimum ~0.2s or an
    honest "Recording too short" error instead of sending a near-empty clip.
  - `GET /api/voice/status`'s `stt_available` is polled by
    `src/hooks/useVoiceStatus.ts` and is the **only** thing that enables the
    Composer's mic button; if the status request itself fails, the button
    stays disabled with that error as the tooltip reason (never assumed
    available). The old "Voice is temporarily disabled on AMD/CUDA
    migration" tooltip is gone.
  - Recognized text is inserted into the composer's textarea — if it was
    empty the transcript replaces it, if the user had already typed
    something the transcript is appended after a space. **Never
    auto-sent** — the human still has to press Enter/Send.
  - The Composer's Voice Orb / voice panel (SVG orb + amplitude animation)
    is **no longer a demo** — it now reflects the real recorder state
    (`requesting-permission` → `listening` → `transcribing`, or
    `error-mic` on failure), including an elapsed-time readout while
    recording. It is still not reused by TTS Speak/Stream (see below) —
    those keep their own small inline indicator in the feedback row.
  - Starting a recording always calls `speech.stop()`/`streaming.stop()`
    first, so Siena's own TTS voice can never play back into the
    microphone mid-recording. Switching conversations while recording
    cancels it (stops mic tracks/closes the `AudioContext`) rather than
    leaving it running against a composer that's no longer visible.
  - **Electron mic permission**: `electron/main.cjs` now registers an
    explicit `session.setPermissionRequestHandler` that approves only
    audio-only `media` requests (no camera/video) and denies everything
    else — replacing Electron's implicit "allow every permission request"
    default with a narrower, explicit one.
  - Trace events (frontend, client-reported via
    `POST /api/trace/client-event`): `stt_ui_recording_requested` →
    `stt_ui_permission_granted`/`stt_ui_permission_denied` →
    `stt_ui_recording_started` → `stt_ui_recording_stopped` →
    `stt_ui_transcribe_started` → `stt_ui_transcribe_completed`/
    `stt_ui_transcribe_failed`, plus `stt_ui_cancelled` for an explicit
    Stop/Dismiss during permission/transcribing.
  - **Verification honesty**: `npm run build` and `pytest tests/ -q` (57
    passed) were run after implementation, and the backend endpoint was
    live-verified with real audio (`scripts/test_whisper_cpp_stt_endpoint.py`,
    above) — but the actual **microphone recording flow itself (real mic →
    permission dialog → real speech → transcript landing in the composer)
    was manually smoke-tested by the human user, not by an automated
    script**, since driving a real microphone and an OS permission dialog
    isn't something that can be scripted here. The user confirmed: "Mic
    smoke confirmed by user: microphone works."
- **Voice Conversation Mode (experimental, `src/hooks/useVoiceConversation.ts`)**
  — hands-free voice loop: `listening → speech_detected → silence_wait →
  finalizing_wait → transcribing → thinking → speaking → listening`,
  auto-sending the transcript through the exact same `handleSend` the manual
  Send button/Enter key use (a completely normal user+assistant message
  pair — never a fake/bypassed one) and speaking the reply back. Toggled via
  a separate `Headphones` button next to the mic in the composer, clearly
  labeled "Conversation · experimental" in the Voice Panel; push-to-talk
  (mic button) is untouched and still works independently — the two are
  mutually exclusive (starting one cancels the other; only one mic stream at
  a time) via `startPushToTalk`/`startConversationMode` in `App.tsx`.
  - **Half-duplex by design**: while transcribing/thinking/speaking (or
    idle/error), `onaudioprocess` ignores mic samples outright even though
    the stream stays open — this is what prevents Siena's own TTS voice
    (played through real speakers, not headphones) from being picked up as
    the next utterance. **No full-duplex/barge-in yet** — the user cannot
    interrupt Siena mid-reply by talking; that needs echo cancellation to
    tell "user talking" apart from "speakers playing Siena's own voice into
    the same mic," left for a future phase.
  - **VAD**: plain RMS amplitude threshold, not a neural model, plus a
    one-time ambient noise-floor calibration at the start of each session
    (first `CALIBRATION_MS = 750ms` of "listening," if the user hasn't
    already started talking, samples room noise; threshold becomes
    `noiseFloor × NOISE_MULTIPLIER (3.0)`, clamped to
    `[MIN_THRESHOLD 0.018, MAX_THRESHOLD 0.06]`). `VOICE_START_MS = 250`ms of
    continuous signal confirms real speech starting (filters clicks/coughs);
    a `PRE_ROLL_MS = 400`ms rolling buffer while "listening" keeps the first
    part of the utterance from being clipped.
  - **Two-stage silence/finalize (bug fix — the first version cut
    utterances off mid-sentence)**: a pause drops `speech_detected` into
    `silence_wait` ("Waiting for you…") immediately; once that silence
    reaches `SILENCE_END_MS = 2000`ms, `voice_conversation_soft_silence_detected`
    fires and the state moves into `finalizing_wait` ("Finishing phrase…")
    for one more `FINALIZE_GRACE_MS = 900`ms. If the user resumes talking
    anywhere in that combined ~2.9s window, `voice_conversation_resumed_before_finalize`
    fires and the loop returns to `speech_detected`, **continuing the same
    utterance buffer** rather than losing what was already said. Only after
    the full grace period elapses does `voice_conversation_utterance_finalized`
    fire and the utterance actually get transcribed/sent. A hard
    `MAX_UTTERANCE_MS = 45000` ceiling forces a cut regardless, so a stuck-open
    mic can't grow the buffer forever. An optional **"Finish" button** in the
    Voice Panel (`conversation.finishNow()`) lets the user manually commit
    the current utterance immediately instead of waiting out the timers —
    it never replaces Stop.
  - **No premature auto-send**: an utterance shorter than
    `MIN_UTTERANCE_MS = 700`ms is discarded before transcription is even
    attempted; an empty or very short (`< 3` chars) transcript after
    transcription is also discarded — both log
    `voice_conversation_utterance_ignored` with a `reason` field and return
    to listening without sending anything. Known tradeoff: the 3-char floor
    also filters a genuinely short reply like "да" — acceptable for an
    experimental mode where a false negative just means "say it again."
  - **TTS for the reply**: prefers the experimental streaming path
    (`streaming.streamSpeak`); if that errors, falls back once to the
    stable WAV Speak path (`speech.speak`) so the loop doesn't just go
    silent; if that also fails, gives up on speaking *that* reply (logs
    `voice_conversation_failed`, stage `tts`) but still returns to
    listening — no infinite retry loop either way.
  - **Diagnostics** (temporary/dev, subtle text in the Voice Panel while
    `speech_detected`/`silence_wait`/`finalizing_wait`): current amplitude,
    threshold, noise floor, speech duration, silence duration, and an
    "auto-send in Xs" countdown during `finalizing_wait` — there to let the
    constants above actually be tuned against real speech instead of
    guessed blind.
  - **Safety**: manual Send/Enter and the push-to-talk mic button are both
    disabled while Conversation Mode is active (avoids two concurrent
    `/api/chat` calls racing); switching conversations stops the session and
    releases the mic tracks/`AudioContext`; stopping mid-`thinking` doesn't
    lose the in-flight `/api/chat` turn (it still lands in history normally)
    but the reply is silently not auto-spoken since the session already
    ended.
  - Trace events (frontend, client-reported): `voice_conversation_started` →
    `voice_conversation_listening` → `voice_conversation_speech_detected` →
    `voice_conversation_soft_silence_detected` →
    `voice_conversation_finalize_wait_started` →
    (`voice_conversation_resumed_before_finalize` if the user kept talking) →
    `voice_conversation_utterance_finalized` →
    `voice_conversation_transcribe_started`/`_completed` →
    `voice_conversation_chat_send_started`/`_completed` →
    `voice_conversation_tts_started`/`_completed` → back to `_listening`, or
    `voice_conversation_utterance_ignored` / `voice_conversation_failed` /
    `voice_conversation_stopped` as appropriate.
  - **Live-verified by the human user** (real microphone, real speakers, not
    scripted — same reasoning as push-to-talk above): a full session
    ("Сиена, ты меня слышишь?") ran four complete
    listen→transcribe→send→speak→listen cycles back to back, every
    utterance transcribed as a complete sentence with no mid-sentence
    cutoff, zero `voice_conversation_failed`/`_utterance_ignored` events,
    and a clean `voice_conversation_stopped` at the end — confirming the
    two-stage silence/finalize fix actually resolved the premature-cutoff
    bug the first version had.
- **Translator** — real (`/api/translate`, per-message Translate button in
  feedback row, translator settings card in Language section).
- **Feedback row — Retry** — real, reuses the existing `/api/chat` flow, no
  new backend endpoint. Safe variant chosen deliberately (documented here
  since the spec allowed either option): Retry is only ever offered for the
  **latest assistant message** — `FeedbackRow`'s button is `disabled` (with a
  `title` tooltip "Retry is only available for the latest assistant reply")
  for every assistant message that isn't the last one in the conversation.
  Clicking Retry on the latest message finds the nearest preceding user
  message (with its original attachments, if any, still held in frontend
  state) and re-sends it through `useChat`'s normal `send()` — this **appends
  a new user+assistant turn at the end**, it never rewrites, replaces, or
  deletes the original assistant message. Also disabled while any send/retry
  is already in flight (`sending` from `useChat`, or another retry in
  progress) to prevent overlapping submissions. Client-event trace:
  `feedback_retry_requested` → `feedback_retry_started` →
  `feedback_retry_completed`/`feedback_retry_failed` (via
  `POST /api/trace/client-event`). Live-verified: Retry disabled+tooltip on a
  non-latest assistant message, enabled on the latest, click appends a new
  user+assistant pair, all three trace events fire in order.
- **Feedback row — Save-to-memory** — real, via new `POST /api/memory/long`
  (see §3). Clicking Save opens an inline editable textarea prefilled with
  the user's current text selection if any (`window.getSelection()`),
  otherwise the first 300 characters of the assistant message — nothing is
  ever saved without this explicit review/edit/confirm step. Save is
  `disabled` when the textarea is empty/whitespace-only. The endpoint calls
  the exact same `LongMemoryStore.save()` used by the `long_memory_save`
  tool (`tools/memory_tools.py`), just tagged `source="feedback_row"` instead
  of `"siena_v2"` so human-saved entries are distinguishable from
  model-saved ones in the Long Memory view. Backend errors (empty text →
  400, store failure → 503) render inline in the editor without closing it
  or crashing the chat. Trace events: `memory_save_from_feedback_started`,
  `memory_save_from_feedback_saved`, `memory_save_from_feedback_failed`.
  Live-verified end to end: opened editor, edited the prefilled text, saved,
  button flipped to "Saved", entry appeared in `GET /api/memory/long` with
  `source: "feedback_row"` and the exact edited text; empty/whitespace text
  correctly kept Save disabled; Cancel discarded the draft without calling
  the backend.
- **OCR (`glm-ocr`) + Vision / image understanding (`qwen2.5vl`)** — two
  separate technical services, routed by `core/image_intent.py`, both real.
  - OCR (`ocr/glm_ocr_service.py`) runs unconditionally on every image
    attachment, unchanged from before — it only reads text
    (`ENABLE_OCR = True`).
  - Vision (`vision/qwen_vision_service.py`, `ENABLE_IMAGE_UNDERSTANDING =
    True`, `IMAGE_UNDERSTANDING_MODEL = "qwen2.5vl"`) is intent-gated: it is
    only called when `core.image_intent.decide_vision()` decides this turn
    is asking what the image *shows*, never for a plain text-reading
    request. Vision is never invoked just because an image is attached —
    this keeps the extra ~seconds-to-a-minute of inference cost and VRAM use
    opt-in.
  - **Bugfix (image/code routing pass)** — a live trace showed a real user
    attaching an image and asking **"Что на этом изображении?"**: OCR ran,
    vision never did, and the main model then wrongly claimed qwen2.5vl was
    unavailable. Root-caused and fixed:
    - The old vision patterns required near-rigid adjacency ("что на
      картинке" but not "что на **этой** картинке") — a demonstrative
      pronoun between the question word and the image noun broke the match
      entirely. Patterns now tolerate a small curated set of filler words
      (`этот/эта/это/этом/этой/тот/тут/там/здесь`) instead of requiring
      literal adjacency, without becoming so loose that they cross-match
      into OCR territory (an earlier draft using a raw `.{0,N}` gap
      accidentally made "что написано на изображении" — an OCR-only
      request — match as vision too; fixed by bounding the gap to the
      filler-word set specifically).
    - "скриншот"/"скрин" was missing from the image-noun list entirely, and
      several common verbs ("посмотри", "взгляни", "глянь", "разбери") had
      no pattern at all.
    - A **second, independent bug**: the old inline gate in `api/server.py`
      was `wants_image_understanding(x) and not wants_ocr(x)` — when a user
      explicitly asked for **both** ("прочитай текст и опиши картинку"),
      both conditions became `True` and the `and not` collapsed the whole
      expression to `False`, so vision silently never ran even though it was
      explicitly requested. Replaced by a single decision function,
      `core.image_intent.decide_vision(text, has_image_attachment) ->
      VisionDecision(run_vision, reason)`, used at both call sites in
      `api/server.py` (the actual `_run_image_vision` gate, and the
      "vision unavailable" honesty check) so they can never drift apart or
      reintroduce this bug independently again.
    - **Ambiguous short question, image attached, no explicit keyword at
      all** ("Что это?", "Что тут?", "Посмотри", "Что думаешь?") now
      defaults to vision — it's a visual question by construction given an
      attached photo. The same phrase with no image attached is completely
      unaffected (`has_image_attachment=False` short-circuits to
      `reason="no_image"`).
  - OCR precedence: an OCR-only request never triggers vision
    (`reason="ocr_only"`). If both are explicitly requested, both run
    (`reason="explicit_both"`) — this is the case the second bug above
    broke.
  - `SYSTEM_PROMPT`'s "Image / Vision / OCR" section was rewritten — the old
    text said "always send the image to qwen2.5vl" and "use qwen2.5vl for
    OCR too", which flatly contradicts the actual two-service architecture
    and was actively teaching the model to expect vision on every image.
    The "Final answer discipline" checklist's image item was similarly
    reworded from "was qwen2.5vl called?" (which invites "no" ⇒ "must be
    broken") to "am I only claiming what the actually-present OCR/vision
    blocks support, and not inventing an unavailability that isn't true?"
  - Trace events: `vision_intent_detected` (now carries a `reason` field —
    `explicit_vision`/`explicit_both`/`ambiguous_fallback`), `vision_started`,
    `vision_completed`, `vision_failed`, `vision_context_injected` (mirrors
    the existing `ocr_*`/`ocr_context_injected` events).
  - `ChatResponse.vision_results` (per-image `status`:
    `described`/`failed`/`unavailable`, plus `chars`/`preview`/`error`)
    mirrors `ocr_results`'s shape. The attachment chip
    (`src/app/App.tsx::AttachChip`) shows a "Vision described/failed/qwen2.5vl
    not installed" line only when a vision result actually came back this
    turn — no placeholder/"ready" state, since the client can't know in
    advance whether vision will be invoked (that's a server-side, per-message
    decision).
  - Live-verified end to end against the real running backend + real Ollama
    + real `qwen2.5vl:latest` (not just unit tests): the **exact reported
    bug phrase** "Что на этом изображении?" now produces
    `vision_intent_detected → vision_started → vision_completed →
    vision_context_injected` and a real grounded description (mentions the
    yellow circle/blue background from the test image) instead of a false
    unavailability claim. "Прочитай текст и опиши картинку" now produces
    both `ocr_completed` and `vision_completed` in the same turn (the
    both-intent bug, confirmed fixed). `scripts/test_qwen_vision_chat.py`'s
    existing A/B/C/D scenarios still PASS unchanged.
  - Unit tests: `tests/test_image_intent.py` (30 cases — every phrase listed
    above, plus the precision regression that caught the OCR/vision
    cross-match during development).
- **Code specialist routing (`qwen2.5-coder:7b`, `core/model_router.py`)** —
  broadened as part of the same pass. `_CODE_PATTERNS` was missing
  "проверь код", a bare "почему не работает" (no explicit "код"/"баг"
  word), and stacktrace/traceback keywords — added. New
  `has_code_context`-aware layer: a small set of otherwise-too-ambiguous
  phrasings ("что за ошибка", "что не так", "прочитай ошибку") only route
  to the code specialist when this turn already has independent code
  evidence — an attached code/text file, or OCR text from an attached
  screenshot that itself looks code/error-shaped
  (`model_router.looks_like_code_or_error()`, a permissive but real
  signal-based heuristic: traceback/exception keywords, `def foo(`,
  `class X`, etc. — not "any text with punctuation"). A plain "исправь этот
  код" still routes correctly with zero attachments regardless — the
  context flag only ever *widens* matching, never narrows the existing
  explicit patterns. A vision-only question ("что на изображении?") never
  matches any code pattern (confirmed by test). Unit tests:
  `tests/test_model_router.py` (19 cases).
- **Settings — unfrozen, but honestly mixed (not "everything is now
  connected").** `POST /api/settings` writes a specific, deliberately small
  set of fields through to `storage/settings.json`
  (`storage/settings_store.py::PERSISTABLE_FIELDS`), reloaded and merged
  over `config.py` defaults on every backend startup, before
  `log_level`/model config take effect. A missing or corrupt
  `settings.json` never blocks startup — it just falls back to `config.py`
  defaults and logs `settings_load_failed`. Trace events:
  `settings_loaded`, `settings_load_failed`, `settings_saved`,
  `settings_save_failed`. Every real toggle below applies **live, no
  restart needed** — the same `config.X = value` + read-fresh-at-call-time
  discipline `log_level` already used. `keep_alive`/model-lifecycle fields
  are deliberately **not** part of any of this — that's a separate,
  not-yet-done pass.
  - **Persisted + live (Model section, pre-existing)**: `primary_model`,
    `code_model`, `max_context_messages`, `num_ctx`, `num_predict`,
    `request_timeout_seconds`, `log_level`.
  - **Persisted + live (new this pass — Settings unfreeze)**: `enable_ocr`,
    `enable_image_understanding`, `enable_translator`,
    `enable_code_specialist_auto`, `enable_reviewer_explicit` (Tools/Code
    sections — real toggles for `config.ENABLE_OCR` /
    `ENABLE_IMAGE_UNDERSTANDING` / `ENABLE_TRANSLATOR` /
    `ENABLE_CODE_SPECIALIST_AUTO` / `ENABLE_REVIEWER_EXPLICIT`, the exact
    flags `_run_image_ocr`/`_run_image_vision`/`_translate_text`/
    `core/model_router.py` already read at call time — flipping one off
    now takes effect on the very next chat turn, not just a fresh process);
    `stt_language` (Voice section — real select bound to
    `config.WHISPER_CPP_LANGUAGE`, the same default both push-to-talk and
    Conversation Mode already fall back to when no language is passed
    explicitly); and `log_level` **now also has an actual UI control**
    (Developer section) — it was already persisted/live before this pass,
    it just had no way to change it from Settings at all.
  - `GET /api/runtime/status` now also echoes `enable_ocr` /
    `enable_image_understanding` / `enable_translator` /
    `enable_code_specialist_auto` / `enable_reviewer_explicit` so their
    current live value is honestly visible outside Settings too — the
    Runtime view's CPU/RAM/VRAM meter *widgets* were not touched.
  - **Frontend-only preference (not backend, not persisted server-side)**:
    Voice section's "Auto-speak new assistant replies by default" — the
    backend has no concept of auto-speak at all (`ChatView`'s own
    `autoSpeak` toggle always was frontend-only), so its *default* value is
    saved to `localStorage` (`AUTO_SPEAK_DEFAULT_KEY`) instead of being
    routed through `/api/settings` for a value the backend would never
    read. Read once on `ChatView` mount; the per-session toggle itself
    still isn't persisted mid-session, same as before.
  - **Read-only runtime data (not a setting)**: Voice section's Status card
    (STT/TTS provider + availability) is a plain, uneditable read of
    `GET /api/voice/status` — shown so a user can see what's actually live
    before touching `stt_language`, not something they can change here.
  - **Still honestly disabled/demo (unchanged from before this pass)**:
    Tools' File system/Network/Memory cards, Code's
    Highlighting/Actions/Font cards, Developer's Electron
    integration/Local API/About cards, all of Appearance (there is no
    theme/accent/font-size switching implemented in the CSS at all — a
    "Light" theme selection would visually do nothing, which is why it's
    left as a clearly labeled decorative demo rather than persisted
    somewhere it would falsely imply an effect it doesn't have), and all of
    Startup.
  - **Deliberately deferred, not just forgotten**: voice profile selection
    and toggling the experimental Stream button's visibility are
    backend-capable (`GET /api/voice/profiles`) but have no frontend UI at
    all yet — connecting them would mean building new UI, not just wiring
    an existing toggle, so it was left out of this point-in-scope pass.
  - **Verification**: `pytest tests/ -q` (57 passed) and `npm run build`
    both clean. Live `curl` round-trip against a running backend: `GET
    /api/settings` shows all new fields with correct defaults;
    `POST /api/settings` with `{"enable_ocr": false, "stt_language": "en"}`
    applied immediately and was reflected in the response,
    `GET /api/runtime/status`, and `storage/settings.json` (which stayed
    valid JSON throughout); an invalid `stt_language` (e.g. `"fr"`) is
    correctly rejected with `400`. **Manual smoke test of the actual
    Settings UI (toggling controls, Saved/Error states, Voice Conversation
    Mode/Speak/Stream/OCR/Vision/Runtime screen unaffected) was confirmed
    by the human user** — "Manual smoke for Settings unfreeze passed."
  - **Full audit/completion pass (later, on top of the above)** — a
    dedicated pass to close the remaining gaps found by re-auditing every
    visible Settings control end to end, rather than leaving them as loose
    threads:
    - **Bugfix**: `storage/settings_store.py::load()` now reads
      `settings.json` with `encoding="utf-8-sig"` instead of plain
      `"utf-8"`. A UTF-8 BOM (e.g. from a human editing/re-saving
      `settings.json` in Notepad, which writes "UTF-8" as UTF-8-with-BOM by
      default) isn't valid JSON syntax on its own, so `json.loads()` used to
      reject the whole file and surface a spurious `settings_load_failed` —
      `utf-8-sig` strips a leading BOM transparently and is otherwise
      identical to plain `utf-8` when there's no BOM, so this is a strict
      improvement with no behavior change for the common case.
    - **Gap found and closed**: `request_timeout_seconds` was already fully
      real on the backend (persisted in `PERSISTABLE_FIELDS`, part of the
      `client_affecting` set that triggers `_rebuild_ollama_client()` on
      change) but had **no UI control anywhere in Settings** — added a
      "Request timeout (seconds)" field to the Model section's Generation
      defaults card, saved together with `num_ctx`/`num_predict`/
      `max_context_messages` by the same Save button.
    - Everything else audited came back unchanged from the original
      Settings unfreeze pass — `primary_model`/`code_model` intentionally
      stay read-only display in Settings (switching lives in the Models
      screen, validated against Ollama there); `num_ctx`/`num_predict`/
      `max_context_messages`/`log_level` and all five Tools/Code toggles
      and `stt_language` were re-verified still fully working; every
      decorative/demo section (Appearance, Startup, Tools' File
      system/Network/Memory, Code's Highlighting/Actions/Font, Developer's
      Electron integration/Local API/About) was re-checked and remains
      honestly labeled rather than newly hidden — the existing
      `LocalOnlyNotice` banners plus "(demo, not connected)" card titles
      were judged sufficient (the task explicitly allows "hide, disable, or
      label" as equally valid options, not just disable).
    - **Tests added**: `tests/test_settings_store.py` (15 cases — default/
      empty/missing file, save→load roundtrip, merge-not-clobber, UTF-8 BOM
      with and without an actual BOM present, malformed JSON, non-object
      top-level JSON, unknown-field filtering, null-value filtering,
      `PERSISTABLE_FIELDS` drift guard) and `tests/test_settings_endpoint.py`
      (13 cases — real `TestClient(server.app)` integration tests against
      the actual `/api/settings` endpoint, reusing the `import api.server as
      server` + `monkeypatch` pattern already established by
      `tests/test_attachment_persistence.py`/`test_nucleares_bridge.py`
      rather than the module's own unit-test-only convention assumed in an
      earlier pass: covers persist+apply, every field's validation
      rejection — `log_level`, `num_ctx`, `num_predict`, `stt_language`,
      `max_context_messages`, `request_timeout_seconds` — the
      non-persistable-field-still-applies-live case, and the UTF-8 BOM fix
      through the real endpoint, not just the store in isolation).
    - **Verification**: `py_compile` clean, `pytest tests/ -q` → **179
      passed**, `npm run build` clean. **Manual Electron smoke confirmed by
      the human user**: `request_timeout_seconds` visible and saves;
      `num_ctx`/`num_predict`/`max_context_messages` save; OCR/Vision/
      Translator/Code specialist/Reviewer toggles save; `stt_language`
      saves; all values survive a backend **and** UI restart;
      `settings_loaded` (not `settings_load_failed`) appears after restart;
      Chat still works normally throughout.
  - **Settings Pass 2 (Appearance/UI preferences made real)** — built the
    theming/preference infrastructure that didn't exist before: a new
    `UiPreferencesProvider`/`useUiPreferences()` context
    (`src/hooks/useUiPreferences.tsx`) applies `appearance_theme` (dark/
    light/system, via `document.documentElement.dataset.theme` +
    `src/styles/ui-preferences.css` attribute-selector overrides on the
    existing Tailwind arbitrary-value classes — no JSX color literals
    touched), `accent_color` (5 swatches: sienna/slate/forest/amber/violet),
    `ui_font_size`/`ui_density` (zoom-based and padding/gap overrides),
    `show_message_timestamps`, `show_typing_animation`,
    `copy_before_clear_chat` (copies the visible chat to the clipboard
    before New Chat clears it, non-blocking if the clipboard API fails),
    `startup_page` (chat/runtime/settings, applied once after the real
    settings load), and `code_font_size`/`code_line_wrap`. All ten fields
    persisted through the same `config.py` ↔
    `storage/settings_store.py::PERSISTABLE_FIELDS` ↔ `api/server.py`
    pipeline as every other real setting, defaults matching the untouched
    look exactly (dark/sienna/default/comfortable, timestamps + typing
    animation on). `pytest` → 188 passed, `npm run build` clean, manual
    Electron smoke confirmed by the human user.
  - **Settings Pass 3 (removed the remaining fake interactive controls)** —
    the rule going forward: real controls are interactive; unsupported/
    deferred capabilities are disabled/read-only; fake/demo toggles are
    removed or replaced with honest status cards, never left looking
    active while doing nothing.
    - **Startup** — startup page stays real. Preload/warmup/launch-at-login
      are now explicit disabled/deferred cards (not toggles) — reason
      documented in the UI itself: no Electron IPC/preload bridge exists to
      reach `app.setLoginItemSettings()` from the renderer, and
      `electron/main.cjs` still has none (see §7, unchanged).
    - **Tool permissions** — the fake File system/Network/Memory toggle
      cards (9 switches that never gated anything) were **removed**,
      replaced by one read-only card listing the live `registered_tools`
      from `GET /api/runtime/status`, with an explicit note that tool
      access is controlled by the backend's tool registry, not by switches
      in this UI.
    - **Code rendering** — six settings made real:
      `code_syntax_highlighting`, `code_show_line_numbers`,
      `code_show_language_badge`, `code_show_copy_button`,
      `code_show_collapse_button`, `code_show_save_button`, all wired into
      `SyntaxHighlight`/`CodeBlock` (`src/app/App.tsx`). The Save button now
      does something real (downloads the snippet as a local file via a
      `Blob` + throwaway `<a download>`, no backend involved) instead of a
      no-op `onClick`. The Apply-patch button and its "confirm before
      applying" toggle were **removed** — Siena has no file-editing target
      for a patch to apply to, so it never did anything real and adding a
      setting to control a nonexistent action would just be a differently-shaped
      fake control.
    - **Voice** — `show_experimental_stream_button` is now real (persisted
      visibility toggle for the "Stream" button in the feedback row). The
      voice profile picker is now real too, but through infrastructure that
      already existed and needed no schema change: `voice/voice_profiles.py`
      / `storage/voice_profiles.json` / the existing
      `GET/POST /api/voice/profiles*` endpoints (see §3) — the new
      `VoiceProfileCard` (`src/app/App.tsx`) lists profiles and activates
      one via `POST /api/voice/profiles/active`, which `VoiceService` already
      reads live for the next Speak/Stream call.
    - **Language** — interface language was disabled here (no i18n system
      existed in this codebase to make it real) — **corrected in the very
      next pass, see the real UI localization entry below; it is not
      disabled anymore.** Preferred input
      language was a separate fake field — it's now just the real
      `stt_language` shown here too, not a duplicate setting. Preferred
      conversation/output language (previously two separate fake selects)
      were merged into one real field, `preferred_response_language`
      (`auto`/`ru`/`en`, default `auto`) — read live in `api/server.py::chat`
      and, only when not `auto`, injected as one soft preference line
      alongside the existing attachment/OCR/vision/Nucleares context
      (`_LANGUAGE_PREFERENCE_NOTES`). It never rewrites
      `config.SYSTEM_PROMPT`, never overrides an explicit user request in
      another language or code, and `auto` (the default) injects nothing at
      all — Siena's natural Russian conversation behavior is unchanged out
      of the box. Language presets now set `stt_language` +
      `preferred_response_language` together instead of doing nothing. The
      Translator card's duplicate "Enable Translator" toggle, unused
      source/target selects, and a "Translate OCR results" toggle with no
      corresponding action anywhere were removed; "Preserve formatting" is
      now real (a `localStorage` preference, since it only shapes what the
      already-existing per-message Translate button sends as
      `TranslateRequest.preserve_formatting` — a frontend request-shaping
      choice, not a `config.py` default, so it doesn't need a settings
      round trip).
    - **Developer** — `log_level` unchanged (real). Electron
      integration and Local API cards were converted from fake
      toggles/editable inputs into **read-only diagnostic cards**: the
      Electron card now shows the real, verified-from-`main.cjs` facts
      (context isolation on, node integration off, sandboxed, no IPC
      bridge/preload script); the Local API card shows the real fixed port
      (`8000`, not the old mockup's `11434`, which was actually Ollama's
      port) and that there is no authentication. The About card's
      fabricated version/build/Python/llama.cpp numbers were replaced with
      only what's actually true: the app version now comes from
      `package.json` via a Vite `define` (`vite.config.ts`), Electron is
      shown as its pinned `package.json` devDependency range.
    - **New persisted fields** (same `config.py`/`settings_store.py`/
      `api/server.py` pipeline as every other real setting):
      `code_syntax_highlighting`, `code_show_line_numbers`,
      `code_show_language_badge`, `code_show_copy_button`,
      `code_show_collapse_button`, `code_show_save_button`,
      `show_experimental_stream_button`, `preferred_response_language`.
    - **Verification**: `py_compile` clean, `pytest tests/ -q` → **195
      passed** (including new tests for every new field's default/roundtrip/
      invalid-enum-rejection, and dedicated tests confirming the language
      preference note is only injected for `ru`/`en`, never for the default
      `auto`), `npm run build` clean. **Manual Electron smoke confirmed by
      the human user**: real settings persist and apply live; disabled/
      deferred settings no longer look fake-interactive; all values survive
      a backend + Electron restart; Chat still works; no
      `settings_load_failed`.
  - **Real UI localization (corrects the previous pass, which disabled
    Interface language instead of implementing it)** — a real i18n system,
    not a decorative language selector:
    - **New infrastructure**: `src/i18n/types.ts` (`Locale = "en" | "ru"`,
      `SUPPORTED_LOCALES`, `DEFAULT_LOCALE = "en"`), `src/i18n/index.ts`
      (`translate(locale, key, params?)` — looks up the current locale,
      falls back to English if the key is missing there, falls back to the
      raw key string if it's missing everywhere, so a typo'd/unregistered
      key is visibly wrong instead of silently blank), and
      `src/i18n/locales/{en,ru}.json`. Locale dictionaries are flat,
      dot-namespaced keys (e.g. `"settings.language.interface"`), not
      nested objects — keeps the fallback lookup a single object index.
      **English and Russian have full key parity: 231 keys each, verified
      with no gaps either direction.**
    - **`t()` lives in the existing `useUiPreferences()` context**
      (`src/hooks/useUiPreferences.tsx`) rather than a new provider —
      `interfaceLanguage` is just one more real UI preference alongside
      theme/accent/font-size/etc., and `t(key, params?)` is derived from it
      via `useMemo`. Every localized component reads strings through `t()`,
      never a hardcoded literal.
    - **`interface_language`** — real, persisted, same
      `config.py` (default `"en"`) ↔ `storage/settings_store.py::
      PERSISTABLE_FIELDS` ↔ `api/server.py` (`SettingsUpdate`/
      `_settings_payload`/validation against `_INTERFACE_LANGUAGES =
      {"en", "ru"}`/apply-to-`config.INTERFACE_LANGUAGE`) pipeline as every
      other real setting. Explicitly separate from `stt_language` (voice
      input), `preferred_response_language` (soft model-reply preference),
      and the Translator's own settings — switching UI language never
      touches any of the three.
    - **Live switching + persistence**: changing the selector in
      Settings > Language > Interface language calls `save({
      interface_language })`, which updates `document.documentElement.lang`
      immediately (`useUiPreferences`'s `applyToDocument`) and re-renders
      every `t()`-driven string with no reload. The choice is cached to
      `localStorage` (same mechanism as theme/accent/font-size/density) so
      `main.tsx` applies it synchronously before React mounts on the next
      launch, avoiding a flash of the wrong language.
    - **Localized this pass**: main sidebar nav (Chat/Tool Trace/Short
      Memory/Long Memory/Insights/Logs/Models/Runtime/Debug/Settings) +
      "Recent sessions"/"Backend unreachable"/"No conversations yet";
      Settings sidebar nav; all 8 Settings screens in full (titles,
      descriptions, card titles, toggle labels/subs, banners, buttons —
      including the Interface language selector itself, the read-only
      Electron/Local API/About cards, and the Translator card); the chat
      composer placeholder (idle/thinking/voice-active variants) and the
      empty-chat "Select a conversation..."/"New chat" state; common
      per-message buttons (Copy/Copied, Retry/Retrying, Save/Saved,
      Translate/Translating, Speak/Preparing/Stop/Speak failed,
      Stream/Stop stream/Stream failed); code block actions
      (copy/copied/collapse/expand/save).
    - **Not yet localized (documented, not hidden)**: deep content of
      Runtime/Debug/Models/Short Memory/Long Memory/Insights/Logs/Tool
      Trace views — dynamic backend data, trace event bodies, diagnostic
      tables. Out of scope for this pass per its own prioritization; no
      control was disabled or hidden to avoid localizing it.
    - **Adding a new UI language later**: add
      `src/i18n/locales/<code>.json` with the same 231 keys, add `"<code>"`
      to `Locale`/`SUPPORTED_LOCALES` (`src/i18n/types.ts`), add `"<code>"`
      to `_INTERFACE_LANGUAGES` in `api/server.py` if it should validate
      server-side. Nothing else in the app needs to change.
    - **Verification**: `py_compile` clean, `pytest tests/ -q` → **198
      passed** (new default/roundtrip/invalid-enum tests for
      `interface_language`, plus a check confirming `en.json`/`ru.json`
      parse as valid JSON with matching key sets), `npm run build` clean.
      **Manual Electron smoke confirmed by the human user**: Interface
      language selector is enabled (not disabled/fake); switching
      Russian/English applies live across sidebar, Settings, and chat;
      layout stays usable in Russian; the choice survives a backend +
      Electron restart; Chat still works; no new `settings_load_failed`.
- **Debug page — complete (0.2.0).** Rebuilt from a mix of real-and-empty
  tabs into five fully real tabs, all sourced from data the app already
  had — no second logging/trace architecture introduced:
  - **Overview** — backend/Ollama reachability, active/last-used model,
    registered tool count, trace socket status, context window
    (`num_ctx`), settings load status, active conversation id, app
    version, and a compact read-only Runtime-diagnostics summary (Ollama
    loaded models, TTS server running/not, whisper.cpp running/not, via
    the existing `GET /api/resources/status` — doesn't duplicate the full
    Runtime page).
  - **Tool Calls** — `pairToolTraceEvents()` (`src/app/App.tsx`) extended
    to also cover Vision, regular Speak (`voice_synthesize_*`), STT
    (`stt_transcribe_*`), Nucleares status/context, and Insights
    (`candidate_memory_*`) — previously only OCR/Translator/specialist/
    router events showed up here.
  - **Last Request** (replaces the old empty "Payload" tab) — reconstructs
    the most recent chat turn purely from existing trace events: user
    message time, routing decision, tool calls, done reason, a computed
    duration, and a truncated final-answer preview. Any field whose event
    never fired is omitted, never invented.
  - **Errors** (replaces the old empty "Timing" tab) — reads
    `GET /api/logs/recent` (already existing, unfiltered JSONL) filtered
    to `level == "ERROR"`, with a per-row copy button.
  - **Memory** — short/long memory counts (unchanged) plus a real pending-
    Insights count via the existing `GET /api/insights`.
  - The old **Delegation** and **Timing** tabs were removed outright
    (permanently-empty placeholders with no data source) rather than kept
    as decorative dead tabs.
  - **Diagnostic export** — "Copy debug report" / "Download report" in the
    header: app version, backend reachability, a redacted settings
    summary, active model/conversation info, resource status, the last 20
    error entries, and the last 50 trace events — `content` fields
    truncated to 160 chars and `ollama_raw_response`'s full `raw` payload
    dropped entirely, so no full conversation history or raw model
    payloads leak into a bug report.
  - One small backend change in support of this: `/api/trace/recent`'s
    event allowlist gained `voice_synthesize_start`/`_result` and
    `candidate_memory_created`/`_promoted`/`_rejected`/`_deferred`/
    `_deleted` — these events already existed and were already logged,
    just never surfaced through this endpoint before.
  - Fully localized (EN/RU) — every visible Debug label/button/tab name/
    empty-state goes through `t()`; only dynamic trace data (tool names,
    event names, raw JSON) stays untranslated, matching the rest of the
    app's localization carve-out for technical data.
  - **Verification**: `py_compile` clean, `pytest tests/ -q` → **200
    passed** (2 new tests for the trace allowlist extension), `npm run
    build` clean. **Manual Electron smoke confirmed by the human user**:
    Debug shows real data; sending a chat message updates the Last Request
    tab; Speak/Nucleares activity appears in Tool Calls; Errors panel
    stays calm with none and shows real entries when triggered; Refresh
    updates visible data; Copy/Download report produce valid, secret-free
    JSON; EN/RU switch updates Debug labels; survives a backend + Electron
    restart; Chat still works; no new `settings_load_failed`.
- **Runtime view — CPU/RAM meters** — real, via `core/system_metrics.py`
  (`psutil`, in-process, computed fresh on every `/api/runtime/status` poll):
  `cpu_percent`, `ram_total_gb`, `ram_used_gb`, `ram_available_gb`,
  `ram_percent`.
- **Runtime view — VRAM meter** — real **only when a working `nvidia-smi` is
  on `PATH`** (`vram_supported: true`, with `vram_total_gb`/`vram_used_gb`/
  `vram_percent`). On AMD (this machine's RX 7900 XTX) or any system without
  a working NVIDIA driver, `vram_supported: false` with a human-readable
  `vram_reason` — the UI shows "Not available" and the reason, never a fake
  0%. AMD VRAM is deliberately not attempted through WMI
  (`Win32_VideoController.AdapterRAM` is a known-truncated 32-bit field —
  verified reporting ~4 GB for this exact 24 GB card) or through raw
  ctypes/ADL calls (crash risk to the whole backend process for a "nice to
  have" meter). One-time `runtime_vram_probe` trace event logged at backend
  startup — never repeated on every 5s poll.

## 6. What is honestly "Not connected yet"

- **Runtime view VRAM** — only when `vram_supported: false` (currently: any
  non-NVIDIA GPU, including this machine's AMD RX 7900 XTX). See §5 above for
  why, and the honest in-UI reason shown instead of a fake number.
- ~~Debug view — Delegation / Timing / Payload tabs~~ — **superseded, see
  §5's "Debug page — complete" entry.** Those two placeholder tabs were
  removed outright (not just filled in); their content lives in the new
  Last Request and Errors tabs instead. Debug is fully real as of 0.2.0 —
  do not read this bullet as still describing the current state.
- **Settings — runtime-only fields (unchanged since the Settings unfreeze
  pass, see §5 for what *did* change)**: `ollama_host`, `max_iterations`,
  and `delegate_timeout_seconds` are still accepted by `POST /api/settings`
  and applied live, but intentionally **not persisted** to disk (out of
  scope for this pass — see `NEXTDO.md`). `keep_alive`/model-lifecycle
  settings are intentionally not part of any Settings section yet — a
  separate, not-yet-done pass.
  - **Superseded by Settings Pass 2/3 (see §5)** — Appearance is no longer
    UI-only (theme/accent/font-size/density/timestamps/typing-animation/
    copy-before-clear/startup_page/code font+wrap are all real and
    persisted); Startup's page selection is real; Tools' File system/
    Network/Memory cards were removed (replaced by a read-only tool-registry
    status card, not left as decorative toggles); Code's remaining
    highlighting/action visibility toggles are real and Apply-patch was
    removed outright; Developer's Electron/Local API cards are read-only
    diagnostics instead of fake controls. **Interface language was
    disabled here (no i18n system existed) but is now real too — see §5's
    "Real UI localization" entry — do not re-disable it.** The only
    settings still honestly disabled/deferred are: startup preload/warmup/
    launch-at-login (no Electron IPC/preload bridge exists), and
    voice-profile creation/editing beyond picking one of the existing
    profiles (the picker itself is real, see §5).
- **Feedback row — Like / Dislike** — still local UI state only, no backend
  behind them, no analytics. Copy / Translate / Speak / Retry / Save-to-memory
  are all real (see §5).

## 7. What is intentionally frozen (do not "fix" without being asked)

- **Mic button / STT / voice input recognition — no longer frozen, moved
  here from history.** This section used to say the mic button was
  hard-`disabled` pending an "AMD/CUDA migration." That is no longer true:
  as of the Phase 2 mic UI work, the mic button is real, `getUserMedia`
  recording is real, and `POST /api/voice/stt/transcribe` (whisper.cpp) is
  wired up end to end — see §5 for the full description. Nothing about
  STT/mic recording is frozen anymore. The only thing still gating the mic
  button is the honest runtime check `GET /api/voice/status`'s
  `stt_available` — if whisper.cpp's exe/model ever go missing, the button
  disables itself again with the reason in its tooltip, but that's a normal
  availability check, not an intentional freeze.
- **Streaming TTS playback (UI) — superseded, see §5.** UI streaming for the
  experimental PCM path is now implemented (Phase 3, `useStreamingSpeech.ts`
  + the "Stream" button in the feedback row) — do not read older notes
  elsewhere as still current. It is a clearly-marked-experimental *addition*
  next to the stable Speak button, not a replacement: `useSpeech.ts`, the
  Speak button, and `/api/voice/synthesize`'s WAV-per-request path (with its
  Silero fallback) are completely untouched and remain the default/stable
  path. The Composer's big Voice Orb is now the real STT UI (§5) and is
  still **not** reused for Speak/Stream — those keep their own small inline
  idle/preparing/speaking/error indicator in the message's feedback row, to
  avoid entangling TTS playback state with the mic/STT recording state
  machine (two genuinely different things happening on two different
  buttons, not a frozen/unfrozen distinction anymore).
- **Electron IPC bridge (`window.siena`, preload script)** — not built, and
  should not be built preemptively. The REST+WS approach works and is
  simpler; only add IPC if a specific capability needs it (native file
  dialogs, system tray, etc.).

---

## 8. Known regressions / next fixes

- **Attachment persistence / in-flight lifecycle verification (Pass 1 + Pass
  1B) passed.** Verified after the manual Electron smoke: uploaded
  attachments persist across conversation switch/reload, and switching chats
  during OCR/Vision/model generation no longer loses attachments or appends
  the result to the wrong visible chat. Validation for the implementation
  pass also passed: `py_compile`, `pytest` (`138 passed, 1 warning`), and
  `npm run build`.
- **Known limitation: no true async job-id/polling architecture yet.**
  `/api/chat` is still synchronous with one global in-flight generation.
  Pending updates may require conversation reload/reactivation rather than a
  live background update. A future job-id/polling design can make pending
  turns update live without depending on the original HTTP request lifecycle.
- **Generated images remain future work.** `storage/attachments/generated/`
  is reserved only. There is no generated image provider, no generated image
  storage UI, and no image generation implementation in this pass. Treat
  image generation as future Pass 2/3 work.

These are open items from the most recent regression pass, kept here so they
aren't rediscovered from scratch:

- **Wagner/research grounding regression — retested and mitigated.** Live
  regression retest via the real `/api/chat` (not a unit stub) found that the
  model was answering real-world identification/history questions ("Кто
  такие Вагнеры?", "Что произошло с ЧВК Вагнер с 2022 по 2026 год?") straight
  from its own training knowledge, without calling `web_search` at all —
  the `open_url`-escalation and contradiction-guard prompt rules never had a
  chance to fire because no tool was ever invoked. Fixed with:
  `core/research_intent.py` (`wants_grounded_research(text)` — a diagnostic
  regex nudge, same pattern as `core/memory_intent.py`), a new
  `research_grounding_intent_detected` trace event, and a strengthened
  `config.SYSTEM_PROMPT` (identification/status/history questions must
  trigger `web_search`; "кто такие X" / "что произошло с X" / a "с YYYY по
  YYYY" year range are explicit triggers; multi-year questions require 2-3
  narrower search queries instead of one broad one; contradiction guard
  hardened against internally-inconsistent claims). Verified with
  `scripts/test_wagner_regression.py` — a live regression script against the
  real backend (fixed one bug in the script itself along the way: it now
  matches the *latest* matching trace entry instead of the first, since
  `/api/trace/recent` accumulates across runs). Confirmed no longer
  reproduced: Prigozhin/Utkin "arrested" claims, the fabricated
  Belgorod/truck incident, invented names/leadership figures, or "судя по
  найденным данным" claims when no search actually happened. **Residual risk
  (WARN, not a blocker):** qwen3.5:9b occasionally gets the exact day of
  Prigozhin/Utkin's death wrong (e.g. 23 vs 24 August) while still getting
  the month/year/event/location/names right — accepted as known small-model
  instability; no source-grounded date guard planned for now.
- The following four were fixed and live-verified in the last pass, but are
  listed here as regression cases worth re-checking after any future
  `SYSTEM_PROMPT` or chat-pipeline change:
  - Explicit memory-save intent ("запомни", "добавь что...") must route to
    `long_memory_save` (via `core/memory_intent.py`'s soft nudge), not a plain
    text answer.
  - "Пришли мне ссылки на погоду" (no city given) must ask for the city —
    must never silently assume Moscow, and must not call `web_search` before
    the city is known.
  - Markdown/fenced-code-block rendering must preserve newlines end-to-end
    (`parseMessageSegments`/`repairMalformedFences` in `App.tsx`).
  - OS-specific scripts (e.g. "напиши bash-скрипт...") must ask which OS or
    provide clearly labeled Linux/macOS variants — must not silently assume
    macOS.

---

## 9. Do not break

These subsystems work today and have no reason to be touched except by an
explicit, scoped request:

- `qwen3_tts_ggml_vulkan` (Vulkan/GGML Qwen3-TTS provider) — tested, PASS.
- `glm-ocr` OCR pipeline (`OCR_MODEL = "glm-ocr"`) — text extraction only,
  unconditional on every image attachment, unchanged by the vision work
  below.
- `qwen2.5vl` vision / image understanding (`ENABLE_IMAGE_UNDERSTANDING =
  True`, `IMAGE_UNDERSTANDING_MODEL = "qwen2.5vl"`,
  `vision/qwen_vision_service.py`) — connected and live-verified (see §5).
  Intent-gated by `core/image_intent.py::decide_vision()` (see §5 for the
  full precedence rules, revised in the image/code routing pass) — do not
  make it fire unconditionally on every image attachment, that would
  silently add ~seconds-to-a-minute of extra latency and VRAM use to plain
  OCR-only turns.
- `translategemma:4b` translator (`translator/translator_service.py`,
  `/api/translate`).
- `qwen3.5:27b` manual-only policy — never a routing candidate, only
  reachable via the explicit `POST /api/models/active` human action
  (`config.MANUAL_HEAVY_MODEL`, `ALLOWED_MANUAL_CHAT_MODELS`).
- **Tool-model keep_alive behavior** — OCR (`glm-ocr`) and the translator
  (`translategemma:4b`) currently call `ollama.Client(...).chat(...)` with no
  explicit `keep_alive` override, i.e. they rely on Ollama's own default
  unload timing. There is no custom keep_alive policy coded today — don't add
  one without a specific, stated reason, since it would change model
  load/unload/VRAM behavior for every caller of these services.

---

## 10. 0.2.0 release finalization

Consolidated snapshot for the 0.2.0 release candidate. Detail for each item
lives in §5 above (or `docs/` — see the doc map in the repo's `README.md`);
this section exists so a reader doesn't have to reconstruct release status
by skimming the whole changelog-style §5.

**Startup.**
```
scripts\start_backend.ps1      # backend — portable, no hardcoded path, no --reload
scripts\start_ui.ps1           # or: cd "Siena v2 Control Panel UI" && npm run desktop
```
`start_backend.bat` (repo root) also works — it delegates to the `.ps1`
script when PowerShell is available. **No `--reload` for normal use** (see
§2) — it restarts in-memory worker state on every watched-file change,
which is a dev-only convenience, not a normal-run default anymore.

**Settings.** Real, persisted, live-applying end to end: backend/model
settings, Tool permissions, Appearance (theme/accent/font size/density/
timestamps/typing-animation/copy-before-clear), Code rendering, Startup
page, and Language (interface/input/response). Deferred and honestly
labeled as such (not faked): startup preload/warmup, launch-at-login,
Electron IPC/Local API diagnostics (read-only, not configurable). See §5's
Settings Pass 2/3 entries and `docs/SETTINGS.md`.

**Localization (EN/RU).** Real i18n system (`src/i18n/`), not a decorative
selector — see §5's "Real UI localization" entry. Covers main sidebar,
Settings sidebar + all 8 Settings screens, chat composer, common message
action buttons, code block actions, and the Debug page. Not yet localized:
deep content of Runtime/Debug-technical-data/Models/Memory/Insights/Logs/
Tool Trace (English only, by design for this release).

**Voice (STT/TTS).** Active STT engine is whisper.cpp (Vulkan), not the
dormant `faster-whisper` package still in `requirements.txt`. TTS defaults
to Faster Qwen3-TTS with automatic Silero fallback; the experimental
"Stream" button's visibility is itself a real, persisted setting. Voice
profiles (timbre presets, separate from personality) are real and
switchable. See §5's Voice entries and `docs/VOICE.md`.

**Attachments / OCR / Vision.** Attachment persistence across conversation
switch/reload is real (Pass 1/1B). OCR (`glm-ocr`) runs unconditionally on
every image attachment; Vision (`qwen2.5vl`) is intent-gated — only runs
when you actually ask what an image shows. Both degrade honestly (reported
unavailable, not crashed) if their model isn't installed.

**Translator.** Real (`/api/translate`, per-message Translate button).
Requires the locally-built `translategemma-strict:4b` (see
`docs/INSTALL.md`), falls back to `qwen3.5:9b` if unavailable.

**Resource Manager.** `GET /api/resources/status` — real, on-demand (not
polled): Ollama's own loaded models, the external `tts-server.exe`
subprocess (backend-managed vs. orphaned), and whisper.cpp's
`whisper-cli.exe` process. Manual tool-model unload
(`POST /api/models/lifecycle/unload`) is real; no automatic unload policy
exists (intentional — see §9).

**Nucleares Game Bridge.** Read-only local telemetry only —
`GET /api/game/nucleares/status` plus on-demand chat context injection
(Phase 2) when a message actually asks about station state. No writes, no
commands, no polling daemon, and this describes Nucleares
**game-simulation** telemetry, not real-world nuclear operation guidance.
Untouched by this release-finalization pass except for this documentation
summary.

**Debug page.** Complete as of 0.2.0 (see §5's "Debug page — complete"
entry) — Overview, Tool Calls, Last Request, Errors, and Memory tabs, all
real, plus a secret-free diagnostic export. The old Delegation/Timing/
Payload placeholder tabs are gone, not just filled in.

**Release limitations (0.2.0).** Games Hub does not exist in this release.
Model-role configuration (swapping which model handles which job) isn't
user-configurable beyond manually switching the active chat model. No
image generation. AMD/non-NVIDIA GPUs don't get Runtime VRAM metrics
(NVIDIA-only by design, honest "not available" shown instead of a fake
number). `npm run build`'s chunk-size advisory is non-blocking. Full list:
`docs/KNOWN_LIMITATIONS_0.2.0.md`.

**Recommended next work after 0.2.0** (not started, not promised for any
specific future version):
1. Model-role configuration — let a user reassign which installed model
   fills each role (code/OCR/vision/translator), not just the existing
   manual active-chat-model switch.
2. Frontend bundle code-splitting (resolve the chunk-size advisory for
   real, e.g. lazy-loading Settings/Debug/Runtime views).
3. Localize the remaining deep pages (Runtime, Models, Memory, Insights,
   Logs, Tool Trace) beyond their current technical-data carve-out.
4. Decide on Games Hub scope, if/when it's prioritized — explicitly out of
   scope for every pass up to and including 0.2.0.
5. Installer packaging — deliberately not attempted this release.
6. Repo hygiene follow-through: the non-blocking cleanup items in
   `docs/KNOWN_LIMITATIONS_0.2.0.md` (`NEXTDO.md`, `requirements-before-amd-test.txt`,
   the two root-level ad-hoc `test_*_direct.py` scripts) are still sitting
   there — fine to leave, but worth an explicit decision at some point.

**Validation for this finalization pass.** `py_compile` clean,
`pytest tests -q` → **200 passed**, `npm run build` clean (chunk-size
advisory only). Version bumped `0.0.1` → `0.2.0` in
`Siena v2 Control Panel UI/package.json` (and `package-lock.json`) — the
single canonical source; every visible version display (Settings sidebar,
Developer Settings About card, Debug Overview, Debug export report) reads
it through one shared `APP_VERSION` constant, not a second hand-maintained
one.

**Manual RC smoke: PASSED (2026-07-10).** Backend start/stop
(`start_backend.bat`, `stop_backend.bat`, `scripts\start_backend.ps1`), UI
startup, Chat, Settings persistence, EN/RU UI language switching, Voice
(TTS/STT), Attachments/OCR/Vision, Translator, Debug, and the Nucleares
read-only bridge (with Nucleares running) were all verified by the human
user against a real running backend + Electron UI. Version confirmed
visible as 0.2.0 everywhere it's shown; no `settings_load_failed` observed
across a normal restart. Full item list in
`docs/RELEASE_CHECKLIST_0.2.0.md`. Known limitations (no installer, Games
Hub, model-role configuration, image generation, deep-page localization,
AMD VRAM metrics, Nucleares read-only) are unchanged — see
`docs/KNOWN_LIMITATIONS_0.2.0.md`.

---

## Summary of this document's own provenance

Only read, nothing changed: `api/server.py`, `config.py`,
`Siena v2 Control Panel UI/src/app/App.tsx`,
`Siena v2 Control Panel UI/src/api/{sienaClient.ts,types.ts}`,
`Siena v2 Control Panel UI/src/hooks/{useRuntimeStatus.tsx,useTraceSocket.tsx}`,
`voice/qwen_tts_ggml_vulkan.py`, `ocr/glm_ocr_service.py`,
`translator/translator_service.py`, `memory/*`, `requirements.txt`,
`start_backend.bat`, `start_desktop.bat`,
`Siena v2 Control Panel UI/package.json`,
`Siena v2 Control Panel UI/electron/main.cjs`, and the existing
`HANDOFF.md`/`NEXTDO.md`.

New file created: `Siena v2 Control Panel UI/HANDOFF_v2.md` (this document).
`HANDOFF.md` was left untouched as the historical mockup-era spec.
