param([string]$Scenario='exploration',[double]$Speed=1.0,[switch]$Loop)

# Validate the scenario against simulator/scenarios/*.json.
$ScenarioDirectory = Join-Path $PSScriptRoot "..\simulator\scenarios"
$ScenarioValidationPath = Join-Path $ScenarioDirectory ($Scenario + ".json")

if (-not (Test-Path -LiteralPath $ScenarioValidationPath -PathType Leaf)) {
    $AvailableScenarios = @(
        Get-ChildItem `
            -LiteralPath $ScenarioDirectory `
            -Filter "*.json" `
            -File `
            -ErrorAction SilentlyContinue |
        Sort-Object BaseName |
        Select-Object -ExpandProperty BaseName
    )

    $AvailableText = if ($AvailableScenarios.Count -gt 0) {
        $AvailableScenarios -join "; "
    }
    else {
        "<none>"
    }

    throw "Scenario '$Scenario' not found. Available: $AvailableText"
}

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Missing project .venv. Follow README installation steps.' }
Set-Location (Join-Path $Root 'simulator')
$Arguments = @('main.py','--scenario',$Scenario,'--speed',$Speed.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($Loop) { $Arguments += '--loop' }
& $Python @Arguments
