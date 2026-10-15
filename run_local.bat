@echo off
:: Serves the web UI locally at http://localhost:8000 (no install needed beyond Python)
set "PY_CMD=python"
python --version >nul 2>&1 || set "PY_CMD=py"

echo Opening http://localhost:8000  (keep this window open, close it to stop)
start "" http://localhost:8000
%PY_CMD% -m http.server 8000 --bind 127.0.0.1 --directory "%~dp0web"
pause
