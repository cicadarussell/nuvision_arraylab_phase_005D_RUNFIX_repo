@echo off
setlocal
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_arraylab_launcher.ps1" -Mode dev
if errorlevel 1 (
  echo.
  echo ArrayLab stopped because setup/start failed. It did NOT pretend the backend started.
  echo Run doctor.bat for the quick environment check.
  pause
)
