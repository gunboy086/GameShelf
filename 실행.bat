@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/
  echo         and check "Add python.exe to PATH" during install.
  pause
  exit /b
)
python -c "import PySide6, requests" 2>nul || (
  echo First run: installing required libraries...
  python -m pip install PySide6 requests
)
start "" pythonw "%~dp0GameShelf.py"
