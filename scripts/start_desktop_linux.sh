#!/usr/bin/env bash
set -euo pipefail
SIENA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$SIENA_ROOT/siena" "$@"
