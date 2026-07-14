# Siena Cyberpunk Companion v0.4 delivery plan

## Objective

Add an optional, read-only Siena Core reaction provider without coupling either project's process lifecycle and without putting model/network latency on telemetry ingestion.

## Delivered architecture

1. Siena v2 owns an explicitly feature-flagged, stateless `/api/external/game-reaction` endpoint.
2. The companion detects and filters game events exactly as in v0.3, then reserves reaction cooldowns.
3. Eligible events enter a bounded priority queue. One background worker owns Core calls.
4. `SienaCoreReactionProvider` sends a compact safe allowlist over local HTTP with bounded timeout and retry policy.
5. A circuit breaker prevents request storms; deterministic templates are an explicit fallback.
6. Session/TTL/supersession guards suppress late results both before and after network work.
7. REST, the existing WebSocket, and React expose cached provider/queue/circuit/fallback diagnostics.

## Safety boundaries

- Read-only telemetry and text reactions; never commands to the game.
- No raw telemetry, coordinates, client-selected model, tools, web, memory, conversation persistence, routing, specialists, attachments, voice, or TTS.
- No automatic Siena/Ollama startup and no cross-project imports.
- Secrets are optional, environment-only, sent as an authorization header, and never logged.
- A provider failure cannot fail the telemetry request or terminate the worker.

## Verification gates

- Existing v0.1-v0.3 companion tests plus v0.4 provider, retry, circuit, queue, stale-result, REST/WS, shutdown, and recovery tests.
- Siena v2 full backend test suite including endpoint feature-flag and statelessness tests.
- Frontend TypeScript/Vite build and Lua bridge static check.
- Fake Siena smoke for success and repeated 500/fallback/open-circuit behavior.
- Optional real Siena smoke only when an already-running Siena health endpoint is reachable; never auto-start it for this check.
