# Bazzite desktop validation — 2026-10-07

Implemented on `port/bazzite-linux`. HEAD remains
`b99c9945fb4460d79f210a850e47d9734e813ac8`; checkpoint remains
`13b8519eec946bd41fde52292861e1a78e4ffb06`. No commits, resets or rebases.
Both `storage/settings.json` and `storage/linux/settings.json` have their original
SHA256 hashes. Inference models/profiles/sampling/reasoning, Internet and Memory
behavior are unchanged. Changes to native process launch concern ownership only.

## Changed files

- `.gitignore`
- `Siena v2 Control Panel UI/electron/backend-process.cjs`
- `Siena v2 Control Panel UI/electron/main.cjs`
- `Siena v2 Control Panel UI/package.json`
- `Siena v2 Control Panel UI/src/app/App.tsx`
- `api/routers/trace.py`
- `assets/siena.png`
- `core/llama_cpp_process.py`
- `core/owned_exec.py`
- `core/runtime.py`
- `docs/BAZZITE_DESKTOP.md`
- `docs/BAZZITE_DESKTOP_VALIDATION.md`
- `requirements-linux.txt`
- `scripts/bootstrap_linux.sh`
- `scripts/desktop_preflight.py`
- `scripts/install_desktop.py`
- `scripts/run_linux.py`
- `scripts/start_desktop_linux.sh`
- `siena`
- `tests/desktop/backend_process.test.cjs`
- `tests/desktop/electron_shell.test.cjs`
- `tests/test_desktop_ownership.py`
- `tests/test_gigaam_stt.py`
- `tests/test_trace_shutdown.py`
- `voice/cosyvoice_cpp.py`
- `voice/gigaam_stt.py`

Also installed the user desktop entry below; generated `.venv-linux`, build,
SQLite test conversations, window state and validation artifacts are ignored.

## Shutdown architecture

```
Electron retains a concrete backend ChildProcess
  -> SIGTERM backend
  -> Uvicorn drains requests (maximum 5 s)
  -> FastAPI lifespan
     -> stop/reap GigaAM owned workers
     -> close owned llama-server
     -> stop owned CosyVoice server
  -> backend exit
  -> Electron exit
```

Electron waits 75 s before SIGKILL fallback, then up to 5 s for exit. Every signal
uses its retained child handle; there is no process-name kill or foreign-process
adoption. Menu, tray, window close, app quit and SIGTERM share the same idempotent
path. The Quit promise is assigned before destroying windows, guarding reentrant
window-all-closed/app.quit. Renderer destruction releases microphone, streaming
requests and WebSockets first. A quiet trace socket now observes disconnect and
cleans up its subscription and pending asyncio tasks immediately.

An exec-only Linux parent-death guard covers backend and native runtime children;
it preserves the Popen/ChildProcess PID and adds no daemon. Abnormal parent death
kills that parent's children; ordinary shutdown remains SIGTERM-first. Existing
LLM/TTS timeout/stop logic and all inference parameters remain unchanged.

Port 8000 occupied by an external process is a startup error. A second launch of
this checkout instead activates its existing Electron instance. Linux default
close/minimize-to-tray are false; explicit existing settings can opt in. The
Desktop Settings page no longer falsely displays Close-to-tray=Yes or states
that backend lifecycle is outside the desktop.

## Permanent environment and launch

`~/Siena_v2/.venv-linux` resolves to `/var/mnt/Games/Siena_v2/.venv-linux`, on
`/dev/nvme0n1p1` (ext4, project SSD). Python 3.14.8 works with selected audio
bindings. `requirements-linux.txt` contains the validated pinned dependencies;
`pip check` passes and all 66 tracked backend modules import. New ownership
helper is also exercised by Python tests and real process launches.

```
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -r requirements-linux.txt
```

Complete bootstrap and asset prerequisites: `docs/BAZZITE_DESKTOP.md`.
No `/tmp` venv is used after migration; unknown environments were not deleted.
The Python `ollama` SDK remains necessary for legacy imports, without installing
or running any Ollama server.

Launcher: `./siena`. The compatibility `scripts/start_desktop_linux.sh` delegates
to it. Binary/model checks precede launch, paths derive from the checkout root,
and Electron owns backend startup/readiness/logging. No systemd, Docker or
separate manually launched inference server is involved.

Installed file: `~/.local/share/applications/siena.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Siena
Comment=Siena desktop assistant
Exec="/var/mnt/Games/Siena_v2/siena"
Icon=/var/mnt/Games/Siena_v2/assets/siena.png
Terminal=false
Categories=Utility;
StartupNotify=true
StartupWMClass=Siena
```

The entry passes `desktop-file-validate`; both terminal launch and installed entry
activation through GNOME GAppInfo (`gio launch`) succeed. A second launch during
model startup returns 0 without another backend. A physical mouse click in GNOME
Overview was not automated.

## GPU diagnosis

Booted Bazzite: **44.20261006** (staged 44.20261006.1). Wayland/GNOME.
Electron 33.4.11 / Chromium 130.0.6723.191, Mesa/RADV 26.2.4-1.fc44.

Default ANGLE/EGL failed at EGL image binding with `EGL_BAD_MATCH` (0x3009),
three GPU-process failures and no functional WebGL. Unsetting EGL_PLATFORM,
selecting the dGPU, or `--use-angle=gl` did not resolve it. Native Wayland with
ANGLE/Vulkan did. This identifies the failing local EGL path; it does not prove
a specific kernel/Mesa regression. Similar AMD/EGL failures have been reported
in the official [Electron issue #45862](https://github.com/electron/electron/issues/45862).

Production arguments: `--ozone-platform=wayland --use-angle=vulkan`.
UI-only DRI_PRIME selector: `pci-0000_03_00_0`; RADV ICD:
`/usr/share/vulkan/icd.d/radeon_icd.x86_64.json`. Inherited Electron Node mode,
EGL_PLATFORM and software GL override are cleared. The backend drops UI-only
DRI_PRIME and retains its separately validated inference selectors.

Actual renderer:

```
ANGLE (AMD, Vulkan 1.4.354 (AMD Radeon RX 7900 XTX (RADV NAVI31)
(0x0000744C)), radv)
```

WebGL/WebGL2, 2D Canvas, GPU compositing and rasterization work in actual Siena.
Final full session: zero GPU crash events. No disable-gpu, SwiftShader or llvmpipe
success path. Complete getGPUInfo/getGPUFeatureStatus and probe logs are in
`external/desktop-validation/results`. This Chromium build's main-process GPUInfo
reports primary iGPU enumeration even when WebGL's unmasked hardware renderer
identifies RX 7900 XTX; `vulkan: disabled_off` is the compositor feature, not
ANGLE's backend. Actual renderer and successful GPU process are checked separately.

## Tests and live evidence

- Python: `.venv-linux/bin/python -m pytest -q tests`: **292 passed**.
- Frontend: `npm test`: **39 passed**, plus **12 Node desktop tests passed**.
- `npm run typecheck`, `npm run build`, `git diff --check`, shell syntax: passed.
- One pre-existing Starlette TestClient deprecation warning; existing Vite large
  chunk warning. Third-party Python tests under `external/` are not Siena's suite.

Desktop tests cover readiness/ownership, duplicate/concurrent startup, occupied
port refusal, missing interpreter, startup timeout, early exit, spawn error,
SIGTERM/forced fallback, cancellation during startup, reentrant/idempotent Quit,
and second-instance arrival before backend readiness. Python tests additionally
check native guard PID preservation, incorrect owner, actual parent-death cleanup,
GigaAM termination/fallback, all-runtime cleanup after one component error, and
trace disconnect/cancellation without pending task leaks.

Real terminal and GNOME sessions successfully exercised typed chat, actual
microphone (recognized «Да.» and «Омага.»), GigaAM → Gemma → CosyVoice,
frontend audio playback, Memory retrieval of RX 7900 XTX after restart/new chat,
and Internet requests invoking web_search with final source URLs. A deterministic
additional WAV phrase was recognized on RX 7900 XTX/Vulkan in **426 ms**:
«Сиена, привет. Ответь одним коротким предложением. Ты меня слышишь?»;
Gemma answered «Да, я тебя слышу.»; its playback completed (2.36 s).
The WAV replay is distinct from new microphone capture. PipeWire showed a real
running Chromium Stream/Output/Audio node; frontend playback reached ended,
including the final restart's 3.08 s audio. Subjective listening/voice quality was
not assessed by automation.

Window close was verified independently. Final installed-entry restart exercised
actual menu Quit plus repeated Quit through the explicit test-only shell hook;
backend log ends with Application shutdown complete / Finished server process.
No forced kill was needed for the final successful shutdown.

After final Quit:

```json
{
  "remaining_processes": [],
  "ports_free": {
    "8000": true,
    "8088": true,
    "8080": true
  },
  "vram_bytes": 1991995392,
  "wait_seconds": 0.645
}
```

Electron/native-server counts are all zero. Ports 8000/8088/8080 are free.
VRAM: **1.855 GiB after Quit**, compared with **1.925 GiB idle baseline** and
**19.894 GiB** during a loaded voice session. Values come from AMD PCI device
sysfs, not parsing inference stdout. Small desktop fluctuations are expected.

The previous `/tmp` runtime required one-time cleanup because its old Uvicorn
waited forever on an idle trace subscriber: exact old child SIGTERM, old owner
SIGKILL after the exceeded shutdown deadline. A test-only Inspector menu call
also exposed that its global scope lacks CommonJS require; the test was replaced
with a scoped shell hook. These failed attempts were cleaned up and followed by
successful clean startup/exit checks; only final results above count as acceptance.

## Limits

Validation applies to the current host/deployment/binaries and existing assets.
No model download/install, driver update, inference redesign or subjective audio
quality evaluation was performed. GNOME tray visibility depends on shell support;
exit is independent of tray presence. Existing 32px Siena icon is reused.
No production debugging ports: inspector/renderer debugging are opt-in under
SIENA_SHELL_DEBUG=1 only. Lifecycle unit tests simulate forced timeouts; real
final exit used graceful shutdown. Complete raw GPU diagnostics and compact
live artifacts remain local in ignored `external/desktop-validation/results`.
