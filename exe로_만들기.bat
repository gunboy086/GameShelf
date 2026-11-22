@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/
  echo         and check "Add python.exe to PATH" during install.
  pause
  exit /b
)
if not exist "%~dp0GameShelf.py" (
  echo [ERROR] GameShelf.py must be in the same folder as this bat file.
  pause
  exit /b
)
echo Installing build tools...
python -m pip install PySide6 requests pillow pyinstaller
echo Building GameShelf.exe ...
set ICON=
if exist "%~dp0GameShelf.ico" set ICON=--icon "%~dp0GameShelf.ico" --add-data "%~dp0GameShelf.ico;."
python -m PyInstaller --noconfirm --noconsole --onefile --name GameShelf %ICON% "%~dp0GameShelf.py"
echo.
if exist "%~dp0dist\GameShelf.exe" (
  copy /y "%~dp0dist\GameShelf.exe" "%~dp0GameShelf.exe" >nul
  if errorlevel 1 (
    echo [WARN] Could not replace GameShelf.exe - close GameShelf and run this again.
    explorer "%~dp0dist"
  ) else (
    echo DONE: GameShelf.exe  ^(next to GameShelf_data, GameTranslator, GameCheat^)
    explorer /select,"%~dp0GameShelf.exe"
  )
) else (
  echo [FAILED] Build did not finish. Check the messages above.
)
pause
