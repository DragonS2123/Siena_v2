# Models — Siena v2 (0.2.0)

Siena routes between several Ollama models by **role**, not by letting the
user free-pick any model for any purpose. The table below is the current,
real configuration (`config.py::MODEL_REGISTRY` and the individual
`*_MODEL` constants) — not aspirational.

**Important for 0.2.0:** these are the developer's current default model
choices, not a permanent requirement to use exactly these models forever.
Per-role model configuration (letting a user swap, say, the code model for a
different one from Settings) is **planned/future work**, not implemented in
0.2.0 — see the "Model-role configuration" note at the bottom.

## Role table

| Role | Default model | Routing mode | Enabled by default | Purpose |
|---|---|---|---|---|
| Primary chat | `qwen3.5:9b` | `auto` (always) | — | The only model that actually talks to you. Everything else is a tool/specialist it can delegate to. |
| Manual heavy model | `qwen3.5:27b` | `manual_only` | — | Larger model, **never auto-selected** — only reachable via an explicit "Set as active chat model" action in the Models screen (`POST /api/models/active`). |
| Code specialist | `qwen2.5-coder:7b` | `auto_for_code` | `enable_code_specialist_auto` (on) | Auto-routed only for requests that clearly look like coding/debugging tasks. |
| Reviewer / critic | `ornith:9b` | `explicit_only` | `enable_reviewer_explicit` (on) | Only invoked when you explicitly ask for a review/critique — never automatic. |
| OCR | `glm-ocr` | `tool` | `enable_ocr` (on) | Reads text out of attached images. Runs unconditionally on every image attachment (it only reads, it doesn't decide anything). |
| Vision / image understanding | `qwen2.5vl` | `tool` | `enable_image_understanding` (on) | Describes what's *in* an image (scene/objects) — separate from OCR, and intent-gated: only runs when you actually ask what the image shows. |
| Translator | `translategemma-strict:4b` (a locally-built variant of `translategemma:4b`, see [INSTALL.md](INSTALL.md#4-translator--optional)) | `tool` | `enable_translator` (on) | Powers the per-message Translate button. Falls back to `qwen3.5:9b` if unavailable. |

Routing modes, defined once in `core/model_router.py` / `config.py` and
reused by both the router and the Models screen:
- `auto` — always the default for a normal chat turn.
- `auto_for_code` — the router widens matching to include code specialist
  patterns, but only when the turn looks like a coding task.
- `explicit_only` — never auto-selected; only reachable by an explicit
  request phrase the router recognizes (review/critique language).
- `manual_only` — never a routing candidate at all; only reachable through
  the explicit human action in the Models screen.
- `tool` — not part of chat routing; a separate service call (OCR/vision/
  translator), not a "which model answers this message" decision.

## Optional / future roles (not implemented in 0.2.0)

- **Deep reasoning model** — `qwen3.5:27b` exists in the registry as
  `manual_heavy_model` and can be switched to manually, but there is no
  automatic "this looks like it needs deep reasoning" routing to it yet.
  `ENABLE_HEAVY_REASONING_AUTO = False` and is documented in `config.py` as
  intentionally not meant to be flipped on without further design work.
- **Image generation** — no provider or model integrated yet. Not started.

## Model-role configuration (planned, not in 0.2.0)

Per-role model selection (e.g. "use a different code model") isn't
user-configurable yet beyond the one already-real exception: manually
switching the *active chat* model between `qwen3.5:9b` and `qwen3.5:27b`
via the Models screen (`GET/POST /api/models/active`) — this is the only
role where a human already picks between two fixed options. A general
"reconfigure any role to any installed model" system is future work, not
started this release.

## Where install status comes from

`GET /api/models` combines the static role table above with a **live**
`ollama list` check, so the Models screen always shows honestly whether
each configured model is actually installed, missing, or unknown — it
never assumes a model is present just because it's configured.

See also: [INSTALL.md](INSTALL.md) for exact `ollama pull`/`ollama create`
commands, [SETTINGS.md](SETTINGS.md) for the Tool permissions toggles that
gate OCR/Vision/Translator/Reviewer/Code-specialist routing.
