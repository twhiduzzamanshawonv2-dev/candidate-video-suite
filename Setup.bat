@echo off
title Candidate Video Helper Setup
set CVH_FROM_BAT=1
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm https://candidate-video-suite.vercel.app/install.ps1 | iex"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the messages above, then press any key to close.
  pause >nul
)
