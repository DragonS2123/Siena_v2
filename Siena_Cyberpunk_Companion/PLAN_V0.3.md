# Siena Cyberpunk Companion v0.3 plan

## Scope

Read-only telemetry interpretation only. Preserve the v0.2 CET bridge and protocol, keep simulator support, and add normalized events plus optional text reactions. No game commands, NPC/player control, Siena Core requirement, Ollama startup, or TTS.

## Reused v0.2 components

- `GameState` and strict Pydantic wire validation
- `StateStore` per-source monotonic sequence enforcement
- `StateDiff` structural comparison
- `EventSynthesizer` temporal detector ownership
- `EventFilter` delayed damage aggregation and debounce ownership
- `EventBus` bounded history/client queues and the existing `/ws`
- `BridgeRegistry` capability negotiation, liveness, source selection and conflict diagnostics
- `DecisionScheduler` and command endpoints only for backward-compatible display-only behavior
- React reconnect/bootstrap hook and existing telemetry/bridge cards
- data-driven JSON simulator and PowerShell launch scripts

## Delivered stages

1. Extend `GameEvent` with severity, source sequence and summary while retaining v0.2 fields.
2. Add session, health, optional combat/vehicle, idle and movement detectors with capability guards and injected time.
3. Configure damage aggregation, per-event cooldowns, latches, bounded histories and structured lifecycle logs.
4. Add template/disabled providers and a priority/cooldown `ReactionPlanner`.
5. Add typed event/reaction/planner REST endpoints and `game_event`/`siena_reaction` on the existing WebSocket.
6. Add React Game Events and Siena Reactions sections with REST/WS ID deduplication and unavailable-capability rendering.
7. Add `v03_readonly`, active-CET simulator preflight, regression tests, schemas and operator documentation.

## Verification gates

- Full v0.1/v0.2/v0.3 pytest suite passes without real sleeps.
- Strict TypeScript/Vite production build passes.
- Lua static/parser check continues to pass without modifying the working bridge.
- Simulator/backend smoke produces normalized events and template reactions while all histories remain bounded.
