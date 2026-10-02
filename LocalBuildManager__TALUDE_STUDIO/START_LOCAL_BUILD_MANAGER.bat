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
if errorlevel 1 (
  echo.
  echo [ERRO] Nao foi possivel preparar Python 3.12 automaticamente.
  echo [INFO] Verifique a ligacao a Internet e tente novamente.
  pause
  exit /b 1
)

if not exist "%LBM_PY%" (
  echo [INFO] A preparar ambiente curto do Local Build Manager...
  echo [INFO] Ambiente: %LBM_VENV%
  "%LBM_BASE_PY%" -m venv "%LBM_VENV%"
  if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar o ambiente Python do gestor.
    pause
    exit /b 1
  )
)

"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir --upgrade pip setuptools wheel
if errorlevel 1 goto :pip_error

"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir -r "%~dp0requirements.txt"
if errorlevel 1 goto :pip_error

"%LBM_PY%" "%~dp0run.py"
if errorlevel 1 pause
exit /b 0

:pip_error
echo.
echo [ERRO] Falha ao instalar dependencias do Local Build Manager.
echo [INFO] Python usado: %LBM_PY%
echo [INFO] TEMP usado: %LBM_TEMP%
pause
exit /b 1
