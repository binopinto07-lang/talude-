@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not defined LBM_EMBEDDED_REPO set "LBM_EMBEDDED_REPO=%~dp0..\..\"

set "LBM_ROOT=%LOCALAPPDATA%\LBM"
set "LBM_VENV=%LBM_ROOT%\talude_manager"
set "LBM_TEMP=%LBM_ROOT%\talude_temp"
set "LBM_PY=%LBM_VENV%\Scripts\python.exe"
set "LBM_LOG=%LBM_EMBEDDED_REPO%LOCAL_BUILDER_STARTUP.log"

if not exist "%LBM_ROOT%" mkdir "%LBM_ROOT%"
if not exist "%LBM_TEMP%" mkdir "%LBM_TEMP%"
set "TEMP=%LBM_TEMP%"
set "TMP=%LBM_TEMP%"

> "%LBM_LOG%" echo ============================================================
>>"%LBM_LOG%" echo TALUDE STUDIO - LOCAL BUILD MANAGER STARTUP
>>"%LBM_LOG%" echo Repo: %LBM_EMBEDDED_REPO%
>>"%LBM_LOG%" echo Builder: %~dp0
>>"%LBM_LOG%" echo ============================================================

echo [1/5] A verificar Python 3.12...
call "%~dp0ENSURE_PYTHON_312.bat" >>"%LBM_LOG%" 2>&1
if errorlevel 1 goto :startup_error

if not exist "%LBM_PY%" (
  echo [2/5] A criar ambiente do Local Build Manager...
  >>"%LBM_LOG%" echo A criar venv: %LBM_VENV%
  "%LBM_BASE_PY%" -m venv "%LBM_VENV%" >>"%LBM_LOG%" 2>&1
  if errorlevel 1 goto :startup_error
)

echo [3/5] A preparar interface...
"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir --upgrade pip setuptools wheel >>"%LBM_LOG%" 2>&1
if errorlevel 1 goto :startup_error
"%LBM_PY%" -m pip install -q --disable-pip-version-check --no-cache-dir -r "%~dp0requirements.txt" >>"%LBM_LOG%" 2>&1
if errorlevel 1 goto :startup_error

echo [4/5] A validar fontes do gestor...
"%LBM_PY%" -m compileall -q "%~dp0app" "%~dp0run.py" >>"%LBM_LOG%" 2>&1
if errorlevel 1 goto :startup_error

echo [5/5] A abrir Local Build Manager...
"%LBM_PY%" "%~dp0run.py" >>"%LBM_LOG%" 2>&1
set "LBM_RC=%ERRORLEVEL%"
if not "%LBM_RC%"=="0" goto :startup_error
exit /b 0

:startup_error
echo.
echo ============================================================
echo [ERRO] O Local Build Manager nao conseguiu arrancar.
echo Log:
echo %LBM_LOG%
echo ============================================================
if exist "%LBM_LOG%" type "%LBM_LOG%"
echo.
pause
exit /b 1
