@echo off
setlocal enabledelayedexpansion

:: Starts the local helper used by the Vercel web UI (http://127.0.0.1:8765)

echo ========================================
echo   Candidate Video Helper - Auto Setup
echo ========================================

:: Python
set "PY_CMD=python"
python --version >nul 2>&1 || set "PY_CMD=py"
!PY_CMD! --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [-] Python not found. Installing Python 3.12 via winget...
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    for /f "tokens=*" %%a in ('powershell -Command "[System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')"') do set "PATH=%%a"
    set "PY_CMD=python"
    python --version >nul 2>&1 || set "PY_CMD=py"
)

:: FFmpeg
ffmpeg -version >nul 2>&1
if %errorLevel% neq 0 (
    echo [-] FFmpeg not found. Installing via winget...
    winget install --id GYAN.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    for /f "tokens=*" %%a in ('powershell -Command "[System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')"') do set "PATH=%%a"
)

echo [+] Installing libraries...
!PY_CMD! -m pip install --upgrade pip
!PY_CMD! -m pip install fastapi uvicorn requests

echo [+] Starting helper. Keep this window open while using the web page.
!PY_CMD! "%~dp0web\helper.py"

pause

