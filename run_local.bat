@echo off
rem Serves the web UI locally at http://localhost:8000. Keep this window open; close it to stop.
rem Uses the private Python from start_helper.bat if it exists, otherwise Python from your PATH.
set "PY_CMD=%LOCALAPPDATA%\CandidateHelper\python\python.exe"
if not exist "%PY_CMD%" (
  set "PY_CMD=python"
  python --version >nul 2>&1 || set "PY_CMD=py"
)
"%PY_CMD%" --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Run start_helper.bat once - it installs a private Python that this file then uses.
  echo.
  echo Press any key to close.
  pause >nul
  exit /b 1
)

echo Opening http://localhost:8000  (keep this window open, close it to stop)
start "" http://localhost:8000
"%PY_CMD%" -m http.server 8000 --bind 127.0.0.1 --directory "%~dp0web"
if errorlevel 1 (
  echo.
  echo The local server stopped with an error. Read the message above, then press any key to close.
  pause >nul
)