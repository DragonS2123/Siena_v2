# Siena Cyberpunk Companion v0.5 implementation plan

## Scope and invariants

v0.5 adds deterministic scene interpretation and natural reaction selection without replacing the v0.4.1 telemetry, event synthesis/filtering, planner, bounded queue, Siena Core provider, REST/WebSocket, or React paths. The bridge remains protocol-compatible and read-only. No TTS, overlay, commands to the game, chat integration, model routing, or long-term memory is added.

## Implemented flow

```text
normalized GameEvent (legacy aliases retained on the wire)
  -> SceneEventNormalizer (semantic alias collapse)
  -> SceneContextBuilder (bounded current/history, phase, health trend, severity)
  -> CompanionBehaviorPolicy (deterministic silence/focus/budget)
  -> ReactionOpportunity
  -> existing ReactionPlanner technical cooldown
  -> existing bounded priority queue / provider / fallback
  -> existing EventBus / REST / WebSocket / React
```

## Delivery checklist

- Strict scene, health-trend, focus, opportunity, update, and suppression models.
- Same-frame legacy/normalized semantic deduplication without removing either wire event.
- Process-local, session-scoped scene state with idle-gap/max-duration/significant-transition boundaries.
- Per-scene reaction budget, minimum interval, critical bypass, optional resolution, and structured suppression logs.
- Critical queued-work replacement plus post-generation session/scene/revision relevance checks.
- Compact scene-aware prompt and deterministic focus-aware template fallback.
- `GET /api/v1/scenes/current`, filtered `GET /api/v1/scenes`, and meaningful `scene_context_updated` WebSocket messages.
- Compact Current Scene panel and phase/focus on Live Siena Reaction.
- Five deterministic v0.5 simulator scenarios and at least 50 v0.5 automated cases.
- Feature flag `SIENA_CP_SCENE_ENABLED=false` returns dispatch to the unchanged v0.4.1 event path.

## Validation gates

1. Companion backend tests and compile check.
2. Frontend unit tests and production build.
3. CET Lua static check.
4. Siena v2 regression suite for the external game endpoint.
5. Headless simulator smoke against the actual FastAPI app, followed by optional live Siena Core smoke when that independent process is available.
