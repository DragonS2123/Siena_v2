# Known Limitations — 0.2.0

Honest list of what's intentionally not done yet, so nothing here should be
mistaken for a bug. If it's not listed here or in a screen's own "deferred"
labeling, it's expected to work.

## Not implemented yet

- **Games Hub** — future work, not part of 0.2.0 at all. No UI entry point,
  no backend support.
- **Model-role configuration** — per-role model selection (letting a user
  swap the code model, OCR model, etc. for a different installed model) is
  not productized yet. The one real exception: manually switching the
  *active chat* model between `qwen3.5:9b` and `qwen3.5:27b` via the Models
  screen. See [MODELS.md](MODELS.md).
- **Image generation** — no provider/model integrated. Not started.
- **Launch at login** — deferred. Would need a main-process Electron API
  (`app.setLoginItemSettings`) reachable only via an IPC/preload bridge,
  which `electron/main.cjs` deliberately doesn't have (no preload script,
  `contextIsolation` on, `nodeIntegration` off, sandboxed). Adding one just
  for this toggle was judged out of scope.
- **Startup preload / warmup** — deferred, same reason. Splash readiness
  already uses real backend/runtime/conversation/model/settings checks, so
  there's no fake warmup step being hidden — the feature itself (pre-warming
  a model before first use) just isn't built.
- **Apply-patch button** (Code rendering) — removed rather than faked.
  Siena has no file-editing target for a patch to apply to.

## Read-only / partial by design

- **Nucleares Game Bridge is read-only telemetry only.** Siena reads the
  local Nucleares webserver over HTTP and can mention live station state in
  chat when you ask about it — she never writes to the game, never issues
  commands, and this is Nucleares **game-simulation** telemetry, not
  real-world nuclear operation guidance.
- **No async Nucleares polling daemon.** Telemetry is fetched on-demand,
  per relevant chat message — not continuously polled in the background.
- **Runtime AMD VRAM metrics are incomplete.** The VRAM meter only works
  with a working `nvidia-smi` on `PATH` (NVIDIA-only). On AMD/other GPUs,
  the UI honestly shows "not available" with a reason instead of a fake
  number — this is expected, not a bug (see
  [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).
- **Some Debug/Runtime data is technical and unlocalized.** Tool names,
  trace event names, and raw JSON payloads in the Debug page and Tool Trace
  view are shown as-is (not translated) — they're diagnostic data, not UI
  copy.

## Localization (EN/RU)

Real for the areas explicitly localized this pass: main sidebar, Settings
sidebar and all 8 Settings screens, chat composer placeholders, common
message action buttons, code block actions, Debug page labels. **Not yet
localized**: deep content of Runtime, Debug (technical data only, see
above), Models, Short/Long Memory, Insights, Logs, and Tool Trace views —
these remain English-only in 0.2.0. See [SETTINGS.md](SETTINGS.md).

## Frontend build

`npm run build` currently emits a Vite chunk-size advisory (bundle >500kB
after minification). This is a non-blocking warning, not a failure — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md). Code-splitting is a future
performance pass, not a 0.2.0 blocker.

## Non-blocking repo cleanup (not deleted, no explicit instruction to remove)

- `NEXTDO.md` — a stale, self-dated (2026-07-07) point-in-time audit
  comparing code against `HANDOFF.md`; some of its findings (e.g. "no
  Insights tab") are no longer true. Left as historical record.
- `requirements-before-amd-test.txt` — a UTF-16 pip-freeze snapshot from
  before the AMD migration, superseded by the curated `requirements.txt`.
- `test_glm_ocr_direct.py`, `test_vision_direct.py` (repo root) — ad-hoc
  manual debug scripts with a hardcoded personal picture path; not part of
  the pytest suite and never run by `scripts\test_all.ps1` or CI.

None of these affect running the app, and none were deleted without an
explicit instruction to do so.

## Not a limitation, but worth knowing

- Voice's **STT active engine is whisper.cpp**, not the `faster-whisper`
  package still listed in `requirements.txt` (kept only for backward
  compatibility — see [VOICE.md](VOICE.md)).
- Several backend settings (`ollama_host`, `max_iterations`,
  `delegate_timeout_seconds`) apply live but are **not persisted** across a
  backend restart — intentionally out of scope for the settings-persistence
  work done so far.
