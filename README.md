# Siena v2

Siena v2 is a local AI companion app: a Python/FastAPI backend running local
Ollama models, with a React/Vite/Electron Control Panel UI. Nothing runs in
the cloud — chat, memory, and settings all stay on your machine.

Current version: **0.2.0** (release candidate, manual RC smoke passed
2026-07-10 — see [RELEASE_NOTES_0.2.0.md](RELEASE_NOTES_0.2.0.md) and
[docs/RELEASE_CHECKLIST_0.2.0.md](docs/RELEASE_CHECKLIST_0.2.0.md) for the
full results).

## Documentation map

| Doc | What it covers |
|---|---|
| [docs/QUICK_START.md](docs/QUICK_START.md) | Fastest path from a fresh clone to a working chat |
| [docs/INSTALL.md](docs/INSTALL.md) | Full dependency list, what's required vs. optional per feature |
| [docs/MODELS.md](docs/MODELS.md) | Which Ollama models Siena uses, by role |
| [docs/VOICE.md](docs/VOICE.md) | STT/TTS engines, providers, voice profiles |
| [docs/SETTINGS.md](docs/SETTINGS.md) | Every Settings screen — what's real, frontend-only, or deferred |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common startup/runtime problems and fixes |
| [docs/KNOWN_LIMITATIONS_0.2.0.md](docs/KNOWN_LIMITATIONS_0.2.0.md) | What's intentionally not implemented yet |
| [Siena v2 Control Panel UI/HANDOFF_v2.md](Siena%20v2%20Control%20Panel%20UI/HANDOFF_v2.md) | The living, detailed developer contract — actual API surface, what's connected vs. not, per-feature implementation notes |

`HANDOFF_v2.md` is the authoritative deep-dive; this README and `docs/` are
the on-ramp.

## Quick start

```powershell
# Backend (one-time setup first — see docs/INSTALL.md)
.\scripts\start_backend.ps1

# UI, in a second terminal
.\scripts\start_ui.ps1
```

See [docs/QUICK_START.md](docs/QUICK_START.md) for the full walkthrough
including first-time setup, and [docs/INSTALL.md](docs/INSTALL.md) for
exact dependency versions and optional-feature install steps (voice,
OCR/vision, translation, Nucleares).

The console REPL in `main.py` also still works for backend-only debugging,
independent of the FastAPI/UI path.

## Architecture, in one paragraph

Electron opens a plain `BrowserWindow` with no IPC bridge — the React UI
talks to the FastAPI backend purely over HTTP (`http://127.0.0.1:8000`) and
one WebSocket (`/ws/trace`) for live tool-trace events. This is a
deliberate simplification, not a stub: don't introduce an Electron IPC
bridge unless a concrete native-only capability (file dialogs, system tray)
actually requires it. Ollama runs every local model; SQLite backs
conversations/memory; JSONL backs logs/trace.

## Testing / building

```powershell
.\scripts\test_all.ps1
```

Or individually:

```powershell
.\.venv-faster-qwen3-tts\Scripts\python.exe -m py_compile api/server.py storage/settings_store.py config.py
.\.venv-faster-qwen3-tts\Scripts\python.exe -m pytest tests -q --basetemp=.pytest_tmp -p no:cacheprovider
cd "Siena v2 Control Panel UI"; npm run build
```

A Vite chunk-size advisory on `npm run build` is expected and non-blocking
— see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## What's implemented

Chat, settings persistence + real UI preferences, real EN/RU localization,
voice (STT/TTS), OCR/vision on attachments, translation, memory + Insights
(human-approved candidate memory), a Resource Manager (Ollama/TTS/Whisper
process visibility), a read-only Nucleares Game Bridge, and a real Debug
diagnostics page. See [docs/KNOWN_LIMITATIONS_0.2.0.md](docs/KNOWN_LIMITATIONS_0.2.0.md)
for what's deliberately not in this release (Games Hub, model-role
configuration, image generation, and a few smaller deferred toggles).

## Contributing / project layout

- `api/server.py` — FastAPI backend, the real API surface (see
  `HANDOFF_v2.md` §3 for the full endpoint table).
- `core/` — routing, agent loop, intent detection.
- `voice/`, `ocr/`, `vision/`, `translator/`, `memory/`, `game/` — feature
  services, each independently optional (see docs/INSTALL.md).
- `storage/` — persisted user data (settings, conversations, memory,
  voice profiles) plus local model caches. Not shipped/committed — see
  `.gitignore`.
- `Siena v2 Control Panel UI/` — the React/Vite/Electron frontend.
- `tests/` — pytest suite (backend). `scripts/` — dev probes/manual test
  scripts plus the `*.ps1` helpers referenced above.

No git history/CI is wired up in this working copy yet; `.gitignore` is in
place so that when this project is committed, local venvs, vendored
binaries, model caches, and generated UI output don't end up in the
repository.
