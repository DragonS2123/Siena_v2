param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ResolvedDestination = [System.IO.Path]::GetFullPath($Destination)
if ($ResolvedDestination -eq [System.IO.Path]::GetFullPath($Root)) { throw "Destination must differ from repository root" }
New-Item -ItemType Directory -Path $ResolvedDestination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "storage") -Destination $ResolvedDestination -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root "memory") -Destination $ResolvedDestination -Recurse -Force
