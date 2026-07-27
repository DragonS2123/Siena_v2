$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TestTemp = Join-Path $env:TEMP "siena_v2_pytest"
New-Item -ItemType Directory -Path $TestTemp -Force | Out-Null
& (Join-Path $Root ".venv-faster-qwen3-tts\Scripts\python.exe") -m pytest (Join-Path $Root "tests") -q -p no:cacheprovider --basetemp $TestTemp
Push-Location (Join-Path $Root "Siena v2 Control Panel UI")
try { npm.cmd run typecheck; npm.cmd test; npm.cmd run build } finally { Pop-Location }
