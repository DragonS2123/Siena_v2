# Install — Siena v2 (0.2.0 release line)

This covers everything needed to run Siena v2 locally. Nothing here requires
a specific username or drive letter — replace `<repo>` with wherever you
cloned/extracted the project.

For the fastest path to a working chat, see [QUICK_START.md](QUICK_START.md)
instead. This document is the complete reference.

## 1. Core requirements (required for basic chat)

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.12.x | The project's own venv (`.venv-faster-qwen3-tts`) was built against 3.12.0. 3.11 is also known to work (see legacy `requirements-before-amd-test.txt` era) but 3.12 is the current target. |
| Node.js | 20+ (tested with 24.18.0) | For the Control Panel UI. |
| npm | 10+ (tested with 11.16.0) | Ships with Node. |
| [Ollama](https://ollama.com) | recent | Runs every local LLM Siena uses. Must be running (`ollama serve`, or the desktop tray app) before starting the backend. |

Install Python backend dependencies:

```powershell
cd <repo>
py -3.12 -m venv .venv-faster-qwen3-tts
.venv-faster-qwen3-tts\Scripts\python.exe -m pip install --upgrade pip
.venv-faster-qwen3-tts\Scripts\python.exe -m pip install -r requirements.txt
```

Install frontend dependencies:

```powershell
cd "<repo>\Siena v2 Control Panel UI"
npm install
```

Pull the required Ollama models (see [MODELS.md](MODELS.md) for the full
role table and which models are optional):

```powershell
ollama pull qwen3.5:9b
ollama pull qwen2.5-coder:7b
```

That's enough for chat + code routing. Everything below is additive.

## 2. Voice (STT + TTS) — optional

Siena's **active** STT engine is `whisper.cpp` (Vulkan-accelerated), not the
`faster-whisper` PyPI package in `requirements.txt` (that one is legacy/dormant
— kept installed only for backward compatibility, see
[VOICE.md](VOICE.md)). whisper.cpp is a vendored, compiled dependency, not
pip-installable:

- Build or obtain `whisper-cli.exe` and place it at
  `external/whisper.cpp/build/bin/Release/whisper-cli.exe`.
- Download a ggml model (the project defaults to the multilingual `base`
  model, ~147MB) to `external/whisper.cpp/models/ggml-base.bin`.
- See [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) for
  build instructions. Vulkan support requires building with
  `-DGGML_VULKAN=1`.

TTS defaults to `faster-qwen3-tts` (CUDA-accelerated), with automatic
fallback to Silero if it's unavailable:

```powershell
.venv-faster-qwen3-tts\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv-faster-qwen3-tts\Scripts\python.exe -m pip install faster-qwen3-tts soundfile
```

The experimental streaming TTS path additionally uses a standalone compiled
server, `external/qwentts.cpp/build/Release/tts-server.exe` (`qwentts.cpp`,
same repo family as whisper.cpp above) — only needed if you want the
experimental "Stream" button; the stable "Speak" button works with the
Python provider alone.

Full detail, provider comparison, and voice profile management:
[VOICE.md](VOICE.md).

## 3. OCR / Vision — optional

Both run as separate Ollama models, no extra pip/build dependencies beyond
what's already in `requirements.txt`:

```powershell
ollama pull glm-ocr
ollama pull qwen2.5vl
```

If either model isn't installed, that feature degrades honestly (OCR/vision
results are simply reported unavailable) rather than failing the whole chat
turn.

## 4. Translator — optional

The translator uses a **locally-built custom Ollama model**, not a plain
`ollama pull`. It's `translategemma:4b` wrapped with a strict
system-prompt Modelfile (`Modelfile.translate.strict` in the repo root) that
suppresses explanations/alternatives/Markdown in the output:

```powershell
ollama pull translategemma:4b
ollama create translategemma-strict:4b -f Modelfile.translate.strict
```

`config.TRANSLATOR_MODEL = "translategemma-strict:4b"` — if you skip the
`ollama create` step, the translator will report the model as missing even
though `translategemma:4b` itself is installed.

## 5. Nucleares Game Bridge — optional

Only relevant if you actually run the Nucleares simulation game locally.
Siena's bridge is **read-only telemetry only** — see
[KNOWN_LIMITATIONS_0.2.0.md](KNOWN_LIMITATIONS_0.2.0.md). No install step on
Siena's side; it just needs Nucleares' own local webserver reachable (default
`http://localhost:8785`, with `[::1]`/`127.0.0.1` and a few alternate ports
tried automatically — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#nucleares-binds-to-ipv6-6-not-1270701)
if it can't connect).

## 6. Electron desktop shell

No separate install — `npm install` above already pulled `electron` as a
dev dependency. `npm run desktop` builds the UI and launches it in a plain
`BrowserWindow` (no IPC bridge, no preload script — the renderer talks to
the backend purely over HTTP/WebSocket to `http://127.0.0.1:8000`).

## Summary: what's required for what

| Feature | Requires |
|---|---|
| Chat (text only) | Python 3.12 + `requirements.txt`, Node/npm, Ollama, `qwen3.5:9b` |
| Code-specialist routing | + `qwen2.5-coder:7b` |
| Reviewer/critic | + `ornith:9b` |
| OCR | + `glm-ocr` |
| Image understanding (vision) | + `qwen2.5vl` |
| Translator | + `translategemma:4b` and the `translategemma-strict:4b` Modelfile build |
| STT (speech-to-text) | + `whisper.cpp` build + a ggml model under `external/whisper.cpp/` |
| TTS (speak/stream) | + `faster-qwen3-tts` (or `qwen-tts`, or nothing — Silero always works as a fallback) |
| Experimental Stream (raw PCM) | + `external/qwentts.cpp` built `tts-server.exe` |
| Nucleares context | + Nucleares itself running locally with its webserver reachable |

Next: [QUICK_START.md](QUICK_START.md) to actually run it.
