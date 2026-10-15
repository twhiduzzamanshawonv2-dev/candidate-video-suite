@echo off
title Candidate Video Helper
rem Installs and starts the local helper (private Python + FFmpeg, no admin, no winget).
rem Uses the helper.py in the web folder. Closes by itself on success; stays open if anything fails.
set CVH_FROM_BAT=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0web\install.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the messages above, then press any key to close.
  pause >nul
)
