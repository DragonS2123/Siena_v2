[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

if (Get-Process -Name 'Cyberpunk2077' -ErrorAction SilentlyContinue) { throw 'Close Cyberpunk 2077 before installing the research mod.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot 'bridge\cet\siena_npc_research'
$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$ModsRoot = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods'
$Target = Join-Path $ModsRoot 'siena_npc_research'
$BackupRoot = Join-Path $GameRoot 'siena_npc_research_backups'

if (-not (Test-Path -LiteralPath (Join-Path $GameRoot 'bin\x64\Cyberpunk2077.exe') -PathType Leaf)) { throw "Invalid game root: $GameRoot" }
if (-not (Test-Path -LiteralPath $ModsRoot -PathType Container)) { throw "CET mods folder not found: $ModsRoot" }
if (-not (Test-Path -LiteralPath (Join-Path $Source 'init.lua') -PathType Leaf)) { throw "Research source is incomplete: $Source" }
$Expected = [IO.Path]::GetFullPath((Join-Path $ModsRoot 'siena_npc_research')).TrimEnd('\')
if ([IO.Path]::GetFullPath($Target).TrimEnd('\') -ne $Expected) { throw 'Refusing unsafe installation target.' }

if (Test-Path -LiteralPath $Target) {
  New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
  $Backup = Join-Path $BackupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
  Copy-Item -LiteralPath $Target -Destination $Backup -Recurse
  Write-Host "Existing research mod backed up outside active CET mods: $Backup"
  Remove-Item -LiteralPath $Target -Recurse -Force
}
New-Item -ItemType Directory -Path $Target | Out-Null
Get-ChildItem -LiteralPath $Source -File | Where-Object { $_.Extension -in '.lua','.json','.md' } | Copy-Item -Destination $Target
Write-Host "Installed only the disabled-by-default Siena NPC research mod: $Target"
Write-Host 'No production observer or dependency was modified. Start the game manually when ready.'
