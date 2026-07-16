# Siena Cyberpunk Companion v0.8.1

Read-only companion pipeline for Cyberpunk 2077. It accepts the existing CET/RedHttpClient telemetry (or the simulator), converts 4 Hz state into rare normalized events, and preserves the v0.7 in-game presence path. v0.8.1 adds only the first live-confirmed, nullable Deep Game State slice; see [V0.8.1_DEEP_GAME_STATE.md](V0.8.1_DEEP_GAME_STATE.md).

It does **not** send commands to Cyberpunk, control the player/NPCs, play audio in CET, start Siena/Ollama, or invoke TTS from Lua. The legacy `CompanionCommand` API remains only as a display-only v0.1/v0.2 compatibility surface and is never consumed by the CET mod. Siena Core, voice, backend presence, and local CET presence are separate opt-ins.

## Pipeline

```text
CET bridge or simulator
  -> POST /api/v1/telemetry/state
  -> StateStore (source/session sequence guard)
  -> StateDiff
  -> EventSynthesizer (capability-aware detectors + session/idle latches)
  -> EventFilter (damage aggregation + per-event cooldown)
  -> SceneEventNormalizer (legacy alias collapse for interpretation only)
  -> SceneContextBuilder (phase, severity, health trend, bounded facts/history)
  -> CompanionBehaviorPolicy (silence, focus, per-scene budget)
  -> ReactionPlanner (priority + cooldown reservation)
  -> bounded priority queue (one asynchronous worker)
  -> Siena Core local HTTP or explicit template fallback
  -> bounded EventBus
  -> REST + existing /ws
  -> React Current Scene / Game Events / Siena Reactions
  -> VoiceBehaviorPolicy (published reactions only)
  -> bounded priority voice queue + one asynchronous worker
  -> Siena v2 POST /api/external/speech (binary WAV)
  -> bounded in-memory clip store + REST / existing /ws
  -> React autoplay unlock + single-tab playback leader
  -> InGamePresenceProjection (published lifecycle/reaction/voice state only)
  -> revision-aware local REST polling
  -> CET compact no-input presence card
```

The required v0.2/v0.7 protocol fields remain unchanged. `BridgeRegistry` still negotiates protocol `1.0`, gives live CET priority in `auto`, reports `source_conflict`, and exposes capabilities. v0.8.1 adds optional `deep_game_state` and default-false domain capabilities; bridges and telemetry payloads without them remain valid.

## Install and run

Requirements: Windows PowerShell 5.1+, Python 3.12+, Node.js 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\frontend
npm.cmd install
Set-Location ..
```

Run processes in separate terminals:

```powershell
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
.\scripts\start_simulator.ps1 -Scenario v04_siena_core -Speed 20
```

Or launch all three:

```powershell
.\scripts\start_all.ps1 -Scenario v04_siena_core -Speed 20
```

Backend: `http://127.0.0.1:8765`; UI: `http://127.0.0.1:5173`; WebSocket: `ws://127.0.0.1:8765/ws`.

For the real game, start the backend and frontend, install/start the already documented CET bridge, and do not start the simulator. See [CET_INSTALLATION.md](CET_INSTALLATION.md), [CET_TROUBLESHOOTING.md](CET_TROUBLESHOOTING.md), and [bridge/cet/README.md](bridge/cet/README.md). The simulator performs a bridge-status preflight and refuses to run when `configured_source=auto` already has a connected CET source.

## Normalized v0.3 events

- Session: `session_started`, `session_ended`
- Health: `player_damaged`, `health_low`, `health_critical`, `player_healed`
- Optional capability transitions: `combat_started`, `combat_ended`, `vehicle_entered`, `vehicle_exited`
- Movement: `player_idle`, `player_moved_after_idle`

Existing v0.1/v0.2 events remain available for compatibility. Each event has a unique `event_id`, UTC `created_at`, source/session/sequence, enum-backed severity, summary, bounded data payload, legacy priority, and deduplication key. The protocol schema is [protocol/game_event.schema.json](protocol/game_event.schema.json).

Defaults: damage is aggregated for 1.5 seconds; low health is latched at 25% until recovery above 35%; critical health is latched at 10%; healing requires a 5% single-state gain; idle starts after 60 seconds under a 0.5-unit movement epsilon; session end occurs after 10 seconds without accepted active telemetry. History is capped at 500 events and 200 reactions.

## Scene context and behavior policy

`SceneContextBuilder` groups normalized game events into session-scoped scenes: session start, exploration, combat, danger, recovery, vehicle, idle, transition, session end, or unknown. It tracks severity, peak severity, health/trend, aggregated damage/healing, capability-aware combat/vehicle state, bounded recent event IDs/facts, revisions, and a compact summary. Scenes close on session changes/end, long meaningful gaps, maximum duration, and significant context transitions. State is process-local and never crosses sessions.

Legacy and normalized aliases remain visible in REST and WebSocket history. The scene normalizer collapses same-frame semantic pairs such as `player_health_critical` + `health_critical`, `player_recovered` + `player_healed`, and both vehicle naming families, so one game fact can produce at most one opportunity.

`CompanionBehaviorPolicy` owns the deterministic decision to speak or stay silent. It selects a bounded focus, enforces a per-scene budget and minimum interval, permits critical bypass, and logs stable suppression reasons without flooding the UI. A critical scene update can replace weaker queued work. Work already generating is not cancelled, but its result is checked against active session, scene ID/revision/focus, critical recency, and TTL before publication.

## Reaction providers

`TemplateReactionProvider` remains the safe default. `SienaCoreReactionProvider` is opt-in. `ReactionPlanner` reserves only mapped meaningful events, applies a 20-second general cooldown and a 60-second same-event cooldown, and lets critical events bypass the general cooldown. The selected event is put into a bounded priority queue; telemetry returns immediately and a single worker performs HTTP calls in order. Critical work can evict lower-priority queued work, while rejected or evicted work is logged explicitly.

The Core request contains a compact event allowlist, a small recent-event window, and the last 3–5 published Siena reactions from the same session (text plus event type only). A new session clears that short reaction context. It omits coordinates, raw telemetry, user chat, long memory, and technical reaction metadata. It cannot choose a model, enable tools/web/TTS, write memory, create a conversation, or invoke specialist routing.

The game prompt states that Siena is the companion/observer and that the player is not Siena. It forbids starting with Siena's name, calling the player Siena, technical backend language, invented game facts, control claims, long tactical instructions, Markdown, stage directions, and routine emoji. `SIENA_CORE_PLAYER_NAME` is optional: when empty no player name is sent; when configured, the name may be used occasionally but not as a repeated prefix.

Responses are schema-validated, stripped of `<think>`/reasoning blocks, Markdown fences, and leading `Siena:`, `Сиена:`, `Ответ:`, `Reaction:`, or `Assistant:` prefixes. JSON-like and empty responses are rejected, whitespace is normalized, and length is bounded without breaking a word. No latin1/cp1251 round-trip or speculative mojibake repair is performed.

Transient network/5xx errors get bounded retries. 4xx responses do not. Repeated failures open a circuit breaker; open state refuses network work until the reset interval, then admits one half-open probe. Timeout, malformed/empty output, open circuit, and configuration errors use `template_fallback` when enabled or produce no reaction when disabled. Old-session, ended-session, TTL-expired, and superseded critical responses are suppressed after the call, so a late model response never leaks into the current session.

```powershell
$env:SIENA_CP_REACTIONS_ENABLED='true'
$env:SIENA_CP_REACTION_PROVIDER='siena_core' # siena_core | template | disabled
$env:SIENA_CP_SIENA_CORE_ENABLED='true'
$env:SIENA_CP_SIENA_CORE_BASE_URL='http://127.0.0.1:8000'
$env:SIENA_CORE_PLAYER_NAME='' # optional; leave empty to omit a player name
..\scripts\start_backend.ps1 -EnableExternalGameReactions # run in Siena_v2 root
.\scripts\start_backend.ps1 -ReactionProvider siena_core -SienaCoreUrl 'http://127.0.0.1:8000'
```

The two projects remain independent processes. The companion never imports Siena_v2 and never starts it automatically. If Siena is unavailable, the status card and `/api/v1/reaction-provider/status` report that fact while telemetry keeps flowing.

## REST API

Existing endpoints remain intact. v0.3 adds:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/events` | `limit`, `event_type`, `severity`, `session_id` filters |
| GET | `/api/v1/events/latest` | latest matching event or `null` |
| GET | `/api/v1/reactions` | `limit`, optional `event_type` |
| GET | `/api/v1/reactions/latest` | latest matching reaction or `null` |
| GET | `/api/v1/planner/status` | enabled/provider/counts/timestamps/cooldown |
| GET | `/api/v1/reaction-provider/status` | cached reachability, circuit, queue, failures/fallbacks |
| GET | `/api/v1/scenes/current` | current active scene or `null` |
| GET | `/api/v1/scenes` | bounded closed history with `limit`, `session_id`, and `phase` filters |

All responses use Pydantic response models; no internal deque is exposed.

## WebSocket

The existing `/ws` continues sending legacy `state`, `event`, `command`, `status`, and `bridge_status` envelopes. v0.3 additionally sends:

```json
{"type":"game_event","payload":{}}
{"type":"siena_reaction","payload":{}}
{"type":"reaction_generation_status","payload":{"request_id":"...","event_id":"...","session_id":"...","event_type":"health_critical","state":"queued"}}
{"type":"scene_context_updated","payload":{"scene_id":"...","phase":"danger","revision":3}}
```

`reaction_generation_status` uses `queued`, `generating`, `completed`, `fallback`, `suppressed`, or `failed`. It never contains the internal prompt or exception. For compatibility the new envelopes also contain the same object in `data`. Each client owns a bounded queue; a disconnected or slow client cannot block telemetry ingestion. The React client loads REST history first, merges WebSocket updates by unique ID, and caps local history.

## Live Siena Reaction

The primary card shows only the current session. It renders idle, queued, generating, completed/fallback, Core-unavailable, and disabled states. Completed text remains readable until a later eligible reaction replaces it. Template fallback is labeled honestly. A critical reaction always replaces weaker content, and a fresh critical result is held for 10 seconds against lower-priority updates. Suppressed/stale results and old-session reactions never become live.

The existing **Siena Reactions** section remains as collapsible diagnostic history. It shows the latest eight current-session reactions by default, can expand to the full bounded local history, and keeps provider/model/latency/fallback/request diagnostics behind secondary disclosure.

## Safe voice reactions

Voice is disabled by default. Text reactions are always published before voice work is considered. `VoiceBehaviorPolicy` accepts only an already-published `SienaReaction`; it never calls Siena Core, rewrites text with an LLM, or voices raw events/lifecycle diagnostics. Danger, critical health, important combat, recovery, scene resolution, and cooldown-safe session greetings are eligible. Duplicate/stale/old-session, weak, muted, over-budget, unsupported, and TTS-unavailable cases remain text-only with structured suppression reasons.

The backend owns one bounded priority queue and one `VoiceWorker`. Critical work can evict weaker queued work from the same scene. A weak request already synthesizing is not cancelled inside Siena's provider; it is marked superseded and its returned WAV is discarded. A separate TTS circuit breaker handles transport/timeouts/5xx/malformed or oversized audio. HTTP 401/403 and other 4xx errors are not retried. No Windows voice fallback is used.

Siena v2 was audited before choosing the contract. The selected provider is currently `qwen3_tts_ggml_vulkan`; Qwen3-TTS, Faster Qwen3-TTS and Silero remain implemented. Stable synthesis is completed WAV. The experimental raw PCM stream is qwentts.cpp-only and has a documented upstream-cancellation limitation, so v0.6 does not use streaming.

Siena v2 adds a minimal local feature-gated endpoint:

```text
POST /api/external/speech
Content-Type: application/json
Accept: audio/wav

{"request_id":"...","text":"...","speaker":null,"language":"ru","audio_format":"wav"}
```

It returns a binary WAV body with `X-Siena-TTS-*` metadata. It does not invoke chat, tools, web, memory, specialists, conversations, or model routing. It is enabled only through `SIENA_EXTERNAL_SPEECH_ENABLED=true` or Siena's explicit `-EnableExternalSpeech` launcher switch. The status probe does not warm or synthesize TTS. A real synthesis may lazily start the human-selected provider only after both external speech and companion voice have been explicitly enabled.

Companion audio is process-local, in-memory, capped, and removed by TTL. JSON/WebSocket messages contain only clip metadata and `/api/v1/voice/audio/{id}`. Session changes and stale scene revisions suppress queued, synthesizing-result, and ready audio. Playing audio is frontend-owned so the backend remains headless.

The browser requires the user to press **Включить голос**. Playback uses one bounded queue, one `HTMLAudioElement`, saved local volume/mute settings, a critical-only interruption mode, and a short post-play gap. A localStorage/BroadcastChannel lease elects one playback tab; the backend also rejects a second tab's playback acknowledgement. Autoplay rejection returns the UI to the explicit-enable state and never affects telemetry.

Voice REST API:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/voice/status` | cached worker/provider/circuit/queue status; no heavy probe |
| GET | `/api/v1/voice/clips` | bounded metadata history; optional session filter |
| GET | `/api/v1/voice/clips/latest` | latest clip metadata or `null` |
| GET | `/api/v1/voice/audio/{voice_clip_id}` | binary WAV; stale/unknown clips return 404 |
| POST | `/api/v1/voice/playback-events` | started/completed/failed/cancelled acknowledgement |

Existing `/ws` additionally publishes `voice_generation_status` and `voice_clip_ready`. Ready payloads contain `audio_url`, never audio bytes.

## In-game companion presence

Presence is disabled by default in both backend and CET. `InGamePresenceProjection` subscribes synchronously and lightly to the existing EventBus publications. It projects queued/generating, published reaction/fallback, voice synthesis and actual browser playback into one strict bounded snapshot. It never calls Siena Core or TTS and stores no prompt, exception, token, raw telemetry, audio, or history.

Snapshot revisions are process-local and monotonic. Identical state does not increment revision. Generating appears only after a 400 ms debounce. Published text replaces generating; critical replaces weak content and is held longer; weak updates cannot overwrite a fresh critical card. Session changes clear old presence, while session end lets the last published line expire naturally. Voice ready is not playing: `Сиена говорит` is shown only after browser `playback_started` and removed on completed/failed/cancelled.

CET polls `GET /api/v1/in-game-presence/current?after_revision=N` at most every 500 ms. A newer snapshot returns JSON; unchanged returns 204. One request token is active at a time, errors use capped exponential backoff, and late callbacks cannot mutate newer state. Only `127.0.0.1` and `localhost` are accepted unless the user explicitly changes the safety setting. Polling runs in `onUpdate`; `onDraw` only renders.

The two CET overlays remain independent:

- **Siena Observer diagnostics** appears only while the CET overlay is open.
- **In-Game Siena Presence** is a separate no-input card during ordinary play, appears only for current content, and fades out automatically.

The card supports six viewport-derived anchors, safe margins, resolution/DPI scaling, word wrap, calm fade, optional fallback/voice labels, and no title/move/resize/navigation/mouse interaction. External `%` is escaped before the proven `TextWrapped` binding. The installed CET NotoSans file contains the Russian test glyphs, but runtime atlas readiness remains explicitly unconfirmed until an in-game visual check. No font binary is shipped.

Presence REST API:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/in-game-presence/current` | strict current snapshot; optional `after_revision`; unchanged is 204 |
| GET | `/api/v1/in-game-presence/status` | cached projection/consumer/poll/overlay diagnostics; no network probe |

The existing `/ws` emits `in_game_presence_status` only for meaningful enabled/connected/state/revision/error/capability changes. CET does not use WebSocket. React adds an observation-only **In-Game Presence** card and cannot control the overlay.

## Simulator scenarios

Existing scenarios remain available. v0.6 adds `v06_voice_normal`, `v06_voice_priority`, `v06_voice_stale`, `v06_voice_session_change`, `v06_voice_failure`, and `v06_voice_no_overlap`. v0.7 adds `v07_presence_normal`, `v07_presence_critical`, `v07_presence_fallback`, `v07_presence_stale`, `v07_presence_disconnect`, and `v07_presence_unicode`; use fake Siena `unicode` mode for the `%`/quotes/dash/newline response.

`v04_siena_core` supplies meaningful events for Core success/failure checks. `simulator/fake_siena.py` is a deterministic local stand-in with success, delay, 500, 401, 403, malformed, empty, reasoning, long, and recovery modes; tests and smoke checks never need a real external network.

## Configuration

All backend variables use prefix `SIENA_CP_`:

| Setting | Default |
|---|---:|
| `TELEMETRY_EXPECTED_RATE_HZ` | 4 |
| `DAMAGE_WINDOW_SECONDS` | 1.5 |
| `SAME_EVENT_COOLDOWN_SECONDS` | 60 |
| `GENERAL_REACTION_COOLDOWN_SECONDS` | 20 |
| `HEALTH_LOW_THRESHOLD_PERCENT` | 25 |
| `HEALTH_LOW_RECOVERY_PERCENT` | 35 |
| `HEALTH_CRITICAL_THRESHOLD_PERCENT` | 10 |
| `HEAL_THRESHOLD_PERCENT` | 5 |
| `IDLE_TIMEOUT_SECONDS` | 60 |
| `PLAYER_POSITION_EPSILON` | 0.5 |
| `SESSION_DISCONNECT_TIMEOUT_SECONDS` | 10 |
| `EVENT_BUFFER_SIZE` | 500 |
| `REACTION_BUFFER_SIZE` | 200 |
| `REACTIONS_ENABLED` | true |
| `REACTION_PROVIDER` | template |
| `SIENA_CORE_ENABLED` | false |
| `SIENA_CORE_BASE_URL` | http://127.0.0.1:8000 |
| `SIENA_CORE_CONNECT_TIMEOUT_SECONDS` | 0.5 |
| `SIENA_CORE_REQUEST_TIMEOUT_SECONDS` | 5 |
| `SIENA_CORE_MAX_RETRIES` | 1 |
| `SIENA_CORE_FALLBACK_ENABLED` | true |
| `SIENA_CORE_CIRCUIT_FAILURE_THRESHOLD` | 3 |
| `SIENA_CORE_CIRCUIT_RESET_SECONDS` | 30 |
| `SIENA_CORE_QUEUE_SIZE` | 32 |
| `SIENA_CORE_MAX_EVENT_AGE_SECONDS` | 20 |
| `SIENA_CORE_RECENT_REACTIONS_LIMIT` | 5 |
| `SCENE_ENABLED` | true |
| `SCENE_EVENT_HISTORY_LIMIT` | 20 |
| `SCENE_HISTORY_LIMIT` | 50 |
| `SCENE_IDLE_GAP_SECONDS` | 30 |
| `SCENE_MAX_DURATION_SECONDS` | 180 |
| `SCENE_RECENT_EVENT_WINDOW_SECONDS` | 45 |
| `SCENE_MAX_REACTIONS` | 3 |
| `SCENE_MIN_REACTION_INTERVAL_SECONDS` | 15 |
| `SCENE_CRITICAL_BYPASS` | true |
| `SCENE_RESOLUTION_REACTION_ENABLED` | true |
| `SCENE_REACTION_STALE_GRACE_SECONDS` | 3 |
| `SCENE_RECENT_REACTIONS_LIMIT` | 5 |
| `VOICE_ENABLED` | false |
| `VOICE_MUTED` | false |
| `VOICE_MIN_INTERVAL_SECONDS` | 8 |
| `VOICE_SAME_TEXT_COOLDOWN_SECONDS` | 120 |
| `VOICE_SCENE_MAX_CLIPS` | 3 |
| `VOICE_CRITICAL_BYPASS` | true |
| `VOICE_MAX_TEXT_CHARS` | 240 |
| `VOICE_MAX_EVENT_AGE_SECONDS` | 30 |
| `VOICE_QUEUE_SIZE` | 20 |
| `VOICE_CLIP_HISTORY_LIMIT` | 100 |
| `VOICE_AUDIO_TTL_SECONDS` | 300 |
| `VOICE_POST_PLAY_GAP_MS` | 250 |
| `VOICE_LANGUAGE` | ru |
| `VOICE_SPEAKER` | empty, use Siena setting |
| `VOICE_VOLUME` | 0.85 |
| `VOICE_REQUIRE_TTS_READY` | true |
| `VOICE_INTERRUPT_MODE` | critical_only |
| `TTS_BASE_URL` | empty |
| `TTS_CONNECT_TIMEOUT_SECONDS` | 2 |
| `TTS_REQUEST_TIMEOUT_SECONDS` | 60 |
| `TTS_MAX_RETRIES` | 1 |
| `TTS_MAX_AUDIO_BYTES` | 15000000 |
| `TTS_CIRCUIT_FAILURE_THRESHOLD` | 3 |
| `TTS_CIRCUIT_RESET_SECONDS` | 30 |
| `PRESENCE_ENABLED` | false |
| `PRESENCE_POLL_INTERVAL_MS` | 500 |
| `PRESENCE_HTTP_TIMEOUT_MS` | 1000 |
| `PRESENCE_GENERATING_DELAY_MS` | 400 |
| `PRESENCE_NORMAL_DURATION_MS` | 8000 |
| `PRESENCE_HIGH_DURATION_MS` | 10000 |
| `PRESENCE_CRITICAL_DURATION_MS` | 14000 |
| `PRESENCE_FALLBACK_DURATION_MS` | 7000 |
| `PRESENCE_FADE_IN_MS` | 180 |
| `PRESENCE_FADE_OUT_MS` | 450 |
| `PRESENCE_MAX_TEXT_CHARS` | 320 |
| `PRESENCE_ERROR_BACKOFF_MS` | 2000 |
| `PRESENCE_MAX_BACKOFF_MS` | 30000 |

`SIENA_CORE_PLAYER_NAME` is the sole unprefixed optional setting retained for the user-facing player name. `SIENA_CP_SIENA_CORE_PLAYER_NAME` is accepted as a compatibility alias.

The v0.2 bridge/source/CORS/companion settings remain supported.

## Test and build

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd test
npm.cmd run check:lua
npm.cmd run build
```

All source templates and API payloads are UTF-8. Windows PowerShell 5.1 can display valid UTF-8 JSON as mojibake because of its console encoding; use PowerShell 7, a browser, or Python for byte-accurate checks. Do not add cp1251/latin1 decode-reencode workarounds to the application.

## Manual v0.7 check

1. In the Siena_v2 root start `scripts/start_backend.ps1 -EnableExternalGameReactions -EnableExternalSpeech`. This enables the local contracts but does not start or change a TTS provider by itself.
2. Confirm `GET http://127.0.0.1:8000/api/health` succeeds.
3. Start companion with `scripts/start_backend.ps1 -ReactionProvider siena_core -SienaCoreUrl 'http://127.0.0.1:8000' -VoiceEnabled -TtsUrl 'http://127.0.0.1:8000' -PresenceEnabled`.
4. Start the frontend, press the explicit **Enable voice** control, then run `scripts/start_simulator.ps1 -Scenario v06_voice_normal -Speed 1`.
5. Run the simulator at speed 1 or 2 and observe **queued**, then **Сиена формулирует реакцию…**.
6. Verify bridge/live telemetry continues updating while the request is pending.
7. Verify a short Russian reaction appears without mojibake and does not call the player Siena.
8. Verify Core/model/latency are compact and detailed diagnostics remain collapsible.
9. Verify Siena's normal conversation list/history did not gain a game-reaction turn.
10. Stop Ollama/Siena Core; run the scenario again and verify telemetry still arrives.
11. Verify the live card honestly labels `Template fallback · Core недоступен`.
12. After the configured failure threshold, verify the circuit reports `open` and no request storm occurs.
13. Restart Siena, wait for the reset interval, then trigger one event and verify half-open recovery closes the circuit.
14. Run the fake provider in `delay` mode and end/change session; verify its late response is suppressed and never replaces the live card.
15. Set `SIENA_CP_REACTION_PROVIDER=disabled`; verify the card says reactions are disabled while events remain visible.

16. Run `v05_quick_recovery`; verify a late danger response is suppressed after recovery.
17. Run `v05_priority_replacement`; verify critical queued work replaces weaker queued work.
18. Run `v05_session_change`; verify no reaction from the old session appears in the new one.
19. Run `v05_silence`; verify routine movement/minor changes remain silent while events and scene state stay available.

20. Run the six `v06_voice_*` scenarios and verify priority replacement, stale/session suppression, failure isolation, and no overlap.
21. Open a second frontend tab; verify only the elected tab plays and the backend rejects conflicting playback acknowledgement.
22. Disable voice or omit either opt-in flag; verify text reactions and telemetry continue unchanged with no synthesis.

23. Install and verify the bridge using the exact commands in `CET_INSTALLATION.md`, then set local CET presence `enabled=true` via the unbound **Toggle Siena Presence** action or diagnostic preview/settings.
24. With CET closed, confirm the technical Siena Observer window is absent while the compact presence card can appear.
25. Verify generating, final text, fallback label, browser-driven voice indicator, expiry, critical replacement, stale suppression, backend disconnect/backoff/recovery, six anchors, Cyrillic, `%`, no input capture, and no visible FPS regression.

v0.7 still has no always-on-top desktop window, game control, game-audio ducking, CET audio, Windows TTS fallback, or write into ordinary Siena chat. Ollama/Siena Core and the selected TTS provider remain separately operator-managed processes.

## Known limitations

- History and latches are process-local and reset when the backend restarts.
- Real CET currently proves health and position; other capabilities remain unavailable unless the bridge explicitly reports them.
- Siena Core must be started separately and its external-game endpoint must be explicitly enabled.
- Provider status is cached; routine status/UI polling never performs a network health probe.
- Legacy display-only commands remain for v0.2 API/UI compatibility but never reach the game.
- The simulator is a protocol harness, not game physics, and will not run alongside an active CET source in automatic mode.
