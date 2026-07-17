[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference='Stop'
if(Get-Process -Name 'Cyberpunk2077' -ErrorAction SilentlyContinue){throw 'Close Cyberpunk 2077 before installing the NPC controller.'}
$ProjectRoot=Split-Path -Parent $PSScriptRoot
$Source=Join-Path $ProjectRoot 'bridge\cet\siena_npc_controller'
$GameRoot=[IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$ModsRoot=Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods'
$Target=Join-Path $ModsRoot 'siena_npc_controller'
$BackupRoot=Join-Path $GameRoot 'siena_npc_controller_backups'
if(-not(Test-Path -LiteralPath (Join-Path $GameRoot 'bin\x64\Cyberpunk2077.exe') -PathType Leaf)){throw "Invalid game root: $GameRoot"}
if(-not(Test-Path -LiteralPath $ModsRoot -PathType Container)){throw "CET mods folder missing: $ModsRoot"}
if((Get-Process -Name 'Cyberpunk2077' -ErrorAction SilentlyContinue)){throw 'Game process must remain closed.'}
if(Test-Path -LiteralPath $Target){New-Item -ItemType Directory -Path $BackupRoot -Force|Out-Null;$Backup=Join-Path $BackupRoot (Get-Date -Format 'yyyyMMdd-HHmmss');Copy-Item -LiteralPath $Target -Destination $Backup -Recurse;Remove-Item -LiteralPath $Target -Recurse -Force;Write-Host "Backup: $Backup"}
New-Item -ItemType Directory -Path $Target|Out-Null
Get-ChildItem -LiteralPath $Source -File|Where-Object{$_.Extension -in '.lua','.json','.md'}|Copy-Item -Destination $Target
Write-Host "Installed v0.10.0 controller (presence remains disabled by default): $Target"
