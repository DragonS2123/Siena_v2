[CmdletBinding()]
param(
  [string]$GamePath,
  [switch]$AllowIncomplete
)

$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$ManifestPath=Join-Path $Root 'assets\siena_npc\asset-manifest.json'
$Manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
$ProjectRoot=Join-Path $Root 'wolvenkit\siena_npc'
$ArchiveSource=Join-Path $ProjectRoot 'source\archive'
$ResourceSource=Join-Path $ProjectRoot 'source\resources'
$CanonicalTweak=Join-Path $Root 'assets\siena_npc\source\r6\tweaks\siena_npc.yaml'
$ProjectTweak=Join-Path $ResourceSource 'r6\tweaks\siena_npc.yaml'
$Entity=Join-Path $ArchiveSource 'siena\entities\siena_default.ent'
$Appearance=Join-Path $ArchiveSource 'siena\appearances\siena_default.app'
$PackedRoot=Join-Path $ProjectRoot 'packed'
$ControllerConfig=Join-Path $Root 'bridge\cet\siena_npc_controller\config.lua'
$ControllerEntity=Join-Path $Root 'bridge\cet\siena_npc_controller\npc_entity.lua'
$Errors=[Collections.Generic.List[string]]::new()
$Blockers=[Collections.Generic.List[string]]::new()
$DefinitionPathLog=[Collections.Generic.List[object]]::new()

function Test-Cr2w([string]$Path,[int64]$MinimumLength=16){
  if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){return $false}
  $Bytes=[IO.File]::ReadAllBytes($Path)
  return $Bytes.Length-ge$MinimumLength-and[Text.Encoding]::ASCII.GetString($Bytes,0,4)-eq'CR2W'
}
function Add-Error([string]$Message){$Errors.Add($Message)}
function Add-Blocker([string]$Message){$Blockers.Add($Message)}
function Get-NormalizedPath([string]$Path){return [IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()}
function Get-TweakPathKind([string]$Path,[string]$ActiveGameRoot){
  $Normalized=Get-NormalizedPath $Path
  if($Normalized-eq(Get-NormalizedPath $CanonicalTweak)-or$Normalized-eq(Get-NormalizedPath $ProjectTweak)){return 'canonical'}
  if($ActiveGameRoot-and$Normalized.StartsWith((Get-NormalizedPath $ActiveGameRoot)+'\')){return 'generated'}
  if($Normalized-match'\\(packed|dist|build|out)\\'){return 'generated'}
  if($Normalized-match'\\[^\\]*(backup|recovery)[^\\]*\\'-or$Normalized-match'\\(node_modules|\.git)\\'-or$Normalized-match'\.(bak|backup)(-|\.|$)'){return 'ignored'}
  return 'authored'
}
function Write-DefinitionClassification([string]$Path,[string]$Classification,[int]$Count,[string]$Reason){
  $Entry=[pscustomobject]@{path=$Path;classification=$Classification;definition_count=$Count;reason=$Reason}
  $DefinitionPathLog.Add($Entry)
  Write-Host "[TweakXL] classification=$Classification definitions=$Count path=$Path reason=$Reason"
}
function Get-YamlCandidates([string]$Path){
  if(-not(Test-Path -LiteralPath $Path -PathType Container)){return}
  foreach($Item in Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue){
    if($Item.PSIsContainer){
      if($Item.Name-in @('.git','node_modules','.venv','.pytest_cache')){Write-DefinitionClassification $Item.FullName 'ignored' 0 'directory excluded from authored-source scan';continue}
      Get-YamlCandidates $Item.FullName
    }elseif($Item.Extension-in @('.yaml','.yml')){$Item}
  }
}

if($Manifest.package_version-ne'0.10.0'-or$Manifest.stable_tweakdb_record-ne'Siena.SienaCompanion'-or$Manifest.target_appearance_name-ne'siena_default'){Add-Error 'Asset manifest stable identity is invalid.'}
$Confirmed=@(
  @{Path=(Join-Path $ArchiveSource 'ep1\characters\entities\citizen\citizen__ep1_rich_wa.ent');Hash='8EECF5884FD0AAB60AA652953250F68ECD0A77C26E38307DD6D072ED34B60E56'},
  @{Path=(Join-Path $ArchiveSource 'base\characters\appearances\citizen\citizen__rich_wa.app');Hash='4C437D28462DAC23BF8863EEA0501CD2FD9FDC593477166A14981FDBB6AC249A'}
)
foreach($Source in $Confirmed){
  if(-not(Test-Cr2w $Source.Path)){Add-Error "Confirmed source is missing or not CR2W: $($Source.Path)"}
  elseif((Get-FileHash -Algorithm SHA256 -LiteralPath $Source.Path).Hash-ne$Source.Hash){Add-Error "Confirmed source hash changed: $($Source.Path)"}
}

foreach($Tweak in $CanonicalTweak,$ProjectTweak){
  if(-not(Test-Path -LiteralPath $Tweak -PathType Leaf)){Write-DefinitionClassification $Tweak 'canonical' 0 'required file missing';Add-Error "TweakXL source missing: $Tweak";continue}
  $Text=Get-Content -LiteralPath $Tweak -Raw
  $DefinitionCount=([regex]::Matches($Text,'(?m)^Siena\.SienaCompanion:\s*$')).Count
  Write-DefinitionClassification $Tweak 'canonical' $DefinitionCount 'required authored source'
  if($DefinitionCount-ne1){Add-Error "Canonical TweakXL source must contain exactly one Siena.SienaCompanion definition; found $DefinitionCount in $Tweak"}
  if($Text-notmatch'(?m)^Siena\.SienaCompanion:\s*$'-or$Text-notmatch'(?m)^\s+\$base:\s*Character\.CitizenRichFemaleCasual\s*$'-or$Text-notmatch'(?m)^\s+entityTemplatePath:\s*siena\\entities\\siena_default\.ent\s*$'-or$Text-notmatch'(?m)^\s+appearanceName:\s*siena_default\s*$'){Add-Error "Invalid standalone TweakXL definition: $Tweak"}
  if($Text-match'(?i)PlayerPuppet|Character\.(Judy|Panam|Songbird|Rogue)|quest'){Add-Error "Player or named quest dependency in TweakXL source: $Tweak"}
}
if((Test-Path $CanonicalTweak)-and(Test-Path $ProjectTweak)){$CanonicalText=(Get-Content -LiteralPath $CanonicalTweak -Raw)-replace"`r`n","`n";$ProjectText=(Get-Content -LiteralPath $ProjectTweak -Raw)-replace"`r`n","`n";if($CanonicalText.Trim()-ne$ProjectText.Trim()){Add-Error 'Canonical and WolvenKit TweakXL sources differ.'}}
$DefinitionRoots=@($Root)
$ActiveGameRoot=$null
if($GamePath){$ActiveGameRoot=[IO.Path]::GetFullPath($GamePath).TrimEnd('\');$DefinitionRoots+=Join-Path $ActiveGameRoot 'r6\tweaks'}
$CanonicalSet=@((Get-NormalizedPath $CanonicalTweak),(Get-NormalizedPath $ProjectTweak))
$Candidates=@($DefinitionRoots|ForEach-Object{Get-YamlCandidates $_}|Sort-Object FullName -Unique)
foreach($Candidate in $Candidates){
  if((Get-NormalizedPath $Candidate.FullName)-in$CanonicalSet){continue}
  $Text=Get-Content -LiteralPath $Candidate.FullName -Raw
  $DefinitionCount=([regex]::Matches($Text,'(?m)^Siena\.SienaCompanion:\s*$')).Count
  $Kind=Get-TweakPathKind $Candidate.FullName $ActiveGameRoot
  if($Kind-eq'authored'-and$DefinitionCount-gt0){Write-DefinitionClassification $Candidate.FullName 'conflicting authored definition' $DefinitionCount 'non-canonical authored source';Add-Error "Conflicting authored Siena.SienaCompanion definition: $($Candidate.FullName)"}
  elseif($Kind-eq'authored'){Write-DefinitionClassification $Candidate.FullName 'ignored' 0 'authored YAML has no Siena.SienaCompanion definition'}
  elseif($Kind-eq'generated'){Write-DefinitionClassification $Candidate.FullName 'generated' $DefinitionCount 'generated/output copy is not a competing source'}
  else{Write-DefinitionClassification $Candidate.FullName 'ignored' $DefinitionCount 'backup/recovery/dependency path excluded'}
}

$Controller=(Get-Content -LiteralPath $ControllerConfig -Raw)+(Get-Content -LiteralPath $ControllerEntity -Raw)
if($Controller-notmatch'entity_record\s*=\s*"Siena\.SienaCompanion"'-or$Controller-notmatch'TweakDB\.GetRecord'-or$Controller-notmatch'standalone_record_unavailable'){Add-Error 'Controller does not fail closed on the standalone record.'}
if($Controller-match'TweakDBID\.new\("Character\.CitizenRichFemaleCasual"\)|spec\.appearanceName|PlayerPuppet.*CreateEntity'){Add-Error 'Controller contains a generic/player spawn path or an unconfirmed appearance field.'}

foreach($Target in $Entity,$Appearance){
  if(-not(Test-Path -LiteralPath $Target -PathType Leaf)){Add-Blocker "Authored target missing: $Target"}
  elseif(-not(Test-Cr2w $Target)){Add-Error "Authored target is not a real CR2W resource: $Target"}
}
$Archives=@(Get-ChildItem -LiteralPath $PackedRoot -Recurse -File -Filter 'siena_npc.archive' -ErrorAction SilentlyContinue)
if($Archives.Count-eq0){Add-Blocker "No packed siena_npc.archive detected under: $PackedRoot"}
elseif($Archives.Count-ne1){Add-Error "Expected exactly one detected packed siena_npc.archive; found $($Archives.Count)."}
elseif($Archives[0].Length-lt1024){Add-Error "Packed archive is unexpectedly small: $($Archives[0].FullName)"}

if($GamePath){
  $Game=[IO.Path]::GetFullPath($GamePath).TrimEnd('\')
  if(-not(Test-Path -LiteralPath (Join-Path $Game 'bin\x64\Cyberpunk2077.exe') -PathType Leaf)){Add-Error "Invalid game root: $Game"}
  else{
    $ActiveCet=@(Get-ChildItem -LiteralPath (Join-Path $Game 'bin\x64\plugins\cyber_engine_tweaks\mods') -Directory -ErrorAction SilentlyContinue|Where-Object{$_.Name-match'(?i)siena.*npc|npc.*siena'})
    $ActiveArchives=@(Get-ChildItem -LiteralPath (Join-Path $Game 'archive\pc\mod') -File -ErrorAction SilentlyContinue|Where-Object{$_.Name-match'(?i)siena.*npc.*\.(archive|xl)$'})
    $ActiveTweaks=@(Get-ChildItem -LiteralPath (Join-Path $Game 'r6\tweaks') -Directory -ErrorAction SilentlyContinue|Where-Object{$_.Name-match'(?i)siena.*npc|npc.*siena'})
    if($ActiveCet.Count-gt1-or$ActiveArchives.Count-gt2-or$ActiveTweaks.Count-gt1){Add-Error 'Duplicate active Siena asset/controller package detected in the game tree.'}
  }
}

[pscustomobject]@{
  identity='Siena.SienaCompanion / siena_default'
  confirmed_sources_valid=($Errors.Count-eq0)
  authored_targets_present=((Test-Path $Entity)-and(Test-Path $Appearance))
  packed_archive_count=$Archives.Count
  tweakxl_definition_paths=@($DefinitionPathLog)
  non_runtime_validation='CR2W headers/hashes, paths, TweakXL identity, duplicate definitions, controller boundary'
  wolvenkit_internal_validation='NOT RUN: requires WolvenKit GUI File Validation'
  errors=@($Errors)
  blockers=@($Blockers)
}|ConvertTo-Json -Depth 4

if($Errors.Count){throw "Siena asset validation failed: $($Errors -join ' | ')"}
if($Blockers.Count-and-not$AllowIncomplete){throw "Siena asset authoring/build incomplete: $($Blockers -join ' | ')"}
