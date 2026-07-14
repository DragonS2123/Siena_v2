[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot 'bridge\cet\siena_cyberpunk_observer'
$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$Exe = Join-Path $GameRoot 'bin\x64\Cyberpunk2077.exe'
$CetRoot = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks'
$ModsRoot = Join-Path $CetRoot 'mods'
$Target = Join-Path $ModsRoot 'siena_cyberpunk_observer'
$RedHttp = Join-Path $GameRoot 'red4ext\plugins\RedHttpClient'

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "Cyberpunk2077.exe was not found: $Exe" }
if (-not (Test-Path -LiteralPath $CetRoot -PathType Container)) { throw "Cyber Engine Tweaks folder was not found: $CetRoot" }
if (-not (Test-Path -LiteralPath $ModsRoot -PathType Container)) { throw "CET mods folder was not found: $ModsRoot" }
if (-not (Test-Path -LiteralPath (Join-Path $GameRoot 'red4ext\RED4ext.dll') -PathType Leaf)) { throw 'RED4ext.dll was not found.' }
if (-not (Test-Path -LiteralPath (Join-Path $GameRoot 'engine\tools\scc.exe') -PathType Leaf)) { throw 'redscript compiler engine\tools\scc.exe was not found.' }
if (-not (Test-Path -LiteralPath $RedHttp -PathType Container)) { throw "RedHttpClient was not found: $RedHttp" }
if (-not (Get-ChildItem -LiteralPath $RedHttp -Filter '*RedHttpClient*.dll' -Recurse -File -ErrorAction SilentlyContinue)) { throw 'RedHttpClient DLL was not found inside its plugin folder.' }
if (-not (Test-Path -LiteralPath (Join-Path $Source 'init.lua') -PathType Leaf)) { throw "Bridge source is incomplete: $Source" }

$ExpectedTarget = [IO.Path]::GetFullPath((Join-Path $ModsRoot 'siena_cyberpunk_observer')).TrimEnd('\')
if ([IO.Path]::GetFullPath($Target).TrimEnd('\') -ne $ExpectedTarget) { throw 'Refusing unsafe installation target.' }

if (Test-Path -LiteralPath $Target) {
    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Backup = "$Target.backup.$Stamp"
    if (-not ([IO.Path]::GetFullPath($Backup).StartsWith([IO.Path]::GetFullPath($ModsRoot), [StringComparison]::OrdinalIgnoreCase))) { throw 'Refusing unsafe backup target.' }
    Move-Item -LiteralPath $Target -Destination $Backup
    Write-Host "Previous Siena bridge backed up to: $Backup"
}

New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $Target 'init.lua'))) { throw 'Installation copy verification failed.' }
Write-Host "Siena CET Bridge installed to: $Target"
Write-Host 'Start Cyberpunk 2077 manually with -no-tls for http://127.0.0.1 transport.'
Write-Host 'No executable, launcher option, or third-party mod was modified.'
