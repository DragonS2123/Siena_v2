# Siena Cyberpunk Companion v0.7 — In-Game Companion Presence

## Audited runtime contract

The installed game uses CET v1.37.1 and exposes the working Lua lifecycle already used by Siena: `onInit`, `onUpdate`, `onDraw`, `onOverlayOpen`, and `onOverlayClose`. Local installed mods confirm `GetDisplayResolution`, `ImGui.Begin/End`, `TextWrapped`, wrap-position helpers, window positioning/sizing/background alpha, window font scaling, and the required no-title/no-resize/no-move/no-scroll flags. Optional input flags are checked at runtime instead of assuming every enum member exists.

`TextUnformatted` was not proven by an installed Lua example, so external text is rendered through the proven `TextWrapped` binding only after control filtering, UTF-8-aware clipping, newline limiting, and `%` to `%%` escaping. Reaction text is never used as a window ID or Lua code.

The installed RedHttpClient path and official API confirm asynchronous `AsyncHttpClient.Get(callback, url)`, `HttpResponse.GetStatusCode()`, `GetText()`, and `GetHeader()`. Presence uses HTTP 204 for unchanged revisions and never parses a body for 204. Each request owns a token/proxy/callback so a callback arriving after a client timeout cannot complete a newer request.

The installed CET `NotoSans-Regular.ttf` contains every glyph in `Здоровье критическое. Найди укрытие.`. Runtime atlas inclusion still requires an in-game visual check, so `presence_font_cyrillic_ready` defaults false and no binary font is added to Git.

## Architecture

```text
existing EventBus publications
  -> InGamePresenceProjection + deterministic policy
  -> monotonic process-local snapshot revision
  -> GET /api/v1/in-game-presence/current?after_revision=N
  -> CET PresenceClient (one async GET, session/revision/TTL/backoff guards)
  -> PresenceOverlay (onDraw only, six anchors, fade, no input)
```

The diagnostic Siena Observer window remains gated by CET open/close events. The presence card is a separate renderer that runs during ordinary play only when locally enabled and a current snapshot exists. CET never receives audio, never opens a WebSocket, and never invokes LLM/TTS or game APIs beyond the existing read-only telemetry readers.

## Rollback and safety

- Backend projection defaults `SIENA_CP_PRESENCE_ENABLED=false`.
- CET `presence_settings.json` defaults `enabled=false`.
- Omitting either opt-in preserves v0.6 behavior.
- Polling is loopback-only, bounded to one request, and independent of telemetry transport.
- Audio remains browser-only; `Сиена говорит` appears only after the browser sends `playback_started`.
- Installer backs up the previous mod and preserves user `presence_settings.json`.
- No second EventBus, reaction planner, WebSocket, telemetry response field, protocol requirement, game command, input binding, or binary font was added.

## Validation gates

1. Fake-clock backend projection and REST tests.
2. React REST/WebSocket diagnostics tests.
3. Lua parser and architecture/static safety checks.
4. Full companion, Siena, frontend, Lua and production-build regression.
5. Local fake-presence process smoke.
6. Installer copy/hash/static verification against the named game installation.
7. Live in-game visual/FPS/input/cyrillic verification only while Cyberpunk is actually available.

## Completed validation (2026-07-15)

- Companion backend: 218 passed.
- Siena v2 regression: 477 passed (one upstream Starlette deprecation warning).
- Frontend: 34 passed.
- Production build: passed with 20 transformed modules.
- CET Lua static checks: 11 files passed.
- Fake process smoke: generating, voice synthesis, browser playback acknowledgement, completed reaction, HTTP 204 revision deduplication, and consumer heartbeat passed.
- Installer/verifier: timestamped backup created; installed source SHA256 and static architecture checks passed.
- Live Cyberpunk smoke: not run because `Cyberpunk2077.exe` was not running; the exact 29-step checklist is in `CET_INSTALLATION.md`.
