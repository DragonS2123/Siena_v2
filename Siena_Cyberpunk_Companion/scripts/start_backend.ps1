param(
    [ValidateSet('template','disabled','siena_core')][string]$ReactionProvider='template',
    [string]$SienaCoreUrl=''
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Missing .venv. Run: & '<Python 3.12 path>' -m venv '$Root\.venv'; & '$Root\.venv\Scripts\pip.exe' install -r '$Root\backend\requirements.txt'" }
$env:SIENA_CP_REACTION_PROVIDER = $ReactionProvider
if ($ReactionProvider -eq 'siena_core') {
    $env:SIENA_CP_SIENA_CORE_ENABLED = 'true'
    if ($SienaCoreUrl) {
        $env:SIENA_CP_SIENA_CORE_BASE_URL = $SienaCoreUrl
    }
    $EffectiveSienaCoreUrl = $env:SIENA_CP_SIENA_CORE_BASE_URL
    if (-not $EffectiveSienaCoreUrl) {
        Write-Warning 'SienaCoreUrl is empty. Companion will start in configuration_error and use the configured fallback.'
    } else {
        try {
            $Health = Invoke-RestMethod -Uri ($EffectiveSienaCoreUrl.TrimEnd('/') + '/api/health') -TimeoutSec 2
            Write-Host "Siena Core health reachable: $($Health.ok)"
        } catch {
            Write-Warning "Siena Core health is unavailable. Companion will still start and use fallback: $($_.Exception.Message)"
        }
    }
}
Set-Location (Join-Path $Root 'backend')
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --no-access-log
