[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$GamePath)
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot 'bridge\cet\siena_cyberpunk_observer'
$GameRoot = [IO.Path]::GetFullPath($GamePath).TrimEnd('\')
$Checks = [ordered]@{
    'Cyberpunk executable' = Join-Path $GameRoot 'bin\x64\Cyberpunk2077.exe'
    'CET root' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks'
    'CET mod init.lua' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer\init.lua'
    'Siena manifest' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer\manifest.json'
    'RED4ext' = Join-Path $GameRoot 'red4ext\RED4ext.dll'
    'redscript compiler' = Join-Path $GameRoot 'engine\tools\scc.exe'
    'RedHttpClient plugin' = Join-Path $GameRoot 'red4ext\plugins\RedHttpClient'
}
$Failed = $false
foreach ($Entry in $Checks.GetEnumerator()) {
    $Exists = Test-Path -LiteralPath $Entry.Value
    Write-Host ("{0,-24} {1}  {2}" -f $Entry.Key, $(if($Exists){'OK     '}else{'MISSING'}), $Entry.Value)
    if (-not $Exists) { $Failed = $true }
}
$RedHttpRoot = Join-Path $GameRoot 'red4ext\plugins\RedHttpClient'
if ((Test-Path -LiteralPath $RedHttpRoot -PathType Container) -and -not (Get-ChildItem -LiteralPath $RedHttpRoot -Filter '*RedHttpClient*.dll' -Recurse -File -ErrorAction SilentlyContinue)) {
    Write-Host "MISSING                  RedHttpClient DLL inside $RedHttpRoot"
    $Failed = $true
}
$Installed = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer'
if (Test-Path -LiteralPath $Installed) {
    foreach ($File in Get-ChildItem -LiteralPath $Source -File) {
        $InstalledFile = Join-Path $Installed $File.Name
        if (-not (Test-Path -LiteralPath $InstalledFile) -or (Get-FileHash -LiteralPath $File.FullName).Hash -ne (Get-FileHash -LiteralPath $InstalledFile).Hash) {
            Write-Host "CONTENT MISMATCH         $($File.Name)"
            $Failed = $true
        }
    }
}
if ($Failed) { throw 'CET bridge verification failed. No files were changed.' }
Write-Host 'CET bridge installation structure and Siena source hashes are valid.'
Write-Host 'Runtime API capabilities still require an in-game check.'
