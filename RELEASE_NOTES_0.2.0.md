# Siena v2 — 0.2.0 Release Notes

## Highlights

Siena v2 0.2.0 is the first release where Settings are genuinely real end
to end, the UI speaks both English and Russian for real, and the Debug page
is an actual diagnostic tool instead of a handful of placeholder tabs. It
also closes out a full release-readiness audit: a `.gitignore` now exists
(none did before), the primary startup script no longer hardcodes a
developer's personal file path, and the visible app version is consistent
everywhere it's shown.

## New features

- **Real UI localization (English/Russian).** A genuine i18n system
  (`src/i18n/`), not a decorative language selector — switch languages live
  from Settings → Language → Interface language, no restart needed. See
  [docs/SETTINGS.md](docs/SETTINGS.md#localization-enru).
- **Debug page is a complete diagnostic tool.** Backend/model/settings
  status, recent errors, a last-request summary (routing, tool calls, done
  reason, duration), tool activity across OCR/Vision/Translator/Voice/
  Nucleares/Insights, and a "Copy/Download debug report" export — built
  entirely from existing trace/log data, with no secrets or full
  conversation history in the export.
- **Full Settings persistence.** Appearance (theme/accent/font size/
  density), timestamps, typing animation, copy-before-clear, code
  rendering preferences, tool permissions, and language preferences all
  persist to `storage/settings.json`, survive a restart, and apply live.
  See [docs/SETTINGS.md](docs/SETTINGS.md) for exactly what's real vs.
  frontend-only vs. deferred.

## Fixed

- `start_backend.bat` no longer hardcodes a developer-specific Python path
  and no longer uses `--reload` by default (portable now, delegates to the
  new `scripts/start_backend.ps1`, with a graceful inline fallback if
  PowerShell isn't available).
- The Settings sidebar showed a hardcoded, stale `v0.9.4-beta` string
  disconnected from the real app version — it now reads the same
  `APP_VERSION` source as the Developer Settings About card, Debug
  Overview, and Debug export report.
- Removed a stale duplicate `HANDOFF_v2 — копия.md` that contradicted the
  real, current `HANDOFF_v2.md` (e.g. claimed settings weren't persisted).

## Changed

- Version bumped from `0.0.1` to **`0.2.0`** (`package.json` /
  `package-lock.json` — the single canonical source; every visible version
  display reads from it via one `APP_VERSION` constant, not a second
  hand-maintained one).
- Code rendering's Apply-patch button was **removed** rather than kept
  fake — Siena has no file-editing target for a patch to apply to.
- Tool permissions' File system / Network / Memory toggles were **removed**
  (they never gated anything real) and replaced with a read-only card
  listing the backend's actual registered tools.
- Added a `.gitignore` (none existed before) covering local venvs,
  `external/` (vendored whisper.cpp/qwentts.cpp binaries+models),
  `storage/models/` (downloaded model caches), `node_modules/`, and
  leftover browser smoke-test profiles.

## Known limitations

Full detail in [docs/KNOWN_LIMITATIONS_0.2.0.md](docs/KNOWN_LIMITATIONS_0.2.0.md).
Headlines, stated plainly:

- **Games Hub does not exist in this release.** It's future work with no
  UI entry point and no backend support.
- **Siena does not control or write to Nucleares.** The Nucleares Game
  Bridge is read-only telemetry only — she can mention live station state
  in chat when asked, but issues no commands and has no polling daemon.
  This is Nucleares game-simulation telemetry, not real-world nuclear
  guidance.
- **Model-role configuration isn't productized.** You can't yet swap which
  model handles code/OCR/vision/translation from Settings — the one real
  exception is manually switching the *active chat* model between
  `qwen3.5:9b` and `qwen3.5:27b` in the Models screen.
- **No image generation** — not integrated, not started.
- Launch-at-login, startup preload/warmup — deferred (would require an
  Electron IPC/preload bridge that deliberately doesn't exist yet).
- AMD GPU users won't see VRAM metrics in Runtime — the meter is
  NVIDIA-only (`nvidia-smi`-based) by design; CPU/RAM metrics work
  regardless of GPU vendor.
- Deep content of Runtime, Debug's technical data, Models, Memory,
  Insights, Logs, and Tool Trace views is not localized yet — English only.
- **No installer.** This release is source + local venv/npm setup only —
  packaging an installer is explicitly a separate, later pass, not part of
  0.2.0.

## Upgrade / run notes

No breaking settings-schema changes — an existing `storage/settings.json`
from an earlier working copy loads as-is, with safe defaults for any new
fields. See [docs/QUICK_START.md](docs/QUICK_START.md) for how to start the
backend/UI, and [docs/INSTALL.md](docs/INSTALL.md) for dependency setup
(Python 3.12, Node 20+, Ollama, and the optional voice/OCR/vision/
translator/Nucleares dependencies).

## Validation status

- `py_compile api/server.py storage/settings_store.py config.py` — passed
- `pytest tests -q --basetemp=.pytest_tmp -p no:cacheprovider` — **200 passed**
- `npm run build` — passed, with the expected non-blocking Vite chunk-size
  advisory (bundle >500kB after minification — see
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md); not a build failure)
- **Manual RC smoke: PASSED (2026-07-10).** Backend start/stop
  (`start_backend.bat`, `stop_backend.bat`, `scripts\start_backend.ps1`), UI
  startup, Chat, Settings persistence, EN/RU UI language switching, Voice
  (TTS/STT), Attachments/OCR/Vision, Translator, Debug, and the Nucleares
  read-only bridge (with Nucleares running) were all verified against a real
  running backend + Electron UI, version confirmed visible as **0.2.0**
  everywhere it's shown, and no `settings_load_failed` observed across a
  normal restart. Full item-by-item results in
  [docs/RELEASE_CHECKLIST_0.2.0.md](docs/RELEASE_CHECKLIST_0.2.0.md).

See [docs/RELEASE_CHECKLIST_0.2.0.md](docs/RELEASE_CHECKLIST_0.2.0.md) for
the full manual RC smoke checklist run before shipping this.
