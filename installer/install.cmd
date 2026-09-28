@echo off
rem Double-click to install S2 Watch. Runs install.ps1 with PowerShell (no system policy changes).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
