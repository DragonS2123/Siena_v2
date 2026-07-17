# Siena NPC Architecture

This document began with the v0.9.0 research boundary; v0.9.1 adds the first narrow production presence path without relaxing it.

v0.9.1 now implements the first production presence path while preserving that boundary:

```text
Siena Core
  -> reaction / decision layer (no NPC queue access in v0.9.1)

Manual operator UI
  -> deterministic command policy
  -> bounded backend NPC command queue
  -> CET NPC controller
  -> single tagged NPCPuppet
```

The sole v0.9.1 queue producer is the manual frontend route. Siena Core, reactions and the LLM cannot enqueue, modify or parameterize NPC commands. The queue accepts a closed enum without game identifiers; the CET controller owns all fixed records, targets, transforms and game calls.

```text
Siena Core (LLM reasoning and conversation)
    -> Companion backend (owns session and policy context)
        -> bounded NPC command protocol (typed allowlist, validation, expiry)
            -> in-game NPC controller (deterministic executor)
                -> Siena entity / gamePuppet
                    -> movement, gaze, bounded animation
```

## Responsibility split

### Siena Core

The model may propose a high-level companion intent such as follow, stay, come-here, acknowledge, or speak. It never receives an arbitrary method name, record ID, entity ID, TweakDB path, position, script, or game-object handle to execute. It cannot issue combat, player-control, quest, inventory, health, vehicle, teleport, spawn-count, or persistence operations.

### Deterministic safety policy

Policy converts or rejects proposed intent before it reaches the game. It owns the finite allowlist, cooldowns, session binding, one-entity invariant, state prerequisites, command expiry, and emergency disable. It rejects unknown fields and commands; denial is the default. Model text cannot override policy.

### Companion backend

The backend authenticates the known local bridge, validates protocol/schema/version, binds commands to the current observed game session, deduplicates sequence/command IDs, and exposes compact acknowledgements. It must not expose a generic RPC endpoint or accept Lua/redscript snippets. Voice/TTS remains a separate presentation path: speaking must never imply permission to move or mutate game state.

### Bounded NPC command protocol

A future protocol should be a closed tagged union, for example `follow`, `stay`, `come_here`, `look_at_player`, `clear_look_at`, `play_idle(id)`, and `stop_animation`, only after each operation has passed research gates. Parameters must be server-selected enums and bounded numeric ranges; coordinates and entity targets must not cross the boundary. Every command carries session ID, command ID, expiry, and expected controller state.

### In-game NPC controller

The game-side controller revalidates player/session presence, `IsPreGame`, DynamicEntitySystem readiness, the unique Siena tag, resolved runtime class, and command applicability. It calculates safe local destinations from the player using confirmed helpers, submits only compiled allowlisted calls, stores at most one movement and one bounded animation handle, and owns cancellation and deletion. It never scans the world and never controls the player.

### Siena entity

Entity ownership is explicit: one stable tag, non-persistent spec until persistence has a separately proven design, no player or quest record, and no global TweakDB or faction mutation. Appearance assets are data selected during build, not runtime-generated global records. A future female-V-derived body must be exported as a standalone NPC-compatible resource and tested as an `NPCPuppet`; the actual PlayerPuppet is never duplicated.

## State machine

```text
disabled
  -> session_ready
  -> spawn_requested
  -> spawned_validating
  -> idle <-> following / moving / looking / animating
  -> cleanup_requested
  -> absent

Any failed prerequisite or session loss -> cleanup_requested -> absent
Any unknown entity/runtime class/hostility -> immediate reject and cleanup
```

Transitions are deterministic and idempotent. Spawn while present is rejected. Despawn while absent succeeds as a no-op. A movement replacement cancels the prior movement command. Session change invalidates all pending commands. Restoration, if ever allowed, must be an explicit controller policy—not an LLM decision and not a saved dynamic spawn side effect.

## Failure containment and observability

Every runtime call is caught; failures produce compact structured status without raw objects or world dumps. Logs bind operation, gate, session marker, stable entity ID, runtime class, managed/spawning/spawned state, selected candidate, and cleanup result. A circuit breaker disables execution after unsafe runtime evidence. The controller must remain uninstallable independently of the production observer.

The v0.9.0 harness intentionally has no backend protocol. It establishes whether the final game-side executor can be CET-only. A minimal redscript service is justified only for a capability that cannot be safely called or owned in CET, such as lifecycle listeners or a bounded animation service; it must expose typed methods, not arbitrary dispatch.
# v0.10.0 integrated architecture

The v0.9.1 transport remains intact, but `state_machine.lua` is now the only body
behavior owner. `npc_entity.lua` is its narrow live-confirmed game API adapter;
`npc_client.lua` is the single RedHttpClient command/result channel; and
`subtitle_overlay.lua` is a replaceable bounded screen-space renderer. Runtime
spawns only `Siena.SienaCompanion`, which is supplied by the packaged TweakXL
source and resolves only to `siena\entities\siena_default.ent` with requested
appearance `siena_default`. The adapter fails closed with
`standalone_record_unavailable` instead of falling back to the generic citizen;
the backend and LLM cannot supply a record or appearance. See
`V0.10.0_SIENA_EMBODIED_COMPANION.md`,
`SIENA_APPEARANCE_PIPELINE.md`, and `SIENA_EMBODIMENT_SAFETY_MODEL.md`.
