param([ValidateSet('exploration','combat','critical_health','vehicle','companion_stuck','v03_readonly','v04_siena_core')][string]$Scenario='exploration',[double]$Speed=1.0,[switch]$Loop)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $PSScriptRoot 'start_backend.ps1'
$Frontend = Join-Path $PSScriptRoot 'start_frontend.ps1'
$Simulator = Join-Path $PSScriptRoot 'start_simulator.ps1'

Write-Host 'Siena Cyberpunk Companion v0.3 (read-only)'
Write-Host 'Backend: http://127.0.0.1:8765'
Write-Host 'UI:      http://127.0.0.1:5173'
Write-Host "Scenario: $Scenario"
Start-Process powershell.exe -ArgumentList @('-NoExit','-ExecutionPolicy','Bypass','-File',$Backend) -WorkingDirectory $Root
Start-Process powershell.exe -ArgumentList @('-NoExit','-ExecutionPolicy','Bypass','-File',$Frontend) -WorkingDirectory $Root
Write-Host 'Waiting up to 20 seconds for backend health...'
$Ready = $false
for ($Attempt=0; $Attempt -lt 40; $Attempt++) {
    try { $Response = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 1; if ($Response.status -eq 'ok') { $Ready=$true; break } } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $Ready) { throw 'Backend did not become healthy. Inspect the backend window.' }
$Arguments = @('-NoExit','-ExecutionPolicy','Bypass','-File',$Simulator,'-Scenario',$Scenario,'-Speed',$Speed)
if ($Loop) { $Arguments += '-Loop' }
Start-Process powershell.exe -ArgumentList $Arguments -WorkingDirectory $Root
Write-Host 'All processes started. Close each process window to stop it cleanly.'
