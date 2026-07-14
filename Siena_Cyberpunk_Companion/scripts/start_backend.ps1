$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Missing .venv. Run: & '<Python 3.12 path>' -m venv '$Root\.venv'; & '$Root\.venv\Scripts\pip.exe' install -r '$Root\backend\requirements.txt'" }
Set-Location (Join-Path $Root 'backend')
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
