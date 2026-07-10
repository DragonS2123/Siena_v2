# Troubleshooting — Siena v2 (0.2.0)

## Backend port 8000 already in use

Something else is already bound to `127.0.0.1:8000`, or a previous backend
process didn't exit cleanly.

```powershell
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

Then restart with `scripts\start_backend.ps1`. The frontend has this port
hardcoded (`API_BASE_URL` in `src/api/sienaClient.ts`) — there's no
alternate-port UI setting in 0.2.0.

## "Windows Store Python shim" — wrong interpreter runs

If `python`/`py` on your `PATH` silently opens the Microsoft Store instead
of running Python, or resolves to a different install than the one with
Siena's dependencies, use the venv's own interpreter directly instead of
relying on `PATH`:

```powershell
.venv-faster-qwen3-tts\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

`scripts\start_backend.ps1` already does this automatically (it resolves
`.venv-faster-qwen3-tts\Scripts\python.exe` relative to the repo, not via
`PATH`, and only falls back to `py`/`python` if that venv isn't present).

## `settings_load_failed` in the trace / logs

`storage/settings.json` exists but couldn't be parsed. Two known causes,
both handled gracefully (the backend falls back to defaults and keeps
running — this is never a startup blocker):

- **UTF-8 BOM** — e.g. the file was edited and re-saved in Notepad, which
  writes "UTF-8" as UTF-8-with-BOM by default. Fixed as of this release:
  `storage/settings_store.py` reads with `encoding="utf-8-sig"`, which
  transparently strips a BOM. If you still see this, check which backend
  binary/version is actually running.
- **Genuinely malformed JSON** — open `storage/settings.json` and check for
  a stray trailing comma or unclosed brace. Deleting the file entirely is
  always safe — the backend regenerates it from defaults on the next save.

Confirm it's fixed: restart the backend and check `GET /api/trace/recent`
or `GET /api/logs/recent` for `settings_loaded` instead.

## Ollama not reachable

`GET /api/runtime/status` → `ollama_status.connected: false`. Check:

```powershell
ollama list
```

If that itself fails, Ollama isn't running — start it (desktop tray app, or
`ollama serve`). `config.OLLAMA_HOST` defaults to `http://127.0.0.1:11434`;
if you've moved Ollama to a different host/port, that's a
`GET/POST /api/settings` field (`ollama_host`) but is **not persisted**
across restarts in 0.2.0 (runtime-only) — see
[SETTINGS.md](SETTINGS.md).

## Model not found

The Models screen (and `GET /api/models`) shows install status per model
honestly — `installed` / `missing` / `unknown` — rather than assuming
anything is present. If a role shows `missing`:

```powershell
ollama pull <model-name>
```

See [MODELS.md](MODELS.md) for the full role table and exact model names —
note the translator specifically needs an extra `ollama create` step, not
just `ollama pull` (see [INSTALL.md](INSTALL.md#4-translator--optional)).

## TTS server already running / lost handle

The experimental streaming TTS path manages an external `tts-server.exe`
subprocess. If the backend restarts (or crashes) while that process is
still running, the new backend process loses its handle to it but the old
`tts-server.exe` may still be bound to its port. `GET /api/resources/status`
reports this honestly (`external_processes.tts_server`, including whether
it's backend-managed or an orphaned external process). Use
`POST /api/voice/tts/stop?force=true` to kill it, or `taskkill` it directly
by PID if you're not going through the API.

## AMD GPU: VRAM metrics show "not available"

Runtime's VRAM meter only works when a working `nvidia-smi` is on `PATH` —
it's NVIDIA-only by design. On AMD (or any non-NVIDIA GPU), `vram_supported:
false` with a human-readable reason is correct, expected behavior, not a
bug. CPU/RAM meters (via `psutil`) work regardless of GPU vendor. See
[KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md).

## Nucleares binds to IPv6 `::1`, not `127.0.0.1`

Nucleares' own local webserver may bind to IPv6 loopback only, so plain
`127.0.0.1` can fail to connect even though the game is running and
`localhost` works fine. Siena's bridge already tries `localhost`, `[::1]`,
and `127.0.0.1` in that order, across several common ports
(`8785`/`8786`/`8787`/`8080`/`8000`) — if it still can't connect, confirm
Nucleares' in-game webserver setting is actually enabled, and that nothing
else on your machine is already bound to whichever port it's using.

## `npm run build` shows a chunk-size warning

```
(!) Some chunks are larger than 500 kB after minification...
```

This is a Vite advisory, not a build failure — the build still exits
successfully (`✓ built in ...`). It means the single JS bundle has grown
past Vite's default 500kB warning threshold as more views/hooks/locale data
were added. **Treat the build as passed if this is the only output beyond
`✓ built`.** Actual code-splitting is a deliberate future optimization, not
required for 0.2.0.

## Electron UI cannot reach the backend

The Electron shell is a plain `BrowserWindow` with no IPC bridge — the
renderer talks to `http://127.0.0.1:8000` over plain `fetch()`/WebSocket,
exactly like a browser tab would. If the UI shows "Backend unreachable":

1. Confirm the backend is actually running (`scripts\smoke_backend.ps1`).
2. Confirm nothing is blocking `127.0.0.1:8000` (a firewall rule, another
   process on that port — see the port-in-use section above).
3. If you're running the Vite dev server variant
   (`npm run desktop:dev`), confirm the dev server itself is up at
   `http://127.0.0.1:5173` — Electron's dev-mode window loads that URL
   directly, and the backend's CORS allowlist only accepts that exact
   origin (not `localhost`, which can resolve to a different address on
   Windows).

## Repo is very large / cloning is slow

If you're working from the developer's original working copy rather than a
fresh clone: `.venv-faster-qwen3-tts/`, `.venv-qwen3-tts/`, `external/`,
`storage/models/`, and `node_modules/` together account for the vast
majority of on-disk size (multiple GB each) and are all local
dependencies/caches, never meant to be committed or shipped — see the
release audit report and the repo's `.gitignore`. A fresh clone should not
include any of these; follow [INSTALL.md](INSTALL.md) to regenerate them.
