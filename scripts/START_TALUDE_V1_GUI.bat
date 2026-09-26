@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :error

.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist "studio\vendor\potree\build\potree\potree.js" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "studio\scripts\bootstrap_vendor.ps1"
  if errorlevel 1 goto :error
)

start "Talude Studio" .venv\Scripts\pythonw.exe talude_studio.py
exit /b 0

:error
echo.
echo [ERRO] Falha ao preparar Talude Studio.
pause
exit /b 1
