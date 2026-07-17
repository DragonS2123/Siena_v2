# Siena Deep Game State research mod

This is an isolated audit harness. It does not import or replace `siena_cyberpunk_observer`, send HTTP, write backend data, run probes automatically, retain game-state history, or call known mutating game methods.

## Install

1. Keep Cyberpunk closed.
2. Copy this whole directory to:
   `B:\SteamLibrary\steamapps\common\Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\siena_game_state_research`
3. Edit the copied `config.lua` and set `enabled = true` only for a research session.
4. Start the game, open CET Bindings, and assign only the probe hotkeys you need. No default bindings are registered.
5. Load a save before probing runtime objects.
6. After research, set `enabled = false`, remove the bindings, or remove only this directory.

## Probes

The v0.8.3 bindings are `probe_equipment_areas`, `probe_cyberdeck_identity`, `probe_cyberdeck_slots`, `probe_quickhack_programs`, and `probe_quickhack_metadata`. Existing general research bindings remain available.

Run the five v0.8.3 probes in that order. They inspect only the bounded `SystemReplacementCW` equipment area and the equipped deck's bounded part/slot collections. `probe_quickhack_metadata` reports static item metadata separately and deliberately does not claim a resolved RAM cost, upload time, duration, cooldown, spread or trace value.

Press one assigned hotkey once while the relevant state is active. Target probing needs a crosshair target; vehicle probing needs a mounted vehicle. The three-second per-probe rate limit rejects accidental repeats. Nil handles and method errors are caught and logged.

## Log

CET writes `spdlog` messages to the individual mod log:

`<Cyberpunk>\bin\x64\plugins\cyber_engine_tweaks\mods\siena_game_state_research\siena_game_state_research.log`

If this CET build does not create an individual log, the same prefixed lines are available in `bin\x64\plugins\cyber_engine_tweaks\scripting.log`. Every successful call is marked `ok`; missing evidence is marked `unavailable`; recovered calls are marked `error`.
