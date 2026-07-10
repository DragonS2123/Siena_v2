# Settings — Siena v2 (0.2.0)

Settings are persisted to `storage/settings.json` (backend-side) via
`GET/POST /api/settings`, reloaded on backend startup, and applied live —
no restart needed for anything marked **real** below. A missing or corrupt
`settings.json` never blocks startup: the backend falls back to
`config.py` defaults and logs `settings_load_failed` (see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)).

Every setting below is one of exactly three things — nothing in the current
Settings UI is a fake control that looks interactive but does nothing:
- **Real** — persists, loads, applies live.
- **Frontend-only (real, but local)** — genuinely applies, just stored in
  the browser's `localStorage` instead of the backend (documented as such
  in the UI itself, e.g. "Local only" badges).
- **Deferred / read-only** — visibly disabled or shown as a read-only
  diagnostic card, with an honest reason. Never a clickable-but-inert fake
  toggle.

## Model settings

| Setting | Status |
|---|---|
| Context window (`num_ctx`), max tokens (`num_predict`), max context messages, request timeout | Real |
| Primary/code model display | Read-only here — switching lives in the Models screen (`POST /api/models/active`), validated against Ollama there |

## Tool permissions

| Setting | Status |
|---|---|
| OCR, Image understanding, Translator, Reviewer/critic auto-routing, Code specialist auto-routing | Real — each takes effect on the very next message |
| File system / Network / Memory toggles | **Removed** (0.2.0) — these never gated anything real. Replaced by a read-only card listing the live registered-tools from the backend's own tool registry. |

## Appearance

| Setting | Status |
|---|---|
| Theme (dark/light/system), accent color (5 swatches), font size, density | Real — applied via `document.documentElement` data-attributes + a CSS override layer, no restart needed |
| Show message timestamps, show typing animation, copy-conversation-before-clearing | Real |

## Code rendering

| Setting | Status |
|---|---|
| Code font size, word wrap | Real |
| Syntax highlighting, line numbers, language badge, copy button, collapse/expand button, save-snippet button (visibility) | Real |
| Save snippet button (action) | Real — downloads the code block as a local file, no backend involved |
| Apply-patch button | **Removed** — Siena has no file-editing target for a patch to apply to, so it never did anything real |
| Code specialist auto-routing | Real (see Tool permissions above — same setting, shown in both places) |

## Voice

See [VOICE.md](VOICE.md) for the full picture. Summary:

| Setting | Status |
|---|---|
| Default STT recognition language (`stt_language`) | Real, persisted server-side |
| Voice profile (timbre preset) | Real, persisted server-side (separate store, `storage/voice_profiles.json`) |
| Show experimental Stream button | Real, persisted — visibility only, never changes backend behavior |
| Auto-speak new assistant replies by default | Frontend-only (real, `localStorage`) |
| Preserve formatting (Translator) | Frontend-only (real, `localStorage`) — shapes what the Translate button sends per call |

## Language

| Setting | Status |
|---|---|
| Interface language (EN/RU) | **Real** — see Localization below |
| Preferred input language | Real — same field as Voice's `stt_language`, shown in both places (not a duplicate setting) |
| Preferred response language (auto/ru/en) | Real — injected as one soft preference line into the chat prompt only when not `auto`; never a hard override of an explicit request, code, or Siena's natural Russian conversation behavior |
| Language presets (English only / Russian only / Mixed) | Real — set input + response language together |
| Translator preferred/fallback model display | Read-only (facts, not settings) |

## Localization (EN/RU)

Real i18n system, not a decorative selector:

- `Siena v2 Control Panel UI/src/i18n/types.ts` — `Locale = "en" | "ru"`.
- `Siena v2 Control Panel UI/src/i18n/index.ts` —
  `translate(locale, key, params?)`: current locale → falls back to English
  → falls back to the raw key string if missing everywhere.
- `Siena v2 Control Panel UI/src/i18n/locales/{en,ru}.json` — flat
  dot-namespaced keys (e.g. `"settings.language.interface"`). Full key
  parity between the two files is a hard requirement, verified at doc-time.
- Switching languages applies immediately (main sidebar, Settings, chat
  composer, common buttons, Debug page) and persists via
  `interface_language` (separate field from `stt_language` and
  `preferred_response_language` — never confused with either).
- **Not fully localized yet in 0.2.0**: deep content of Runtime/Debug/
  Models/Memory/Insights/Logs/Tool Trace views (dynamic backend data, trace
  event bodies, diagnostic tables) — see
  [KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md).
- Adding a third language: add a new locale JSON with the same keys,
  register it in `SUPPORTED_LOCALES`/`Locale`, and add it to the backend's
  `_INTERFACE_LANGUAGES` set (`api/server.py`) if it should validate
  server-side. Nothing else needs to change.

## Startup

| Setting | Status |
|---|---|
| Startup page (chat/runtime/settings) | Real |
| Preload / warmup | Deferred — splash readiness already uses real backend/runtime/conversation/model/settings checks; there's no fake warmup step to configure |
| Launch at login | Deferred — would require a main-process Electron API (`app.setLoginItemSettings`) reachable only via an IPC/preload bridge, which doesn't exist (see [KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md)) |

## Developer

| Setting | Status |
|---|---|
| Console log level | Real |
| Electron integration card | Read-only diagnostics (real facts: context isolation on, node integration off, sandboxed, no IPC bridge) |
| Local API card | Read-only (real facts: fixed port 8000, no authentication) |
| About (version, Electron, backend) | Read-only, real values (app version from `package.json` via a Vite build-time define) |

## Persisted settings schema

All real, persisted fields live in `storage/settings_store.py::
PERSISTABLE_FIELDS`, mirrored in `api/server.py`'s `SettingsUpdate`/
`_settings_payload`, with enum/bool validation and safe defaults on load
(malformed or BOM'd `settings.json` never blocks startup — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)).
