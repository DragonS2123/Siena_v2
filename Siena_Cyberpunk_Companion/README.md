# Siena Cyberpunk Companion v0.4

Read-only companion pipeline for Cyberpunk 2077. It accepts the existing v0.2 CET/RedHttpClient telemetry (or the simulator), converts 4 Hz state into rare normalized events, applies aggregation and cooldowns, and publishes bounded event/reaction history through REST and the existing WebSocket. v0.4 can optionally ask the separately running Siena v2 backend for short text reactions over local HTTP.

It does **not** send commands to Cyberpunk, control the player/NPCs, start Siena/Ollama, or use TTS. The legacy `CompanionCommand` API remains only as a display-only v0.1/v0.2 compatibility surface and is never consumed by the CET mod. Siena Core integration is opt-in and fails back to local templates without interrupting telemetry.

## Pipeline

```text
CET bridge or simulator
  -> POST /api/v1/telemetry/state
  -> StateStore (source/session sequence guard)
  -> StateDiff
  -> EventSynthesizer (capability-aware detectors + session/idle latches)
  -> EventFilter (damage aggregation + per-event cooldown)
  -> ReactionPlanner (priority + cooldown reservation)
  -> bounded priority queue (one asynchronous worker)
  -> Siena Core local HTTP or explicit template fallback
  -> bounded EventBus
  -> REST + existing /ws
  -> React Game Events / Siena Reactions
```

The existing v0.2 bridge remains unchanged. `BridgeRegistry` still negotiates protocol `1.0`, gives live CET priority in `auto`, reports `source_conflict`, and exposes capabilities. A CET capability reported as unavailable is never interpreted as a false game state. Health and position alone are sufficient for the detectors.

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

## Reaction providers

`TemplateReactionProvider` remains the safe default. `SienaCoreReactionProvider` is opt-in. `ReactionPlanner` reserves only mapped meaningful events, applies a 20-second general cooldown and a 60-second same-event cooldown, and lets critical events bypass the general cooldown. The selected event is put into a bounded priority queue; telemetry returns immediately and a single worker performs HTTP calls in order. Critical work can evict lower-priority queued work, while rejected or evicted work is logged explicitly.

The Core request contains a compact event allowlist and a small recent-event window. It omits coordinates and raw telemetry and asks for text only. It cannot choose a model, enable tools/web/TTS, write memory, create a conversation, or invoke specialist routing. Responses are schema-validated, stripped of reasoning/Markdown wrappers, and length-bounded. Reactions record the requested provider, actual provider, model, latency, request id, and any explicit fallback reason.

Transient network/5xx errors get bounded retries. 4xx responses do not. Repeated failures open a circuit breaker; open state refuses network work until the reset interval, then admits one half-open probe. Timeout, malformed/empty output, open circuit, and configuration errors use `template_fallback` when enabled or produce no reaction when disabled. Old-session, ended-session, TTL-expired, and superseded critical responses are suppressed after the call, so a late model response never leaks into the current session.

```powershell
$env:SIENA_CP_REACTIONS_ENABLED='true'
$env:SIENA_CP_REACTION_PROVIDER='siena_core' # siena_core | template | disabled
$env:SIENA_CP_SIENA_CORE_ENABLED='true'
$env:SIENA_CP_SIENA_CORE_BASE_URL='http://127.0.0.1:8000'
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

All responses use Pydantic response models; no internal deque is exposed.

## WebSocket

The existing `/ws` continues sending legacy `state`, `event`, `command`, `status`, and `bridge_status` envelopes. v0.3 additionally sends:

```json
{"type":"game_event","payload":{}}
{"type":"siena_reaction","payload":{}}
```

For compatibility the new envelopes also contain the same object in `data`. Each client owns a bounded queue; a disconnected or slow client cannot block telemetry ingestion. The React client loads REST history first, merges WebSocket updates by unique ID, and caps local history.

## Simulator scenarios

Existing scenarios: `exploration`, `combat`, `critical_health`, `vehicle`, `companion_stuck`, `v03_readonly`, and `v04_siena_core`.

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

The v0.2 bridge/source/CORS/companion settings remain supported.

## Test and build

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd run check:lua
npm.cmd run build
```

## Manual v0.4 check

1. In the Siena_v2 root start `scripts/start_backend.ps1 -EnableExternalGameReactions`.
2. Confirm `GET http://127.0.0.1:8000/api/health` succeeds.
3. Start companion with `scripts/start_backend.ps1 -ReactionProvider siena_core -SienaCoreUrl 'http://127.0.0.1:8000'`.
4. Start the frontend and `scripts/start_simulator.ps1 -Scenario v04_siena_core -Speed 20`.
5. Verify bridge/live telemetry continues updating while a reaction request is pending.
6. Verify the Siena Core Status card says reachable and circuit `closed`.
7. Verify a mapped event produces a short `siena_core` reaction.
8. Verify the reaction diagnostics show model and latency, with no fallback badge.
9. Verify Siena's normal conversation list/history did not gain a game-reaction turn.
10. Stop Siena Core; run the scenario again and verify telemetry still arrives.
11. Verify reactions are marked `template_fallback` with an explicit reason.
12. After the configured failure threshold, verify the circuit reports `open` and no request storm occurs.
13. Restart Siena, wait for the reset interval, then trigger one event and verify half-open recovery closes the circuit.
14. Run the fake provider in `delay` mode and end/change session; verify its late response is not published.
15. Set `SIENA_CP_REACTION_PROVIDER=disabled`; verify events remain visible and no reaction is emitted.

## Known limitations

- History and latches are process-local and reset when the backend restarts.
- Real CET currently proves health and position; other capabilities remain unavailable unless the bridge explicitly reports them.
- Siena Core must be started separately and its external-game endpoint must be explicitly enabled.
- Provider status is cached; routine status/UI polling never performs a network health probe.
- Legacy display-only commands remain for v0.2 API/UI compatibility but never reach the game.
- The simulator is a protocol harness, not game physics, and will not run alongside an active CET source in automatic mode.
