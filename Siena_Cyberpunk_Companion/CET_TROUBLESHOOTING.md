# CET Bridge troubleshooting

## Where to look

- CET console and `bin\x64\plugins\cyber_engine_tweaks\scripting.log`.
- `bin\x64\plugins\cyber_engine_tweaks\cyber_engine_tweaks.log`.
- `red4ext\logs\redhttpclient-*.log`.
- Backend console and `GET http://127.0.0.1:8765/api/v1/bridge/status`.
- React Cyberpunk Bridge card.
- `GET http://127.0.0.1:8765/api/v1/in-game-presence/status` and the React In-Game Presence card.

Run the non-mutating structure/hash check:

```powershell
.\scripts\verify_cet_bridge.ps1 -GamePath "D:\Games\Cyberpunk 2077"
```

## HTTP results

- `200`: hello, heartbeat, status, or disconnect succeeded.
- `202`: telemetry was validated and accepted. `active=false` means another configured source owns the UI pipeline.
- `204`: presence revision is unchanged; this is a normal poll result and has no body.
- `400`: incompatible protocol hello. Update the bridge/backend pair; do not retry telemetry blindly.
- `409`: duplicate/stale sequence, missing compatible hello, timed-out registration, or mismatched bridge version. Repeat hello; inspect session/sequence before manually resetting.
- `422`: JSON shape or value failed Pydantic validation. Inspect backend detail and never replace unknown fields with random values.
- `500`: backend failure. Stop telemetry, preserve only the latest pending state, and inspect backend logs.

## Common symptoms

### No hello

Confirm backend is running, game was launched with `-no-tls`, the URL is loopback, RedHttpClient is loaded, and its log shows the POST. The bridge will not fall back to a blocking client.

### Connected but no telemetry

Load a save and verify `Game loaded: Yes` in the CET overlay. A session is not created before `Game.GetPlayer()` returns a live player.

### Capability unavailable

This is non-fatal. Open CET console and inspect the related protected reader against the installed game version. Pause and district are intentionally unavailable in v0.2. Do not enable a capability by hardcoding a value.

### Backend restart

The game must remain responsive. After an async callback/failure, the bridge uses capped backoff, repeats hello, keeps its session/sequence, and sends only the latest state. A hung RedHttpClient job remains the only job until callback because the plugin has no public cancellation API.

### Presence does not appear

Confirm both opt-ins: backend `SIENA_CP_PRESENCE_ENABLED=true` and CET local `presence_settings.json` `enabled=true`. Check that the game was launched with `-no-tls`, URL is loopback, current snapshot session matches the bridge session, and `/status` reports a recent poll. Hidden/expired/suppressed/old-session snapshots are intentionally not drawn.

### Cyrillic is squares

The installed `NotoSans-Regular.ttf` contains the Russian test glyphs, but CET's runtime atlas configuration controls what is loaded. Set CET `bin\x64\plugins\cyber_engine_tweaks\config.json` `font.path` to a font you own that includes Cyrillic, restart the game, visually verify the sample, then mark `font_cyrillic_ready=true` in local presence settings. No transliteration or encoding round-trip is used.

### Backend is offline

The last card fades naturally, polling enters capped exponential backoff, and telemetry transport continues independently. Warnings are rate-limited. `show_disconnect_indicator=false` hides player-facing errors by default.

## Manual in-game checklist

1. Start backend.
2. Open React UI.
3. Launch Cyberpunk with `-no-tls`.
4. Open CET console and confirm `mod initialized`.
5. Load a save and confirm hello/session.
6. Compare health in game and UI.
7. Receive damage and confirm `player_damaged` after the 500 ms aggregation window.
8. Move and compare coordinates.
9. Enter/exit a vehicle only if capability is available.
10. Enter/leave combat only if capability is available.
11. Pause/unpause only as an unavailable-capability check in v0.2.
12. Stop backend and confirm the game does not freeze.
13. Restart backend and wait for hello/reconnect without restarting the game.
14. Confirm sequence continues monotonically and stale states are not replayed.
