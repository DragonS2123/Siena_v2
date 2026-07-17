[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference='Stop'
if(Get-Process -Name 'Cyberpunk2077' -ErrorAction SilentlyContinue){throw 'Close Cyberpunk 2077 before uninstalling.'}
$Target=Join-Path ([IO.Path]::GetFullPath($GamePath)) 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_npc_controller'
if(-not(Test-Path -LiteralPath $Target)){Write-Host 'Controller is not installed.';return}
$Manifest=Get-Content -LiteralPath (Join-Path $Target 'manifest.json') -Raw|ConvertFrom-Json
if($Manifest.name-notin @('Siena NPC Presence Controller','Siena Embodied Companion')){throw 'Refusing to remove an unidentified folder.'}
if($PSCmdlet.ShouldProcess($Target,'Remove only Siena NPC Presence Controller')){Remove-Item -LiteralPath $Target -Recurse -Force;Write-Host "Removed only: $Target"}
