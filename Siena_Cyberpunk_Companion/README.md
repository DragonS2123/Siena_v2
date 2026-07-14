# Siena Cyberpunk Observer v0.2

An isolated observer that accepts either simulated state or read-only Cyber Engine Tweaks telemetry, turns meaningful changes into events, filters noise, produces deterministic display-only decisions, and renders the result in a standalone React panel.

The v0.2 bridge is read-only. It does **not** create/control NPCs, execute commands in game, modify quests, call Siena_v2, use Siena memory, Ollama, Whisper, or TTS. All backend state is process-local and bounded.

## Architecture

```text
JSON scenario -> Simulator (250 ms) -> POST telemetry -> StateStore
                                                  |-> StateDiff -> EventSynthesizer
                                                  |                -> EventFilter
                                                  |                   -> EventBus -> WS/REST -> React UI
                                                  |                   -> DecisionScheduler -> commands
CET Bridge implements the telemetry producer contract ----------------^
Future Siena Core implements SienaCoreClient (currently disabled) -----^
```

The CET producer is now implemented under `bridge/cet/`, but requires a real game installation and manual runtime verification. See [CET_INSTALLATION.md](CET_INSTALLATION.md), [CET_TROUBLESHOOTING.md](CET_TROUBLESHOOTING.md), and [bridge/cet/README.md](bridge/cet/README.md).

The backend is the source of truth. `StateStore` enforces monotonically increasing sequence numbers within a session. Events live in a bounded ring buffer. Each WebSocket client has a bounded queue and slow clients lose their oldest pending update. The 500 ms damage window is delayed and emitted as one aggregate. Thresholds fire only on boundary crossings. The deterministic scheduler is an architectural stand-in, not an LLM.

## Requirements

- Windows PowerShell 5.1+
- Python 3.12+
- Node.js 20+ and npm

## Install

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\frontend
npm.cmd install
Set-Location ..
```

If the Windows `python` alias is unavailable, replace it with an explicit Python 3.12 executable path.

## Run

Run everything with a scenario:

```powershell
.\scripts\start_all.ps1 -Scenario combat
.\scripts\start_all.ps1 -Scenario companion_stuck -Loop -Speed 2.0
```

Or run each process in its own terminal:

```powershell
.\scripts\start_backend.ps1
.\scripts\start_frontend.ps1
.\scripts\start_simulator.ps1 -Scenario critical_health -Speed 0.5
```

Addresses: backend/API `http://127.0.0.1:8765`, WebSocket `ws://127.0.0.1:8765/ws`, UI `http://127.0.0.1:5173`.

Direct simulator invocation from `simulator/` is also supported:

```powershell
..\.venv\Scripts\python.exe main.py --scenario combat --speed 2.0 --loop
```

Available scenarios are `exploration`, `combat`, `critical_health`, `vehicle`, and `companion_stuck`.

## Test and build

```powershell
Set-Location .\backend
..\.venv\Scripts\python.exe -m pytest
Set-Location ..\frontend
npm.cmd run check:lua
npm.cmd run build
```

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness and version |
| GET | `/api/v1/status` | Session, packet, scheduler, integration status |
| POST | `/api/v1/telemetry/state` | Validate and accept a `GameState`; returns 409 for stale/duplicate sequence |
| GET | `/api/v1/telemetry/latest` | Latest state or `null` |
| GET | `/api/v1/events?limit=200` | Newest-first bounded event history |
| POST | `/api/v1/commands` | Validate/store a manual command |
| GET | `/api/v1/commands/current` | Current non-expired command or `null` |
| WS | `/ws` | State, event, command, and one-second status heartbeat |
| POST | `/api/v1/bridge/hello` | Register version/capabilities and negotiate protocol |
| POST | `/api/v1/bridge/heartbeat` | Refresh liveness and transport diagnostics |
| GET | `/api/v1/bridge/status` | Bridge, source conflict, rate, latency, and capabilities |
| POST | `/api/v1/bridge/disconnect` | Mark a bridge intentionally disconnected |

Invalid JSON/model data returns FastAPI's structured 422 response. Sequence conflicts return 409 with the latest accepted sequence in the detail message.

## WebSocket protocol

Every frame is a JSON envelope:

```json
{"type":"state","data":{}}
{"type":"event","data":{}}
{"type":"command","data":{}}
{"type":"status","data":{}}
```

The UI performs a REST bootstrap, reconnects with capped exponential backoff, and remains usable if the backend disappears. Its local event view is capped at 200 entries.

## Synthesized events

- Session: `game_started`, `game_loaded`, `game_paused`, `game_resumed`, `game_stopped`
- Combat: `combat_started`, `combat_ended`, `enemy_detected`, `enemy_count_changed`
- Player: `player_damaged`, `player_health_below_50`, `player_health_critical`, `player_recovered`, `player_entered_vehicle`, `player_exited_vehicle`
- Companion: `companion_appeared`, `companion_disappeared`, `companion_too_far`, `companion_regrouped`, `companion_stuck`, `companion_health_critical`, `companion_intent_changed`
- Location: `district_changed`

`enemy_detected` is debounced for five seconds per threat key. Rapid player damage is aggregated for 500 ms. Health and distance events are boundary-triggered. A companion is stuck after five seconds without meaningful distance progress while marked moving with `follow` or `regroup` intent.

Scheduler mapping: critical player health -> `retreat`; combat start -> `protect`; companion too far -> `regroup`; combat end -> `follow`. P0 can interrupt a lower-priority active command. Commands expire and manual commands use the same strict schema.

## Configuration

Backend settings use the `SIENA_CP_` prefix, for example `SIENA_CP_PORT=8766`, `SIENA_CP_EVENT_BUFFER_SIZE=500`, `SIENA_CP_COMPANION_TOO_FAR_METERS=25`. The frontend accepts `VITE_API_URL`. Defaults bind only to `127.0.0.1`; CORS allows only the local Vite origins.

## Current limitations

- Memory and state disappear when the backend exits; there is no SQLite database.
- The scheduler is deterministic and commands are displayed but not executed in a game.
- Simulator scenario steps apply discrete patches; it is a protocol/load harness, not a game physics model.
- JSON Schema documents mirror the versioned wire fields; cross-field lifecycle and health invariants are enforced by the Pydantic v2 models.
- This version has no authentication because it is localhost-only.
- CET game calls and actual game launch remain awaiting manual verification.
- Pause and district capabilities are intentionally unavailable in v0.2.

## Future Siena_v2 connection

Implement a real `SienaCoreClient` adapter in the observer process. Pass accepted high-value events and a bounded state snapshot to Siena Core, translate its validated decision into `CompanionCommand`, apply timeouts/circuit breaking, and retain the deterministic scheduler as fallback. Do not import or copy Siena_v2 storage.

## CET bridge scope

The implemented CET producer reads only explicitly supported game facts, maps them to protocol v1 `GameState`, owns a stable session ID and monotonic sequence, and samples at 250 ms. Capability negotiation, reconnect backoff, and a one-state pending buffer are implemented. Game commands and acknowledgements remain deliberately out of scope.
