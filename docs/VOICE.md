# Voice — Siena v2 (0.2.0)

Voice is a pair of pure interface services — **STT** (speech-to-text) turns
your voice into text that lands in the chat input, **TTS** (text-to-speech)
turns Siena's already-written reply into audio. Neither is a tool the model
can call, and neither decides what Siena says.

## STT (speech-to-text)

**Active engine: `whisper.cpp`** (Vulkan-accelerated), via
`voice/whisper_cpp_stt.py`. This is what the mic button and Conversation Mode
actually use.

`voice/stt.py` (faster-whisper/CTranslate2, `large-v3-turbo`) still exists in
the codebase and in `requirements.txt` for backward compatibility, but it is
**dormant** — nothing currently calls it. Don't spend install effort getting
CUDA/CTranslate2 working for STT; it isn't on the active code path in 0.2.0.

Setup: see [INSTALL.md](INSTALL.md#2-voice-stt--tts--optional) for the
whisper.cpp binary/model placement. Greedy decoding
(`WHISPER_CPP_BEAM_SIZE = 1`) is forced deliberately — the default
beam-search settings are known to crash on Vulkan on at least one tested
AMD configuration; greedy avoids it and is fast (~0.2–0.5s including model
load). If the Vulkan call fails outright, it automatically retries once on
CPU and reports which backend actually answered (`GET /api/voice/status` →
`stt_backend_hint`).

- **Mic button** (push-to-talk): click to record, click again to stop —
  transcribed text lands in the composer input, you still press Send
  yourself. Never auto-sent.
- **Voice Conversation Mode** (experimental, `Headphones` icon next to the
  mic): hands-free listen → transcribe → auto-send → speak → listen loop.
  Mutually exclusive with push-to-talk.

## TTS (text-to-speech)

Three providers share one contract (`is_available()` /
`synthesize_to_file()`), tried in order:

| Provider | `config.VOICE_TTS_PROVIDER` value | Notes |
|---|---|---|
| **Faster Qwen3-TTS** (default) | `"faster_qwen3_tts"` | CUDA-accelerated via [`faster-qwen3-tts`](https://github.com/andimarafioti/faster-qwen3-tts) — RTF ~0.4–0.7 after warmup. |
| Qwen3-TTS | `"qwen3_tts"` | Same voice quality, no CUDA-graph speedup (slower, 30s+ per phrase without it). |
| Silero | `"silero"` | Always installed, always the automatic fallback if the primary provider is unavailable or errors — nothing breaks even on a machine with neither Qwen package installed. |

Buttons in the UI:
- **Speak** (stable): WAV-per-request via `POST /api/voice/synthesize`, with
  automatic Silero fallback. An amber note appears if the fallback actually
  spoke, so it's never silently substituted without you knowing.
- **Stream** (experimental, marked "exp" in the UI, and the visibility of
  this button itself is a real Settings toggle — see
  [SETTINGS.md](SETTINGS.md)): raw PCM streaming via
  `external/qwentts.cpp`'s `tts-server.exe`, Faster/Qwen3-TTS only, **no
  Silero fallback** — an unavailable provider here is an honest error, not a
  silent substitution.

### Voice profiles (timbre presets — not personality)

A **voice profile** is a saved `speaker`/`language`/`instruct` combination —
a technical description of tone/timbre, completely separate from
`config.SYSTEM_PROMPT` (Siena's character). Stored in
`storage/voice_profiles.json` (created automatically, seeded with 3
defaults), managed via `voice/voice_profiles.py`.

Real, persisted, and switchable from **Settings → Voice → Voice profile** —
activating a different profile changes the very next Speak/Stream output,
no restart needed.

Default profiles: `siena_default_adult` (active by default — mature, calm,
warm), `siena_soft_companion` (gentle, intimate), `siena_clear_technical`
(clear, focused). Manage via `GET/POST /api/voice/profiles`,
`GET/POST /api/voice/profiles/active`, `PATCH /api/voice/profiles/{id}`.

## Real Settings for voice (see SETTINGS.md for the full list)

- `stt_language` — default STT recognition language (auto/ru/en) —
  **persisted server-side**, not a browser-only preference.
- Voice profile (above) — persisted server-side, its own store.
- "Auto-speak new assistant replies by default" and "Preserve formatting"
  (Translator) — these two are genuinely **frontend-only** preferences
  (`localStorage`), documented as such in the UI itself.
- "Show experimental Stream button" — real, persisted, purely a visibility
  toggle (doesn't change backend behavior).

## Disabling voice entirely

- **UI only**: don't use the mic/Speak/Stream buttons — nothing forces you
  to.
- **Backend**: don't build whisper.cpp / install a TTS provider at all.
  `/api/voice/*` endpoints report honest `unavailable` statuses; nothing
  else in the app depends on them.
