#!/usr/bin/env bash
set -euo pipefail
SIENA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SIENA_PYTHON="${SIENA_PYTHON:-$SIENA_ROOT/.venv-linux/bin/python}"
if [[ ! -x "$SIENA_PYTHON" ]]; then
  printf '%s\n' 'Set SIENA_PYTHON to your prepared native Linux venv python.' >&2
  exit 1
fi
cd "$SIENA_ROOT"
exec "$SIENA_PYTHON" scripts/run_linux.py
