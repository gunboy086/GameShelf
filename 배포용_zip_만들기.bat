@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/
  pause
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_release.ps1"
pause
