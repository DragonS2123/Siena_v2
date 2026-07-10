# Quick Start — Siena v2

Assumes you've already done the one-time setup in [INSTALL.md](INSTALL.md)
(Python venv + `pip install -r requirements.txt`, `npm install`, and pulled
at least `qwen3.5:9b` in Ollama).

## 1. Start Ollama

Make sure Ollama is running (desktop tray app, or `ollama serve` in a
terminal) and has the models you need pulled (`ollama list` to check).

## 2. Start the backend

```powershell
.\scripts\start_backend.ps1
```

This resolves `.venv-faster-qwen3-tts` automatically and starts uvicorn on
`http://127.0.0.1:8000` — no `--reload` (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for why that matters). Leave this
terminal open.

Double-click `start_backend.bat` at the repo root works too (it delegates to
this same script when PowerShell is available, with a portable fallback if
not) — use whichever you prefer.

Confirm it's alive from another terminal:

```powershell
.\scripts\smoke_backend.ps1
```

## 3. Start the UI

In a second terminal:

```powershell
.\scripts\start_ui.ps1
```

This builds the Control Panel UI and launches it as a desktop Electron
window. First run installs `node_modules` automatically if missing.

## 4. Say something

Chat opens by default (configurable in Settings → Startup). Type a message
and press Enter. If the backend or Ollama isn't reachable, the splash
screen's retry button and the sidebar will say so honestly instead of
silently hanging.

## 5. Optional: try voice / OCR / vision / translate / Nucleares

Each of these degrades gracefully if its dependency isn't installed (see
[INSTALL.md](INSTALL.md)'s summary table) — the corresponding button/feature
either doesn't appear or reports why it's unavailable, rather than crashing
chat.

- **Voice**: mic button in the composer (push-to-talk), Speak button under
  any assistant reply.
- **OCR / Vision**: attach an image and ask a question about it.
- **Translate**: Translate button under any assistant reply.
- **Nucleares**: ask Siena something like "что сейчас со станцией?" while
  Nucleares is running locally.

## 6. Change how it looks/behaves

Settings → Appearance (theme/accent/density/font size), Settings → Language
(interface language EN/RU, input/response language preferences) — see
[SETTINGS.md](SETTINGS.md) for the full breakdown of what's real vs.
deferred.

## Running tests / build (for contributors)

```powershell
.\scripts\test_all.ps1
```

Runs `py_compile`, `pytest`, and `npm run build` in sequence — the same
commands used throughout this project's own development passes.

## Something not working?

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the common startup issues
(port already in use, `settings_load_failed`, Ollama unreachable, etc.).
