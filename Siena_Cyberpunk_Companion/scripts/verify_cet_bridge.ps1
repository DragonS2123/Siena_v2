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
    'Presence client' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer\presence_client.lua'
    'Presence renderer' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer\presence_overlay.lua'
    'Presence config' = Join-Path $GameRoot 'bin\x64\plugins\cyber_engine_tweaks\mods\siena_cyberpunk_observer\presence_config.lua'
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
    $InitText = Get-Content -LiteralPath (Join-Path $Installed 'init.lua') -Raw
    $ClientText = Get-Content -LiteralPath (Join-Path $Installed 'presence_client.lua') -Raw -ErrorAction SilentlyContinue
    $OverlayText = Get-Content -LiteralPath (Join-Path $Installed 'presence_overlay.lua') -Raw -ErrorAction SilentlyContinue
    $ConfigText = Get-Content -LiteralPath (Join-Path $Installed 'presence_config.lua') -Raw -ErrorAction SilentlyContinue
    $StaticChecks = [ordered]@{
        'No debug.getinfo' = $InitText -notmatch 'debug\.getinfo'
        'Presence polling endpoint' = ($ConfigText -match '/api/v1/in-game-presence/current') -and ($ClientText -match 'config\.endpoint_path')
        'Presence uses async GET' = $ClientText -match 'AsyncHttpClient\.Get'
        'No HTTP in onDraw module' = $OverlayText -notmatch 'AsyncHttpClient|HttpClient\.'
        'Presence renderer exists' = $OverlayText -match '##SienaInGamePresence'
        'Presence defaults disabled' = $ConfigText -match 'enabled\s*=\s*false'
        'Remote URL disabled' = $ConfigText -match 'allow_remote_presence_url\s*=\s*false'
        'Diagnostic overlay remains gated' = $InitText -match 'if overlay_open then app:_draw_overlay\(\) end'
    }
    foreach ($StaticCheck in $StaticChecks.GetEnumerator()) {
        Write-Host ("{0,-31} {1}" -f $StaticCheck.Key, $(if($StaticCheck.Value){'OK'}else{'FAILED'}))
        if (-not $StaticCheck.Value) { $Failed = $true }
    }
}
if ($Failed) { throw 'CET bridge verification failed. No files were changed.' }
Write-Host 'CET bridge installation structure and Siena source hashes are valid.'
Write-Host 'Runtime API capabilities still require an in-game check.'
