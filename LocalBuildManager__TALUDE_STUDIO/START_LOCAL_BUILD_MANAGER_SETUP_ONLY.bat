@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LBM_ROOT=%LOCALAPPDATA%\LBM"
set "LBM_VENV=%LBM_ROOT%\m"
set "LBM_TEMP=%LBM_ROOT%\t"
set "LBM_PY=%LBM_VENV%\Scripts\python.exe"

if not exist "%LBM_ROOT%" mkdir "%LBM_ROOT%"
if not exist "%LBM_TEMP%" mkdir "%LBM_TEMP%"
set "TEMP=%LBM_TEMP%"
set "TMP=%LBM_TEMP%"

call "%~dp0ENSURE_PYTHON_312.bat"
if errorlevel 1 exit /b 1

if not exist "%LBM_PY%" (
  "%LBM_BASE_PY%" -m venv "%LBM_VENV%" || exit /b 1
)

"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir --upgrade pip setuptools wheel || exit /b 1
"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir -r "%~dp0requirements.txt" || exit /b 1
exit /b 0
