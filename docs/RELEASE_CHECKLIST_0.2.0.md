# Release Checklist — 0.2.0

**Manual RC smoke: PASSED (2026-07-10).** All functional smoke and health
signal items below were verified by the human user against a real running
backend + Electron UI. Details per item are in the checked list; see
[RELEASE_NOTES_0.2.0.md](../RELEASE_NOTES_0.2.0.md) for the release-level
summary. This is a human smoke checklist, not an automated gate (the
automated parts are `scripts\test_all.ps1`, already run as part of
finalizing this release — see "Automated checks" below for the latest
results).

## Functional smoke

- [x] Backend starts through **both** `start_backend.bat` (double-click or
      from a terminal) and `scripts\start_backend.ps1` — no `--reload`,
      no hardcoded personal path in either
- [x] UI starts through `scripts\start_ui.ps1` **or** `npm run desktop`
      and reaches the backend
- [x] Chat works (send a message, get a real reply)
- [x] Settings persist across a backend + Electron restart
- [x] UI language EN/RU switches live (Settings → Language → Interface
      language) and persists across restart
- [x] Voice **Speak**/TTS works (button under an assistant reply)
- [x] Voice **STT push-to-talk** works (mic button in the composer)
- [x] Attachment persistence works (attach a file, switch conversations,
      confirm it's still there on return)
- [x] OCR works (attach an image with text, ask what it says)
- [x] Vision works (attach an image, ask what it shows)
- [x] Translator works (Translate button under a reply)
- [x] Nucleares status endpoint works **if Nucleares is running**
      (`GET /api/game/nucleares/status`) — verified with Nucleares running
- [x] Nucleares chat context injection works **if Nucleares is running**
      (ask "что сейчас со станцией?") — verified with Nucleares running
- [x] Debug page diagnostics/export work (Overview shows real data, "Copy
      debug report" produces valid JSON with no secrets/full conversation
      text) — **Debug is complete as of this release**, not a pending item
- [x] Version visible as **0.2.0** in: Settings sidebar, Developer Settings
      About card, Debug Overview, Debug export report (all four read the
      same `APP_VERSION` constant, sourced from `package.json`)

## Health signals

- [x] No `settings_load_failed` in trace/logs after a normal restart
- [x] `GET /api/runtime/status` shows `ollama_status.connected: true` with
      at least the primary chat model installed

## Automated checks (last run for this release)

- [x] `py_compile api/server.py storage/settings_store.py config.py` — passed
- [x] `pytest tests -q --basetemp=.pytest_tmp -p no:cacheprovider` — **200 passed**
- [x] `npm run build` — passed (chunk-size warning only, non-blocking — see
      [TROUBLESHOOTING.md](TROUBLESHOOTING.md))

Re-run `scripts\test_all.ps1` yourself before actually shipping — the
above reflects the finalization pass, not a guarantee against later
changes.

## Hygiene / portability

- [x] No hardcoded local path blockers — `start_backend.bat` fixed (portable,
      delegates to `scripts\start_backend.ps1`, falls back gracefully, no
      `--reload` by default)
- [x] `.gitignore` present and covers venvs/`external/`/`storage/models`/
      `node_modules`/browser smoke profiles/logs
- [x] No secrets/tokens in tracked source (audited clean)
- [x] Stale duplicate `HANDOFF_v2 — копия.md` deleted

## Docs

- [x] README.md accurate and portable (no hardcoded personal paths)
- [x] docs/INSTALL.md, QUICK_START.md, MODELS.md, VOICE.md, SETTINGS.md,
      TROUBLESHOOTING.md, KNOWN_LIMITATIONS_0.2.0.md all present and current
      for 0.2.0
- [x] RELEASE_NOTES_0.2.0.md finalized
- [x] `HANDOFF_v2.md` updated with final 0.2.0 state

## Non-blocking cleanup (left untouched, not release blockers)

Not deleted — no explicit instruction to remove them, and none are
release-blocking:
- `NEXTDO.md` — stale point-in-time audit (dated 2026-07-07); superseded by
  current state but self-dated, so not actively misleading.
- `requirements-before-amd-test.txt` — superseded by `requirements.txt`.
- `test_glm_ocr_direct.py`, `test_vision_direct.py` (repo root) — ad-hoc
  manual debug scripts with a hardcoded personal picture path; not part of
  the pytest suite, don't run in CI/automated checks.

See [docs/KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md) for the
full non-blocking list.

## Not done in this release (explicitly out of scope)

- [ ] Package installer — separate, later pass.
- [ ] Games Hub — future work, not part of 0.2.0.
- [ ] Model-role configuration (per-role model swapping beyond manual
      active-chat-model switching) — future work.
