# Siena Cyberpunk Observer v0.2 — Read-only CET Bridge plan

## Baseline

- v0.1 backend baseline: 29 pytest tests pass.
- v0.1 frontend baseline: strict TypeScript and Vite production build pass.
- Existing telemetry/event/scheduler pipeline remains authoritative and is extended, not replaced.
- No CET, NativeDB, or game typedef files were found in the accessible workspace. Game-specific calls therefore require both primary-source support and a live in-game verification before a capability can be called proven.

## Verified API basis

- CET loads each mod through `init.lua`, supports `onInit`, `onUpdate`, `onDraw`, and `onShutdown`, and permits calling `Game.GetPlayer()` during update without retaining a stale player handle.
- Player availability and `player:GetWorldPosition()` are supported by current CET documentation/examples.
- RedHttpClient exposes `AsyncHttpClient.Post(callback, url, body, headers)`. Its official CET example constructs the callback with `NewProxy` and `HttpCallback.Create`; the implementation uses a background `JobQueue`.
- Local plain HTTP requires Cyberpunk to be started manually with `-no-tls`. The bridge itself additionally restricts its URL to `127.0.0.1` or `localhost`.
- Health readers use read-only stat pool/stat system methods behind protected calls and runtime capability detection. They remain "awaiting live verification" until tested against the installed game/CET/RedHttpClient versions.
- Position uses `player:GetWorldPosition()` behind protected calls and likewise requires a live check.
- Combat, vehicle, pause, and district are deliberately unavailable in this iteration: no enemy/speed heuristics and no unverified API calls.

## Backend work

1. Extend `GameState` compatibly with `source` (default `simulator`) and optional `bridge_version`.
2. Store monotonic sequences independently per source/session and expose only a selected active source.
3. Add bounded `BridgeRegistry`, protocol compatibility checks, five-second liveness, capabilities, telemetry rate/latency, errors, and source-conflict reporting.
4. Add hello, heartbeat, status, and disconnect routes. Publish `bridge_status` over the existing bounded WebSocket bus.
5. Keep simulator fully functional and make CET the default priority in automatic source selection; support `SIENA_CP_TELEMETRY_SOURCE=auto|cet|simulator`.

## CET bridge work

1. Keep config, state reading, state building, capability detection, session state, transport, and diagnostics in separate Lua modules.
2. Sample at 250 ms from `onUpdate`, never network on every frame.
3. Permit one request in flight. Keep exactly one pending state (`latest-state-wins`) and count replaced pending states.
4. Heartbeat every two seconds when idle, detect request timeout at five seconds, and reconnect with 0.5/1/2/5/10-second capped backoff.
5. Repeat hello after reconnect, keep sequence monotonic for a loaded-world session, and start a new session only on a stable unavailable-to-available transition or manual debug reset.
6. Provide rate-limited diagnostics and an optional read-only ImGui overlay. Release references/proxies on shutdown.

## Installation and documentation

- Install only the Siena CET directory after validating game/CET/RedHttpClient layout; back up an older Siena bridge directory.
- Uninstall only that exact directory. Never fetch binaries or edit launch options/executables.
- Document dependencies, `-no-tls`, logs, console checks, status codes, troubleshooting, and an honest in-game validation checklist.

## Verification gates

- All v0.1 tests plus at least 15 v0.2 backend cases pass.
- Frontend strict production build passes.
- Lua parses with an available Lua compiler if installed; otherwise run structural/static checks for balanced source, accidental globals, blocking loops/clients, per-frame networking, and shutdown cleanup.
- PowerShell scripts parse and pass non-mutating negative/verification-path checks.
- No claim of successful game integration is made without launching Cyberpunk 2077.
