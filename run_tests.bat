@echo off
setlocal
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_arraylab_launcher.ps1" -Mode tests
if errorlevel 1 (
  echo.
  echo Tests failed or setup failed. Run doctor.bat for environment checks.
  pause
)
