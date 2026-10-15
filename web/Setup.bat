@echo off
title Candidate Video Helper Setup
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm https://candidate-video-suite.vercel.app/install.ps1 | iex"
echo.
pause
