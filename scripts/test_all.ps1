# Runs the same backend + frontend validation commands used throughout this
# project's development passes (0.2.0 release audit pass). Stops at the
# first failure so CI-style usage gets a real non-zero exit code.

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv-faster-qwen3-tts\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Expected venv not found at $venvPython - falling back to 'python' on PATH." -ForegroundColor Yellow
    $venvPython = "python"
}

Write-Host "=== py_compile ===" -ForegroundColor Cyan
& $venvPython -m py_compile api/server.py storage/settings_store.py config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== pytest ===" -ForegroundColor Cyan
& $venvPython -m pytest tests -q --basetemp=.pytest_tmp -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== npm run build ===" -ForegroundColor Cyan
Push-Location "Siena v2 Control Panel UI"
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "`nAll checks passed." -ForegroundColor Green
