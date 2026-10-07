# Siena desktop on Bazzite

The checkout stays on the SSD; `~/Siena_v2` may point to it. Work on
`port/bazzite-linux`. The launcher and backend resolve the checkout root.

## Bootstrap

```bash
cd ~/Siena_v2
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -r requirements-linux.txt
cd 'Siena v2 Control Panel UI'
npm ci
npm run build
cd ..
.venv-linux/bin/python scripts/install_desktop.py
./siena
```

Alternatively `scripts/bootstrap_linux.sh` prepares the venv and frontend, then
run the desktop installer. Runtime binaries, models, reference voice and the
validated local transcribe.cpp wheel must already exist; the launcher reports
missing assets. It downloads no models and installs no system packages.

Validated Python: 3.14.8, permanent Homebrew interpreter, `.venv-linux` on the
project SSD. `requirements-linux.txt` pins the validated dependencies. The
legacy `ollama` Python SDK is still an import dependency; no Ollama server is
installed or started. Old `/tmp` environments are neither used nor deleted.

## Ownership and exit

`./siena` execs Electron. Electron acquires one instance lock before spawning its
retained backend ChildProcess. Backend owns Gemma and the lazily started
CosyVoice server, plus individual GigaAM subprocess handles. A second launch
activates the existing window. Port 8000 held by another backend is an error;
Siena never adopts or signals a foreign process.

Window close on Linux, menu Quit/Ctrl+Q, tray Quit, SIGTERM and app quit share
one idempotent path: release the renderer/microphone/WebSockets, SIGTERM the
owned backend, await its exit up to 75 seconds, then SIGKILL only that handle if
necessary. Backend lifespan terminates/reaps all owned workers. Uvicorn drains
requests up to five seconds; an idle trace WebSocket observes disconnect directly.
The backend and native children use an exec-only Linux parent-death guard,
keeping their PIDs. Abnormal Electron death releases the backend; abnormal or
forced backend death also releases its children.

Linux close/minimize-to-tray defaults are false. Explicit booleans in existing
Linux settings opt into tray behavior; startup never rewrites settings.

## Wayland GPU

Electron 33.4.11 / Chromium 130, Mesa/RADV 26.2.4 on Bazzite 44.20261006.
The default ANGLE/EGL path failed to bind its offscreen image (`EGL_BAD_MATCH`,
three GPU-process exits); clearing `EGL_PLATFORM` and selecting the dGPU alone
were insufficient. `--use-angle=gl` still failed. Native Wayland plus
`--use-angle=vulkan` rendered successfully on the RX 7900 XTX.

Production flags: `--ozone-platform=wayland --use-angle=vulkan`.
UI-only `DRI_PRIME=pci-0000_03_00_0` and the RADV `VK_DRIVER_FILES` ICD select
hardware. Inherited Electron Node mode, EGL override and software GL override
are cleared. Backend removes UI-specific selectors and retains its validated
inference configuration. No disable-gpu or SwiftShader fallback is used.

WebGL's actual renderer is the decisive check. Chromium's `getGPUInfo` on this
build can report the primary iGPU enumeration even while ANGLE uses the dGPU;
its `vulkan: disabled_off` compositor feature does not describe ANGLE's Vulkan
backend. Complete diagnostics and before/after probes are retained locally in
`external/desktop-validation/results` (ignored validation artifacts).

## Diagnostics and checks

```bash
.venv-linux/bin/python -m pip check
.venv-linux/bin/python -m pytest -q tests
cd 'Siena v2 Control Panel UI'
npm test
npm run typecheck
npm run build
```

Tests under `external/` belong to third-party source trees and are not Siena's
Python suite. Backend stdout/stderr append to
`storage/linux/logs/desktop-backend.{stdout,stderr}.log`. Existing inference
logs remain in the same directory. GUI window state lives in
`storage/linux/desktop-config/Siena`, independently of user settings.

`SIENA_SHELL_DEBUG=1 ./siena` opts into localhost-only Node inspector 9231 and
renderer debugging 9229, plus existing shell diagnostics. Normal launch opens
neither debugging endpoint. Do not enable this environment variable routinely.

The installed `~/.local/share/applications/siena.desktop` invokes the same
launcher, uses the existing Siena tray artwork and requires no root. Re-run
`scripts/install_desktop.py` after moving the checkout.
