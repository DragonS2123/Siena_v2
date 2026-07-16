# CET Bridge installation

## Dependencies

- A supported legitimate Cyberpunk 2077 installation.
- RED4ext compatible with that game patch.
- redscript.
- Cyber Engine Tweaks.
- RedHttpClient compatible with the installed game/CET/RED4ext versions.

The project does not download any of these binaries. Use their official release pages and confirm version compatibility. RedHttpClient's current documentation lists its exact dependencies and supported versions: <https://github.com/rayshader/cp2077-red-httpclient>.

## Install

Start the observer backend first. Presence remains disabled unless `-PresenceEnabled` is supplied:

```powershell
cd G:\Siena_v2\Siena_Cyberpunk_Companion
.\scripts\start_backend.ps1 -PresenceEnabled
```

Install only the Siena mod:

```powershell
.\scripts\install_cet_bridge.ps1 -GamePath "B:\SteamLibrary\steamapps\common\Cyberpunk 2077"
.\scripts\verify_cet_bridge.ps1 -GamePath "B:\SteamLibrary\steamapps\common\Cyberpunk 2077"
```

The target is:

```text
<game>\bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer
```

An existing Siena bridge is moved to a timestamped backup beside it. `presence_settings.json` is restored into the new code directory, so user anchor/margin/opacity/font/toggle settings survive an update. The installer prints every installed code file. No other mod is copied, removed, or modified.

## Enable and configure presence

Presence has two independent opt-ins: backend `-PresenceEnabled` and CET local `enabled`. The CET default is false. Register **Toggle Siena Presence** without a default key, then assign a non-conflicting binding in CET, or open the diagnostic Siena Observer window and use its preview/settings controls. Saving writes only `presence_settings.json` on explicit action; there is no per-frame file I/O.

Default local settings are top-right, margins 32/48, width 520, alpha 0.72 and font scale 1.0. Six anchors are supported. `font_path` is documented metadata because this CET build exposes font selection through CET's own `config.json`; set CET `font.path` to an existing user font when the runtime atlas lacks Cyrillic. Do not copy a system font into this repository.

## Local HTTP and `-no-tls`

RedHttpClient normally accepts TLS endpoints. Its official local-development mode requires launching Cyberpunk manually with:

```text
-no-tls
```

for `http://127.0.0.1:8765`. Add this yourself in Steam/GOG/Epic launch settings or a personal shortcut. The scripts intentionally do not edit launch options. Never combine `-no-tls` with an external backend URL; the Siena bridge rejects every non-loopback URL anyway.

## Uninstall

```powershell
.\scripts\uninstall_cet_bridge.ps1 -GamePath "D:\Games\Cyberpunk 2077"
```

Confirm the prompt. The script validates Siena's manifest before removing the exact Siena directory. Timestamped backups are left untouched for manual review.

## Startup order

1. Start backend.
2. Start React UI with `scripts\start_frontend.ps1`.
3. Launch Cyberpunk with `-no-tls`.
4. Open CET overlay/console and load a save.
5. Assign/trigger **Toggle Siena Presence** or enable preview in the CET-only diagnostic window.
6. Close CET and confirm the diagnostic window disappears independently of the compact presence card.

## v0.7 manual live-smoke checklist

This is the final real-game validation; automated tests never start Cyberpunk, Ollama, or real TTS.

1. Start Ollama.
2. Start Siena v2 with external game reactions and speech enabled.
3. Start the companion backend with Siena Core, Voice, and Presence enabled.
4. Start the React frontend.
5. Click **Enable voice** in the frontend.
6. Enable CET presence locally (`enabled=true` or the unbound **Toggle Siena Presence** hotkey).
7. Install and verify the updated CET bridge.
8. Launch Cyberpunk with the local HTTP option described above.
9. With CET closed, confirm the diagnostic Siena Observer window is absent.
10. Produce a meaningful game event.
11. Confirm the compact generating message appears.
12. Confirm it is replaced by the completed reaction.
13. Confirm audio is played only by the browser.
14. Confirm the speaking label exists only during actual browser playback.
15. Confirm the card expires and fades away.
16. Produce a critical-health event.
17. Confirm critical replaces a weak card.
18. Heal before an older slow reaction completes.
19. Confirm that stale reaction never appears.
20. Stop the companion backend.
21. Confirm the game remains responsive and the CET log is not flooded.
22. Restart the backend.
23. Confirm presence polling recovers.
24. Open CET.
25. Confirm the diagnostic window and presence card remain independent.
26. Check layout at 1920x1080 and the current user resolution.
27. Check Cyrillic, quotes, dash, newlines, and `%` rendering.
28. Confirm the card captures no mouse, keyboard, or navigation input.
29. Compare FPS before and after at the default 500 ms polling interval.
