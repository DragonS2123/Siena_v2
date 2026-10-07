#!/usr/bin/env bash
set -euo pipefail
SIENA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SIENA_DATA_DIR="${SIENA_DATA_DIR:-$SIENA_ROOT/storage/linux}"
mkdir -p "$SIENA_DATA_DIR/desktop-config" "$SIENA_DATA_DIR/desktop-cache"
export XDG_CONFIG_HOME="$SIENA_DATA_DIR/desktop-config"
export XDG_CACHE_HOME="$SIENA_DATA_DIR/desktop-cache"
# Codex may inherit this flag; Electron must run its desktop main process.
unset ELECTRON_RUN_AS_NODE
cd "$SIENA_ROOT/Siena v2 Control Panel UI"
if [[ ! -x node_modules/.bin/electron || ! -f dist/index.html ]]; then
  printf '%s\n' 'Prepare Linux dependencies with npm ci and npm run build first.' >&2
  exit 1
fi
exec node_modules/.bin/electron . --ozone-platform=wayland "$@"
