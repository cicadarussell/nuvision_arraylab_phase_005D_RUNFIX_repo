@echo off
setlocal
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_arraylab_launcher.ps1" -Mode doctor
pause
