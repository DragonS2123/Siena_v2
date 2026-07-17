[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot;$Source=Join-Path $Root 'bridge\cet\siena_npc_controller';$Target=Join-Path ([IO.Path]::GetFullPath($GamePath)) 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_npc_controller';$Failed=$false
foreach($File in Get-ChildItem -LiteralPath $Source -File|Where-Object{$_.Extension -in '.lua','.json','.md'}){$Installed=Join-Path $Target $File.Name;$Match=(Test-Path -LiteralPath $Installed)-and((Get-FileHash $File.FullName).Hash-eq(Get-FileHash $Installed).Hash);Write-Host("{0,-24} {1}"-f $File.Name,$(if($Match){'OK'}else{'MISSING/MISMATCH'}));if(-not $Match){$Failed=$true}}
if(Test-Path -LiteralPath (Join-Path $Target 'config.lua')){$Text=Get-Content -LiteralPath (Join-Path $Target 'config.lua') -Raw;if($Text-notmatch'enabled\s*=\s*false' -or $Text-notmatch'npc_auto_spawn\s*=\s*false'){$Failed=$true}}
if($Failed){throw 'NPC controller verification failed.'};Write-Host 'Controller files match and safe defaults remain disabled.'
