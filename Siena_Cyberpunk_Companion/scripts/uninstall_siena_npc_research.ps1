[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

if (Get-Process -Name 'Cyberpunk2077' -ErrorAction SilentlyContinue) { throw 'Close Cyberpunk 2077 before uninstalling the research mod.' }
$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$ModsRoot = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods'
$Target = Join-Path $ModsRoot 'siena_npc_research'
$Expected = [IO.Path]::GetFullPath((Join-Path $ModsRoot 'siena_npc_research')).TrimEnd('\')
if ([IO.Path]::GetFullPath($Target).TrimEnd('\') -ne $Expected) { throw 'Refusing unsafe uninstall target.' }
if (-not (Test-Path -LiteralPath $Target -PathType Container)) { Write-Host 'Siena NPC research mod is not installed.'; return }
$ManifestPath = Join-Path $Target 'manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Refusing to remove a folder without the research manifest.' }
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.name -ne 'Siena NPC Runtime Research') { throw 'Manifest does not identify Siena NPC Runtime Research.' }
if ($PSCmdlet.ShouldProcess($Target, 'Remove only Siena NPC Runtime Research')) {
  Remove-Item -LiteralPath $Target -Recurse -Force
  Write-Host "Removed only: $Target"
  Write-Host 'External backups were preserved for manual review.'
}
