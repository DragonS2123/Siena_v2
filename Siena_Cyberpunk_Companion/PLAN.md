# Siena Cyberpunk Observer v0.1 — implementation plan

## Scope and boundaries

- Build an isolated Python/TypeScript prototype. No imports, storage, model calls, or runtime coupling to Siena_v2.
- Keep game input and future Siena Core behind explicit interfaces.
- Keep all state in bounded process memory for v0.1.
- Bind services to localhost by default and support local-development CORS only.

## Delivery sequence

1. Define versioned Pydantic models and matching JSON Schema documents.
2. Implement monotonic per-session state storage, state diffing, event synthesis, filtering, bounded event history, WebSocket fan-out, and deterministic scheduling.
3. Expose health, status, telemetry, event, command, and WebSocket endpoints through FastAPI.
4. Add a standalone 250 ms telemetry simulator with five data-driven scenarios, speed control, and looping.
5. Add a resilient React/Vite observer UI with reconnecting WebSocket, REST bootstrap, manual commands, filters, and bounded local event history.
6. Add Windows PowerShell launchers and complete operator/developer documentation.
7. Run backend tests, frontend production build, simulator/API smoke tests, and repair all failures.

## Architecture decisions

- `StateStore` owns latest-state and sequence invariants; one session may restart only with a new `session_id`.
- `StateDiff` is structural and side-effect free. `EventSynthesizer` owns temporal detectors such as companion stuck state.
- `EventFilter` owns debounce, low-priority deduplication, and delayed 500 ms damage aggregation.
- `EventBus` uses a bounded deque plus one bounded queue per WebSocket client; a slow client drops its oldest pending message instead of applying unbounded backpressure.
- `DecisionScheduler` consumes accepted events and emits deterministic, expiring commands. Manual commands pass through the same strict model and replace the current command.
- `SienaCoreClient` is a protocol with a local deterministic implementation; no Siena_v2 call is made.

## Verification gates

- At least the 20 behaviors named in the task have direct pytest coverage.
- `npm run build` succeeds with strict TypeScript checking.
- A live backend accepts simulator telemetry, serves latest state/events, and publishes WebSocket envelopes.
- Git status confirms that only the new project directory was added by this work.
