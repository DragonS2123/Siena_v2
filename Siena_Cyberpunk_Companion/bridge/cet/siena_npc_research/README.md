# Siena NPC Runtime Research v0.9.0.1

This is a disabled-by-default, manual-only CET research harness. It is not the production Siena NPC controller and has no backend, HTTP, WebSocket, polling, TTS, or autonomous behavior.

v0.9.0.1 corrects the first live Gate-0 blocker: `IsPreGame` is not a method on the returned player object in this runtime. Session detection now uses `Game.GetPlayer()` plus the nullable, already project-confirmed `Game.GetSystemRequestsHandler():IsPreGame()` path. A valid player is not rejected merely because the optional pre-game getter is unavailable.

## Safety boundary

The only permitted writes are creation/deletion of one entity tagged `SienaNpcResearch`, submission/cancellation of confirmed navigation commands to that entity, and reversible look-at on that entity. The candidate is the temporary generic shell `Character.CitizenRichFemaleCasual`; it is not Siena's final body.

Do not enable this mod until the environment and source evidence in `V0.9.0_SIENA_NPC_RUNTIME_RESEARCH.md` have been reviewed. Installation does not enable it. To conduct a manual test, edit the installed `config.lua`, set `enabled = true`, reload CET mods, and progress through the gates in order. Despawn immediately if the shell appears hostile or enters combat; automatic hostility detection is not claimed.

## Manual hotkeys

- `Siena NPC Research: Probe Runtime` — Gate 0, no spawn.
- `Siena NPC Research: Spawn`, `Despawn`, `Status` — Gates 1–2.
- `Siena NPC Research: Follow`, `Stay`, `Come Here` — Gate 3.
- `Siena NPC Research: Look At Player`, `Clear Look At` — Gate 4.

Idle-animation hotkeys are intentionally absent because no minimal, bounded, dependency-free animation call was confirmed. Every command is wrapped in `pcall`; there is no `onUpdate` handler.

The dedicated `siena_npc_research.log` contains UTC time, player EntityID-derived session marker, gate, command, result, and compact evidence. CET's `scripting.log` receives the same lines when `spdlog` is available.

Use the project PowerShell scripts to install, verify, or uninstall. The game must be closed for install/uninstall. Existing research files are backed up under `<game root>\siena_npc_research_backups`, outside active CET mods.
