# Starts the Siena v2 FastAPI backend (api/server.py) via uvicorn.
#
# Portable on purpose: resolves the interpreter relative to this script's
# location instead of a hardcoded developer-specific path (see the 0.2.0
# release audit - the older start_backend.bat at the repo root hardcodes
# one machine's python.exe path and will not run elsewhere).
#
# Prefers the project's own .venv-faster-qwen3-tts venv (used throughout
# this project's own test/validation commands); falls back to "py" or
# "python" on PATH if that venv doesn't exist on this machine.
#
# No --reload by default: uvicorn's --reload restarts backend worker state
# (in-memory trace hub, active-chat-model override, spawned TTS subprocess
# handle) whenever a watched file changes, which is confusing for normal use.
# Pass -Reload if you're actively editing backend code.
#
# ASCII-only on purpose: PowerShell 5.1 can misread a BOM-less UTF-8 script
# using the system's legacy codepage on non-English Windows locales, which
# previously corrupted an em-dash into a parser error. Keep this file plain
# ASCII rather than relying on encoding/BOM handling.

param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv-faster-qwen3-tts\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
} else {
    $python = "python"
}

Write-Host "Siena v2 backend - using interpreter: $python"
Write-Host "Repo root: $repoRoot"

$uvicornArgs = @("-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "8000")
if ($Reload) {
    Write-Host "Starting with --reload (dev mode - do not use for normal sessions)"
    $uvicornArgs += "--reload"
}

& $python @uvicornArgs
