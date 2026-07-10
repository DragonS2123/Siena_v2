@echo off
setlocal
cd /d "%~dp0"

rem Portable launcher (0.2.0 release-blocker fix pass) - no hardcoded
rem developer-specific path, no --reload by default. Prefers
rem scripts\start_backend.ps1 (auto-detects the project venv); falls back
rem to a direct call here if PowerShell isn't available.

where powershell >nul 2>nul
if %ERRORLEVEL%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_backend.ps1"
    goto :eof
)

echo PowerShell not found on PATH - falling back to a direct interpreter call.

set "PYTHON=%~dp0.venv-faster-qwen3-tts\Scripts\python.exe"
if exist "%PYTHON%" goto :run

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYTHON=py"
    goto :run
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYTHON=python"
    goto :run
)

echo Could not find .venv-faster-qwen3-tts, "py", or "python". Install dependencies first - see docs\INSTALL.md.
exit /b 1

:run
echo Using interpreter: %PYTHON%
"%PYTHON%" -m uvicorn api.server:app --host 127.0.0.1 --port 8000
