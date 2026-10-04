@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LBM_EMBEDDED_REPO=%LBM_EMBEDDED_REPO%"
if not defined LBM_EMBEDDED_REPO set "LBM_EMBEDDED_REPO=%~dp0..\..\"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3.12 "%~dp0run.py"
  if %ERRORLEVEL% EQU 0 exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo [ERRO] Python 3.12 nao encontrado.
  echo Instale Python 3.12 e tente novamente.
  pause
  exit /b 1
)
python "%~dp0run.py"
if errorlevel 1 pause
exit /b %ERRORLEVEL%
