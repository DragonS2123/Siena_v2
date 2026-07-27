$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root "Siena v2 Control Panel UI")
try {
    npm.cmd run build
    $env:SIENA_LOAD_DIST = "1"
    npm.cmd run desktop
} finally {
    Remove-Item Env:\SIENA_LOAD_DIST -ErrorAction SilentlyContinue
    Pop-Location
}
