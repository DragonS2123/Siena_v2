[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot 'bridge\cet\siena_npc_research'
$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$Target = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_npc_research'
$Failed = $false
foreach ($File in Get-ChildItem -LiteralPath $Source -File | Where-Object { $_.Extension -in '.lua','.json','.md' }) {
  $Installed = Join-Path $Target $File.Name
  $Match = (Test-Path -LiteralPath $Installed -PathType Leaf) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $File.FullName).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $Installed).Hash)
  Write-Host ("{0,-24} {1}" -f $File.Name, $(if ($Match) { 'OK' } else { 'MISSING/MISMATCH' }))
  if (-not $Match) { $Failed = $true }
}
if (Test-Path -LiteralPath (Join-Path $Target 'config.lua')) {
  if ((Get-Content -LiteralPath (Join-Path $Target 'config.lua') -Raw) -notmatch 'enabled\s*=\s*false') { Write-Host 'config.lua               UNSAFE: enabled by default'; $Failed = $true }
}
if ($Failed) { throw 'Siena NPC research verification failed. No files were changed.' }
Write-Host 'Installed research files match source and remain disabled by default.'
Write-Host 'Runtime capabilities still require the documented manual in-game gates.'
