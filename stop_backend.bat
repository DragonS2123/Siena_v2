@echo off
setlocal

set "SIENA_PORT=8000"
set "SIENA_FORCE=0"

if /I "%~1"=="--force" set "SIENA_FORCE=1"

echo [Siena] Stopping backend on port %SIENA_PORT%...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=[int]$env:SIENA_PORT; " ^
  "$force=$env:SIENA_FORCE -eq '1'; " ^
  "$conns=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; " ^
  "if (-not $conns) { Write-Host '[OK] No process is listening on backend port.'; exit 0 }; " ^
  "$exitCode=0; " ^
  "foreach ($c in $conns) { " ^
  "  $pidNum=$c.OwningProcess; " ^
  "  $proc=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $pidNum) -ErrorAction SilentlyContinue; " ^
  "  $cmd=if ($proc) { [string]$proc.CommandLine } else { '' }; " ^
  "  $isSienaBackend=($cmd -match 'uvicorn' -and $cmd -match 'api\.server:app'); " ^
  "  if ($force -or $isSienaBackend) { " ^
  "    Write-Host ('[STOP] PID ' + $pidNum + ' on port ' + $port); " ^
  "    Stop-Process -Id $pidNum -Force -ErrorAction SilentlyContinue; " ^
  "  } else { " ^
  "    Write-Host ('[WARN] Port ' + $port + ' is used by PID ' + $pidNum + ', but it does not look like Siena backend.'); " ^
  "    Write-Host ('[WARN] CommandLine: ' + $cmd); " ^
  "    Write-Host '[HINT] Run stop_backend.bat --force to kill the process anyway.'; " ^
  "    $exitCode=2; " ^
  "  } " ^
  "}; " ^
  "exit $exitCode"

set "CODE=%ERRORLEVEL%"

if "%CODE%"=="0" (
  echo [Siena] Backend stop completed.
) else (
  echo [Siena] Backend stop finished with code %CODE%.
)

exit /b %CODE%