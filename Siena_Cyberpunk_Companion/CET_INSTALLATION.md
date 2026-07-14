# CET Bridge installation

## Dependencies

- A supported legitimate Cyberpunk 2077 installation.
- RED4ext compatible with that game patch.
- redscript.
- Cyber Engine Tweaks.
- RedHttpClient compatible with the installed game/CET/RED4ext versions.

The project does not download any of these binaries. Use their official release pages and confirm version compatibility. RedHttpClient's current documentation lists its exact dependencies and supported versions: <https://github.com/rayshader/cp2077-red-httpclient>.

## Install

Start the observer backend first:

```powershell
cd G:\Siena_v2\Siena_Cyberpunk_Companion
.\scripts\start_backend.ps1
```

Install only the Siena mod:

```powershell
.\scripts\install_cet_bridge.ps1 -GamePath "D:\Games\Cyberpunk 2077"
.\scripts\verify_cet_bridge.ps1 -GamePath "D:\Games\Cyberpunk 2077"
```

The target is:

```text
<game>\bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer
```

An existing Siena bridge is moved to a timestamped backup beside it. No other mod is copied, removed, or modified.

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
5. Check the Cyberpunk Bridge card and optional CET diagnostic overlay.
