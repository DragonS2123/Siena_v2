[CmdletBinding()]
param()

$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$Validator=Join-Path $PSScriptRoot 'validate_siena_wolvenkit_assets.ps1'
$Canonical=@(
  (Join-Path $Root 'assets\siena_npc\source\r6\tweaks\siena_npc.yaml'),
  (Join-Path $Root 'wolvenkit\siena_npc\source\resources\r6\tweaks\siena_npc.yaml')
)
$Packed=Join-Path $Root 'wolvenkit\siena_npc\packed\r6\tweaks\siena_npc.yaml'
foreach($Path in $Canonical+$Packed){if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){throw "Regression fixture path missing: $Path"}}

$All=@(Get-ChildItem -LiteralPath (Join-Path $Root 'assets'),(Join-Path $Root 'wolvenkit') -Recurse -File -Include '*.yaml','*.yml'|Where-Object{([regex]::Matches((Get-Content -LiteralPath $_.FullName -Raw),'(?m)^Siena\.SienaCompanion:\s*$')).Count-gt0})
if($All.Count-ne3){throw "Regression requires two canonical definitions plus one generated packed copy; found $($All.Count)."}

$Output=& $Validator -AllowIncomplete 6>&1|Out-String
$CanonicalLogs=([regex]::Matches($Output,'"classification":\s*"canonical"')).Count
$GeneratedLogs=([regex]::Matches($Output,'"classification":\s*"generated"')).Count
if($CanonicalLogs-ne2){throw "Expected two canonical classification logs; found $CanonicalLogs.`n$Output"}
if($GeneratedLogs-ne1){throw "Expected one generated packed classification log; found $GeneratedLogs.`n$Output"}
if($Output-match'classification=conflicting authored definition'-or$Output-notmatch'"errors":\s*\[\]'){throw "Packed copy was treated as a conflict or validation returned errors.`n$Output"}
Write-Host 'Asset validator regression passed: two canonical definitions plus one packed generated copy are accepted.'
