#!/usr/bin/env bash
set -euo pipefail
SIENA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$SIENA_ROOT"
if [[ ! -x .venv-linux/bin/python ]]; then
  python3 -m venv .venv-linux
fi
.venv-linux/bin/python -m pip install -r requirements-linux.txt
cd "$SIENA_ROOT/Siena v2 Control Panel UI"
npm ci
npm run build
printf '%s\n' 'Siena desktop prepared. Run ./siena from the project root.'
