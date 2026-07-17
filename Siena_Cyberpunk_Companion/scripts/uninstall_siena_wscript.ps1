[CmdletBinding(SupportsShouldProcess=$true)]
param()
$ErrorActionPreference='Stop';if(Get-Process -Name 'WolvenKit' -ErrorAction SilentlyContinue){throw 'Close WolvenKit before removing the WScript.'};$Target=Join-Path $env:APPDATA 'REDModding\WolvenKit\WScript\CreateSienaStandaloneAssets.wscript'
if(Test-Path -LiteralPath $Target){if($PSCmdlet.ShouldProcess($Target,'Remove only Siena WScript')){Remove-Item -LiteralPath $Target -Force;Write-Host "Removed: $Target"}}else{Write-Host 'Siena WScript is not installed.'}
