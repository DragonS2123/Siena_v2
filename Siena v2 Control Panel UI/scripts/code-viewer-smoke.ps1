param(
  [Parameter(Mandatory = $true)]
  [string]$SmokeRoot,
  [Parameter(Mandatory = $true)]
  [string]$ReleaseFile
)

$ErrorActionPreference = "Stop"
$ResolvedSmokeRoot = [System.IO.Path]::GetFullPath($SmokeRoot)
$ResolvedTempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
if (-not $ResolvedSmokeRoot.StartsWith($ResolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not ([System.IO.Path]::GetFileName($ResolvedSmokeRoot)).StartsWith("siena_code_viewer_smoke_")) {
  throw "SmokeRoot must be a dedicated siena_code_viewer_smoke_* directory under TEMP"
}

$DesktopRoot = Split-Path $PSScriptRoot -Parent
$RepositoryRoot = Split-Path $DesktopRoot -Parent
$ScreenshotDir = Join-Path $ResolvedSmokeRoot "screenshots"
$ProfileDir = Join-Path $ResolvedSmokeRoot "electron-profile"
$HelperPath = Join-Path $ResolvedSmokeRoot "isolated_backend.py"
$BackendOut = Join-Path $ResolvedSmokeRoot "backend.out.log"
$BackendErr = Join-Path $ResolvedSmokeRoot "backend.err.log"
$ElectronOut = Join-Path $ResolvedSmokeRoot "electron.out.log"
$ElectronErr = Join-Path $ResolvedSmokeRoot "electron.err.log"
$ReadyFile = Join-Path $ResolvedSmokeRoot "ready"
$Backend = $null
$Electron = $null

try {
  New-Item -ItemType Directory -Path $ScreenshotDir -Force | Out-Null
  New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
  $Helper = @'
import os
import sys
from pathlib import Path
sys.path.insert(0, os.environ["SIENA_REPOSITORY_ROOT"])
import config

root = Path(os.environ["SIENA_SMOKE_ROOT"])
config.BASE_DIR = root
config.CONVERSATIONS_DB_PATH = root / "storage" / "conversations.sqlite3"
config.ATTACHMENTS_STORAGE_ROOT = root / "storage" / "attachments"
config.SETTINGS_STORE_PATH = root / "storage" / "settings.json"
config.SHORT_MEMORY_PATH = root / "memory" / "short_memory.json"
config.LONG_MEMORY_DB_PATH = root / "memory" / "long_memory.sqlite3"
config.CANDIDATE_MEMORY_DB_PATH = root / "memory" / "candidate_memory.sqlite3"
config.MEMORY_VECTORS_DB_PATH = root / "memory" / "memory_vectors.sqlite3"
config.TTS_OUTPUT_DIR = root / "storage" / "tts"
config.VOICE_PROFILES_PATH = root / "storage" / "voice_profiles.json"
config.LOG_DIR = root / "logs"

from storage.conversation_store import ConversationStore
store = ConversationStore(config.CONVERSATIONS_DB_PATH)
conversation_id = store.create_conversation("CODE VIEWER SMOKE")
store.append_message(conversation_id, "user", "Render smoke samples")
blocks = [
    ("html", '<!DOCTYPE html>\n<html lang="ru"><body>Cyberpunk Calculator</body></html>'),
    ("css", "body { color: #c4644a; background: #0f0e0c; }"),
    ("javascript", "const answer = () => 42;"),
    ("typescript", "interface Result { value: number }\nconst result: Result = { value: 42 };"),
    ("python", 'def greet(name: str):\n    return f"Привет, {name}"'),
    ("csharp filename=\"QuantumCircuit.cs\"", "using System;\npublic class QuantumCircuit { public int Qubits => 8; }"),
    ("json", '{"ready": true, "count": 12}'),
    ("powershell", '$items = Get-ChildItem\nWrite-Host "Готово: $($items.Count)"'),
    ("lua", "local value = 42\nprint(value)"),
    ("plaintext", "\n".join(f"long line {line:03d} " + "x" * 140 for line in range(1, 61))),
    ("bash", "#!/usr/bin/env bash\necho ready"),
    ("go", 'package main\nfunc main() { println("ready") }'),
]
content = "Code Viewer visual smoke.\n\n" + "\n\n".join(
    f"```{language}\n{code}\n```" for language, code in blocks
)
store.append_message(conversation_id, "assistant", content, model="smoke")

import uvicorn
uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=False)
'@
  [System.IO.File]::WriteAllText($HelperPath, $Helper, [System.Text.UTF8Encoding]::new($false))
  $env:SIENA_SMOKE_ROOT = $ResolvedSmokeRoot
  $env:SIENA_REPOSITORY_ROOT = $RepositoryRoot
  $env:SIENA_SHELL_DEBUG = "1"
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

  $Backend = Start-Process -FilePath "C:\Windows\py.exe" -ArgumentList @("-3", $HelperPath) `
    -WorkingDirectory $RepositoryRoot -WindowStyle Hidden `
    -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr -PassThru
  $BackendReady = $false
  for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
    try {
      $null = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 1
      $BackendReady = $true
      break
    } catch {
      Start-Sleep -Milliseconds 250
    }
  }
  if (-not $BackendReady) {
    throw "Backend failed: $([System.IO.File]::ReadAllText($BackendErr))"
  }

  $Electron = Start-Process -FilePath (Join-Path $DesktopRoot "node_modules\electron\dist\electron.exe") `
    -ArgumentList @(".", "--remote-debugging-port=9333", "--user-data-dir=$ProfileDir") `
    -WorkingDirectory $DesktopRoot -WindowStyle Hidden `
    -RedirectStandardOutput $ElectronOut -RedirectStandardError $ElectronErr -PassThru
  $DebugReady = $false
  for ($Attempt = 0; $Attempt -lt 80; $Attempt++) {
    try {
      $null = Invoke-RestMethod "http://127.0.0.1:9333/json/list" -TimeoutSec 1
      $DebugReady = $true
      break
    } catch {
      Start-Sleep -Milliseconds 250
    }
  }
  if (-not $DebugReady) {
    throw "Electron CDP failed: $([System.IO.File]::ReadAllText($ElectronErr))"
  }

  & node (Join-Path $PSScriptRoot "code-viewer-smoke.cjs") $ScreenshotDir
  if ($LASTEXITCODE -ne 0) { throw "Code Viewer CDP assertions failed" }
  New-Item -ItemType File -Path $ReadyFile -Force | Out-Null

  # Give the caller a bounded window to inspect the screenshots. Cleanup is
  # unconditional even if the caller disappears or never creates ReleaseFile.
  for ($Attempt = 0; $Attempt -lt 220 -and -not (Test-Path -LiteralPath $ReleaseFile); $Attempt++) {
    Start-Sleep -Milliseconds 250
  }
} finally {
  if ($Electron -and -not $Electron.HasExited) { Stop-Process -Id $Electron.Id -Force }
  if ($Backend -and -not $Backend.HasExited) { Stop-Process -Id $Backend.Id -Force }
  # Chromium utility processes can briefly outlive the Electron browser
  # process. Only stop descendants whose command line contains this run's
  # unique profile/helper path, then retry cleanup after handles close.
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -like "*$ProfileDir*" -or $_.CommandLine -like "*$HelperPath*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  for ($CleanupAttempt = 0; $CleanupAttempt -lt 10 -and (Test-Path -LiteralPath $ResolvedSmokeRoot); $CleanupAttempt++) {
    Start-Sleep -Milliseconds 250
    Remove-Item -LiteralPath $ResolvedSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
