# Quick health check against an already-running Siena v2 backend
# (start it first with scripts/start_backend.ps1 or start_backend.bat).
#
# Hits a handful of real, cheap GET endpoints once each - this is a smoke
# check, not a load test or a polling loop.

param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$failed = $false

function Test-Endpoint {
    param([string]$Path, [string]$Label)
    try {
        $response = Invoke-RestMethod -Uri "$BaseUrl$Path" -Method Get -TimeoutSec 5
        Write-Host "[OK]   $Label ($Path)" -ForegroundColor Green
        return $response
    } catch {
        Write-Host "[FAIL] $Label ($Path): $($_.Exception.Message)" -ForegroundColor Red
        $script:failed = $true
        return $null
    }
}

Write-Host "Smoke-testing Siena v2 backend at $BaseUrl ...`n"

$runtime = Test-Endpoint "/api/runtime/status" "Runtime status"
Test-Endpoint "/api/settings" "Settings" | Out-Null
Test-Endpoint "/api/trace/recent?limit=1" "Trace log" | Out-Null

if ($runtime) {
    Write-Host "`nActive chat model: $($runtime.active_chat_model)"
    Write-Host "Ollama connected:  $($runtime.ollama_status.connected)"
    Write-Host "Registered tools:  $($runtime.registered_tools.Count)"
}

Write-Host ""
if ($failed) {
    Write-Host "Smoke check FAILED - is the backend running? (scripts/start_backend.ps1)" -ForegroundColor Red
    exit 1
} else {
    Write-Host "Smoke check passed." -ForegroundColor Green
    exit 0
}
