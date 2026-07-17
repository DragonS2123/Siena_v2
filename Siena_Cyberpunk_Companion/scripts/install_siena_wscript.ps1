[CmdletBinding()]
param()
$ErrorActionPreference='Stop';if(Get-Process -Name 'WolvenKit' -ErrorAction SilentlyContinue){throw 'Close WolvenKit before installing the WScript.'}
$Root=Split-Path -Parent $PSScriptRoot;$Source=Join-Path $Root 'wolvenkit\siena_npc\scripts\CreateSienaStandaloneAssets.wscript';$TargetRoot=Join-Path $env:APPDATA 'REDModding\WolvenKit\WScript';$Target=Join-Path $TargetRoot 'CreateSienaStandaloneAssets.wscript'
if(-not(Test-Path -LiteralPath $Source -PathType Leaf)){throw "Source WScript missing: $Source"};New-Item -ItemType Directory -Path $TargetRoot -Force|Out-Null
if(Test-Path -LiteralPath $Target){$Backup=$Target+'.bak-'+(Get-Date -Format 'yyyyMMdd-HHmmss');Copy-Item -LiteralPath $Target -Destination $Backup -Force;Write-Host "Existing WScript backup: $Backup"}
Copy-Item -LiteralPath $Source -Destination $Target -Force
if((Get-FileHash -LiteralPath $Source).Hash-ne(Get-FileHash -LiteralPath $Target).Hash){throw 'Installed WScript hash mismatch.'};Write-Host "Installed WolvenKit user WScript: $Target"
