# Siena Cyberpunk Companion v0.3

Read-only companion pipeline for Cyberpunk 2077. It accepts the existing v0.2 CET/RedHttpClient telemetry (or the simulator), converts 4 Hz state into rare normalized events, applies aggregation and cooldowns, creates optional template text reactions, and publishes bounded history through REST and the existing WebSocket.

It does **not** send commands to Cyberpunk, control the player/NPCs, call Siena Core/Ollama, or use TTS. The legacy `CompanionCommand` API remains only as a display-only v0.1/v0.2 compatibility surface and is never consumed by the CET mod.

## Pipeline

```text
CET bridge or simulator
  -> POST /api/v1/telemetry/state
  -> StateStore (source/session sequence guard)
  -> StateDiff
  -> EventSynthesizer (capability-aware detectors + session/idle latches)
  -> EventFilter (damage aggregation + per-event cooldown)
  -> ReactionPlanner (template or disabled, priority + cooldown)
  -> bounded EventBus
  -> REST + existing /ws
  -> React Game Events / Siena Reactions
```

The existing v0.2 bridge remains unchanged. `BridgeRegistry` still negotiates protocol `1.0`, gives live CET priority in `auto`, reports `source_conflict`, and exposes capabilities. A CET capability reported as unavailable is never interpreted as a false game state. Health and position alone are sufficient for the v0.3 detectors.

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
.\scripts\start_simulator.ps1 -Scenario v03_readonly -Speed 20
```

Or launch all three:

```powershell
.\scripts\start_all.ps1 -Scenario v03_readonly -Speed 20
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

## Reactions

`TemplateReactionProvider` is the default. It reacts only to mapped meaningful events, not every event. `ReactionPlanner` considers priority, a 20-second general cooldown and 60-second same-event cooldown; critical events bypass the general cooldown. `DisabledReactionProvider` creates no reactions. Reactions are text-only and use [protocol/siena_reaction.schema.json](protocol/siena_reaction.schema.json).

```powershell
$env:SIENA_CP_REACTIONS_ENABLED='true'
$env:SIENA_CP_REACTION_PROVIDER='template' # template | disabled
```

No provider imports Siena_v2 or starts an external model.

## REST API

Existing endpoints remain intact. v0.3 adds:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/events` | `limit`, `event_type`, `severity`, `session_id` filters |
| GET | `/api/v1/events/latest` | latest matching event or `null` |
| GET | `/api/v1/reactions` | `limit`, optional `event_type` |
| GET | `/api/v1/reactions/latest` | latest matching reaction or `null` |
| GET | `/api/v1/planner/status` | enabled/provider/counts/timestamps/cooldown |

All responses use Pydantic response models; no internal deque is exposed.

## WebSocket

The existing `/ws` continues sending legacy `state`, `event`, `command`, `status`, and `bridge_status` envelopes. v0.3 additionally sends:

```json
{"type":"game_event","payload":{}}
{"type":"siena_reaction","payload":{}}
```

For compatibility the new envelopes also contain the same object in `data`. Each client owns a bounded queue; a disconnected or slow client cannot block telemetry ingestion. The React client loads REST history first, merges WebSocket updates by unique ID, and caps local history.

## Simulator scenarios

Existing scenarios: `exploration`, `combat`, `critical_health`, `vehicle`, `companion_stuck`.

`v03_readonly` covers a new session, movement, multiple small hits, strong damage, low/critical thresholds, healing, combat/vehicle transitions, 60 seconds idle, movement after idle, and then exits. Backend timeout subsequently creates `session_ended`. At `-Speed 20`, the complete scenario plus disconnect timeout is convenient for manual verification.

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

The v0.2 bridge/source/CORS/companion settings remain supported.

## Test and build

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm.cmd run check:lua
npm.cmd run build
```

## Manual v0.3 check

1. Start backend and frontend.
2. Start `v03_readonly -Speed 20`, or start Cyberpunk with the CET bridge.
3. In the UI verify bridge/live telemetry remains visible.
4. Take damage (or let the scenario run) and verify one aggregated `player_damaged` event.
5. Verify `health_low` and `health_critical` appear only on threshold entry.
6. Verify a mapped Siena text reaction appears without audio/model startup.
7. Keep health below the threshold and confirm events do not repeat each telemetry frame.
8. Stop telemetry and wait 10 seconds; verify `session_ended` through REST/UI.

## Known limitations

- History and latches are process-local and reset when the backend restarts.
- Real CET currently proves health and position; other capabilities remain unavailable unless the bridge explicitly reports them.
- Template reactions are deterministic and intentionally small; there is no Siena Core adapter in v0.3.
- Legacy display-only commands remain for v0.2 API/UI compatibility but never reach the game.
- The simulator is a protocol harness, not game physics, and will not run alongside an active CET source in automatic mode.
