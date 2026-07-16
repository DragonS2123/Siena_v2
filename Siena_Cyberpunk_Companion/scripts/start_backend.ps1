param(
    [ValidateSet('template','disabled','siena_core')][string]$ReactionProvider='template',
    [string]$SienaCoreUrl='',
    [switch]$VoiceEnabled,
    [string]$TtsUrl='',
    [switch]$PresenceEnabled
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
if ($VoiceEnabled) {
    $env:SIENA_CP_VOICE_ENABLED = 'true'
    if ($TtsUrl) {
        $env:SIENA_CP_TTS_BASE_URL = $TtsUrl
    }
    $EffectiveTtsUrl = $env:SIENA_CP_TTS_BASE_URL
    if (-not $EffectiveTtsUrl) {
        Write-Warning 'TtsUrl is empty. Voice will remain in text-only mode.'
    } else {
        try {
            $TtsStatus = Invoke-RestMethod -Uri ($EffectiveTtsUrl.TrimEnd('/') + '/api/voice/status') -TimeoutSec 2
            if ($TtsStatus.tts_available) {
                Write-Host "TTS status reachable: $($TtsStatus.tts_provider) / $($TtsStatus.tts_voice)"
            } else {
                Write-Warning 'TTS health is reachable but the configured provider is unavailable. Text reactions will continue.'
            }
        } catch {
            Write-Warning "TTS health unavailable. Voice will remain text-only until it recovers: $($_.Exception.Message)"
        }
    }
    Write-Warning 'Browser playback is not allowed until the user clicks Enable Voice in the frontend.'
}
if ($PresenceEnabled) {
    $env:SIENA_CP_PRESENCE_ENABLED = 'true'
    Write-Host 'In-game presence projection enabled. CET overlay remains locally disabled until the user enables it.'
}
Set-Location (Join-Path $Root 'backend')
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --no-access-log
