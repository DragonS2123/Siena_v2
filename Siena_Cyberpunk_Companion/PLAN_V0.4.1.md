# Siena Cyberpunk Companion v0.4.1 delivery plan

## Objective

Polish the working v0.4 text-reaction path without changing the CET bridge, protocol v0.2, telemetry detectors, cooldowns, queue bounds, or process boundaries.

## Delivered behavior

1. The game-channel prompt explicitly identifies Siena as the companion/observer and the player as a separate person addressed as «ты».
2. `SIENA_CORE_PLAYER_NAME` optionally supplies a player name. An empty value omits the name completely; a configured value is allowed only as an occasional form of address.
3. Each Core request receives only the last 3–5 published reactions from the same game session, represented by reaction text and event type. A new session clears this short context.
4. The response sanitizer removes reasoning blocks, fences, technical speaker prefixes, JSON-like responses, excess whitespace, and overlong tails without re-encoding Unicode.
5. The existing `/ws` publishes `reaction_generation_status` for `queued`, `generating`, `completed`, `fallback`, `suppressed`, and `failed` transitions of actually planned reactions.
6. React presents a primary **Live Siena Reaction** card and keeps **Siena Reactions** as collapsible diagnostic history. A fresh critical reaction is held for 10 seconds against weaker updates.

## Boundaries retained

- No CET bridge or protocol v0.2 changes.
- No additional WebSocket, LLM request, telemetry-handler network call, long-memory write, ordinary Siena chat entry, TTS, voice, avatar animation, in-game overlay, always-on-top window, or game command.
- Existing anti-spam cooldown, aggregation, latches, stale suppression, priority queue, and session isolation remain in force.
- Headless backend operation remains supported; the React panel is optional.

## Verification gates

- Siena v2 external endpoint tests, including exact Russian Unicode.
- Full companion backend suite, including persona, optional name, bounded session context, sanitizer, lifecycle, fallback, stale suppression, REST, and WebSocket Unicode.
- Node UI behavior tests for critical hold, session isolation, suppressed lifecycle, reload selection, and Unicode.
- Frontend production build and Lua bridge static check.
- Fake Siena success and fallback smoke without real Ollama.

