# Starts the Siena v2 Control Panel UI as a desktop Electron window.
#
# Equivalent to the existing start_desktop.bat, provided here as a
# PowerShell script alongside the other scripts/*.ps1 release-readiness
# helpers (0.2.0 audit pass). Does not touch start_desktop.bat.

param(
    [switch]$DevServer  # point Electron at the Vite dev server instead of building first (hot reload while iterating on UI code)
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$uiDir = Join-Path $repoRoot "Siena v2 Control Panel UI"
Set-Location $uiDir

if (-not (Test-Path (Join-Path $uiDir "node_modules"))) {
    Write-Host "node_modules not found - running npm install first..."
    npm install
}

if ($DevServer) {
    Write-Host "Starting Electron against the Vite dev server (run 'npm run dev' in another terminal first)."
    npm run desktop:dev
} else {
    Write-Host "Building and launching the desktop app..."
    npm run desktop
}
