param([ValidateSet('exploration','combat','critical_health','vehicle','companion_stuck','v03_readonly')][string]$Scenario='exploration',[double]$Speed=1.0,[switch]$Loop)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Missing project .venv. Follow README installation steps.' }
Set-Location (Join-Path $Root 'simulator')
$Arguments = @('main.py','--scenario',$Scenario,'--speed',$Speed.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($Loop) { $Arguments += '--loop' }
& $Python @Arguments
