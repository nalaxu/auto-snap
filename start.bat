@echo off
rem Double-click to start the panel. Arguments pass through to start.ps1 (e.g. start.bat -Rebuild -NoBrowser)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
