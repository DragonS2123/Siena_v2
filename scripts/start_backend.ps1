$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root ".venv-faster-qwen3-tts\Scripts\python.exe") -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --app-dir $Root
