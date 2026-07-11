# Known Limitations — 0.2.3 (Computer Awareness Layer, Phase 1)

Additions on top of [KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md)
(which still applies in full). Scope: the read-only Computer Awareness
Layer (`computer/`, `/api/computer/*`, the Computer Status Card, and the
`[COMPUTER_CONTEXT]` chat injection).

## By design (Phase 1 boundaries, not bugs)

- **Strictly read-only.** No command execution, no process control, no file
  changes, no repair actions. Siena can *explain* the computer's state; she
  cannot change it — the hidden context block explicitly tells the model so,
  and `tests/test_computer_awareness.py` structurally asserts that every
  `/api/computer/*` route is GET-only and that the `computer/` package
  contains no subprocess/`os.system`/`Popen` usage of its own.
- **No clipboard, no screenshots, no webcam, no input control.** Not
  implemented at all in Phase 1 — not merely disabled.
- **`allow_active_window_title` defaults to `false`.** Window titles can
  contain personal information (document names, site titles); while off, the
  title is neither collected nor ever included in chat context.
- **Computer context is injected only on explicit computer/runtime
  questions** (`computer/computer_context.py`'s regex intent), never on every
  message, and never persisted into conversation history.
- **No backend-side polling.** State is collected per request; the frontend
  polls `GET /api/computer/status` every `computer_status_poll_seconds`
  (default 10s). Successful polls do not write trace events — only
  warning-set *changes* and collection failures do.

## Honest gaps

- **VRAM is NVIDIA-only** (reuses `core/system_metrics.py`'s nvidia-smi
  path). On AMD (including this project's own RX 7900 XTX) it reports an
  honest `unavailable` with a reason instead of a wrong number — see that
  module's docstring for why WMI/ADL readings are deliberately not attempted.
- **`gpu_name` is always null for now** — the existing nvidia-smi query
  doesn't request it and AMD has no safe query at all.
- **`network_available` is a local NIC-up check only** — it never sends
  anything to the network, so it can't distinguish "interface up" from
  "actually has internet".
- **`important_processes` is a curated allowlist** (backend, Ollama,
  tts-server, whisper-cli) — deliberately not a full system process dump, so
  "что жрёт память?" is answered from Siena's own runtime plus overall RAM
  numbers, not per-app accounting for the whole machine.
- **The process RAM numbers are RSS** — the usual caveats about shared
  memory apply.
- **Deep localization**: the Computer Status Card labels and warnings are
  localized (EN/RU), but the raw `/api/computer/status` payload's messages
  are canonical Russian, same convention as the presence layer.
