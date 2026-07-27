$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root ".venv-faster-qwen3-tts\Scripts\python.exe") -m pip install -r (Join-Path $Root "requirements.txt")
Push-Location (Join-Path $Root "Siena v2 Control Panel UI")
try { npm.cmd install } finally { Pop-Location }
