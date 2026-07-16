# Siena Cyberpunk Observer v0.8.1 — CET Bridge

This is a read-only Cyber Engine Tweaks mod. It samples supported player state and sends JSON only to the loopback FastAPI service. It does not request commands, spawn or control NPCs, modify quests, use `Override`, evaluate network responses as Lua, or contact a remote host.

## Modules

- `init.lua`: CET lifecycle, 250 ms scheduler, overlay, and manual reset hotkey.
- `state_reader.lua`: protected live-object reads; no player handle is retained.
- `state_builder.lua`: protocol v1 JSON-safe values and `source="cet"`.
- `capability_detector.lua`: promotes a capability only after a successful protected read.
- `session_manager.lua`: stable world session and monotonic sequence.
- `telemetry_client.lua`: RedHttpClient `AsyncHttpClient`, one request in flight, one latest pending state, heartbeat, timeout detection, and capped reconnect.
- `diagnostics.lua`: rate-limited messages written through CET logging/console.
- `config.lua`: localhost-only endpoint and timing controls.
- `presence_config.lua`: disabled-by-default local overlay settings and explicit JSON persistence.
- `presence_client.lua`: revision-aware asynchronous GET polling, one request token, session/TTL validation and capped backoff.
- `presence_overlay.lua`: six viewport anchors, safe wrapped UTF-8 text, fade and no-input rendering.

## API basis and capability policy

The CET mod structure/events and `Game.GetPlayer()` behavior follow the current [CET Wiki](https://wiki.redmodding.org/cyber-engine-tweaks/first-steps/mod-structure), [event documentation](https://wiki.redmodding.org/cyber-engine-tweaks/cet-functions/events), and [upgrade guide](https://wiki.redmodding.org/cyber-engine-tweaks/upgrade-guide). Transport follows the official [RedHttpClient repository](https://github.com/rayshader/cp2077-red-httpclient), including its `NewProxy`/`HttpCallback.Create` CET example and background `AsyncHttpClient`.

Runtime candidates:

- Position: `Game.GetPlayer()` then `player:GetWorldPosition()`.
- Current health: `Game.GetStatPoolsSystem():GetStatPoolValue(player:GetEntityID(), gamedataStatPoolType.Health, false)`.
- Maximum health: `GetStatPoolMaxPointValue(player:GetEntityID(), gamedataStatPoolType.Health)`.
- Combat: `player:IsInCombat()`.
- Vehicle: `player:GetMountedVehicle() ~= nil`.
- Static stats: `StatsSystem:GetStatValue(entityID, "Level"|"StreetCred"|"Armor"|"PowerLevel"|"Health"|"Memory")` at 5-second intervals.
- RAM: `GetStatPoolValue` and `GetStatPoolMaxPointValue` with `gamedataStatPoolType.Memory` at the existing 250 ms health/RAM interval.
- Weapon: `GetActiveWeaponObject(40)`, falling back to `GetItemInSlot(..., AttachmentSlots.WeaponRight)` while holstered; record identity is `GetItemID().id`.
- Status effects: `StatusEffectHelper.GetAppliedEffects(player)` at 1 Hz with a 32-entry inspection cap.

All calls are wrapped in `pcall` and validated for ordinary finite JSON numbers/booleans. A capability stays false until its call succeeds on a live player. Pause and district remain false because this iteration has no sufficiently verified, version-stable reader. Do not infer combat from visible enemies or vehicle state from speed.

The v0.8.1 calls above were executed successfully by the isolated Siena research mod on 2026-07-16. `GetRecordID()` was proven absent on the weapon object and is forbidden in production; `Game.GetPreventionSystem()` was also proven absent. Exact evidence and sampled values are recorded in `V0.8.1_DEEP_GAME_STATE.md`.

## Request behavior

Sampling occurs at 250 ms from `onUpdate`; networking does not occur every frame. RedHttpClient exposes no public cancel operation, so a request that exceeds five seconds is marked timed out but remains the sole in-flight request until its callback arrives. No second job is started. Incoming samples replace the single pending state. Reconnect uses 0.5, 1, 2, 5, and 10 seconds, repeats hello, and sends only the latest state.

## Diagnostics

The optional ImGui panel shows connection, session, sequence, status, queue count, dropped states, rate, health, position, and capabilities. Repeated logs are rate-limited. CET logs normally live under:

This diagnostic panel is still rendered only between `onOverlayOpen` and `onOverlayClose`. The in-game presence card is a separate renderer: it can draw while CET is closed, contains no technical telemetry, and never exposes controls during ordinary play. Its polling occurs only in `onUpdate`; `onDraw` contains rendering only.

```text
<game>\bin\x64\plugins\cyber_engine_tweaks\scripting.log
<game>\bin\x64\plugins\cyber_engine_tweaks\cyber_engine_tweaks.log
```

RedHttpClient logs live under `<game>\red4ext\logs\redhttpclient-*.log`.

Use the registered CET hotkey `Siena Observer: reset session`, or from CET console:

```lua
GetMod("siena_cyberpunk_observer"):ResetSession()
```

The console form depends on CET returning the mod object; the hotkey is the preferred reset path.
