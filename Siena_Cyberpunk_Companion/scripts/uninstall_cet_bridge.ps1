[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$ModsRoot = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods'
$Target = Join-Path $ModsRoot 'siena_cyberpunk_observer'
$Expected = [IO.Path]::GetFullPath((Join-Path $ModsRoot 'siena_cyberpunk_observer')).TrimEnd('\')
if ([IO.Path]::GetFullPath($Target).TrimEnd('\') -ne $Expected) { throw 'Refusing unsafe uninstall target.' }
if (-not (Test-Path -LiteralPath $Target -PathType Container)) { Write-Host 'Siena CET Bridge is not installed.'; return }
$ManifestPath = Join-Path $Target 'manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'Refusing to remove a directory without the Siena bridge manifest.' }
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.name -ne 'Siena Cyberpunk Observer' -or $Manifest.version -ne '0.2.0') { throw 'Manifest does not identify Siena Cyberpunk Observer v0.2.0.' }
if ($PSCmdlet.ShouldProcess($Target, 'Remove only Siena Cyberpunk Observer CET mod')) {
    Remove-Item -LiteralPath $Target -Recurse -Force
    Write-Host "Removed only: $Target"
}
