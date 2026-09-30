@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0START_LOCAL_BUILD_MANAGER_SETUP_ONLY.bat"
if errorlevel 1 exit /b 1

set "LBM_PY=%LOCALAPPDATA%\LBM\m\Scripts\python.exe"
set "TEMP=%LOCALAPPDATA%\LBM\t"
set "TMP=%LOCALAPPDATA%\LBM\t"

"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir "pyinstaller>=6,<7"
if errorlevel 1 exit /b 1

"%LBM_PY%" -m PyInstaller --noconfirm --clean --windowed --name LocalBuildManager --add-data "projects;projects" --collect-all PySide6 run.py
if errorlevel 1 (
  echo [ERRO] Falha no PyInstaller.
  pause
  exit /b 1
)

echo.
echo [OK] EXE criado em dist\LocalBuildManager\LocalBuildManager.exe
pause
