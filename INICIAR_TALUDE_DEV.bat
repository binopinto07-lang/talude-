@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "TALUDE_ROOT=%CD%"
set "LOG_DIR=%TALUDE_ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\INICIAR_TALUDE_V3.log"
echo ===== TALUDE STUDIO V3 - SOURCE MODE ===== > "%LOG%"
set "PYTHON=%LOCALAPPDATA%\LBM\py312\python.exe"
if not exist "%PYTHON%" (
  where py >nul 2>nul
  if errorlevel 1 (
    echo [ERRO] Python 3.12 nao encontrado. Execute LocalBuildManager__TALUDE_STUDIO\START_LOCAL_BUILD_MANAGER.bat
    goto :failed
  )
  set "PYTHON=py -3.12"
)
%PYTHON% -c "import sys; assert sys.version_info[:2] == (3,12)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERRO] E necessario Python 3.12 x64. Consulte %LOG%.
  goto :failed
)
%PYTHON% -m pip --version >> "%LOG%" 2>&1
if errorlevel 1 (
  echo A preparar pip no Python 3.12...
  %PYTHON% -m ensurepip --upgrade >> "%LOG%" 2>&1
  if errorlevel 1 goto :failed
)
%PYTHON% -c "import PySide6.QtWebEngineWidgets, numpy, scipy, laspy, rasterio, shapely, ezdxf, fastapi, uvicorn" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo A instalar dependencias V3 (primeira execucao)...
  %PYTHON% -m pip install -r requirements-v3.txt >> "%LOG%" 2>&1
  if errorlevel 1 goto :failed
)
%PYTHON% -c "import sys;sys.path.insert(0,'.');from studio.backend.v3_algorithms import available_algorithms;print(available_algorithms())" >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
set "PYTHONPATH=%TALUDE_ROOT%;%TALUDE_ROOT%\src"
echo A iniciar TALUDE STUDIO V3...
%PYTHON% talude_studio.py >> "%LOG%" 2>&1
if errorlevel 1 goto :failed
echo Aplicacao fechada normalmente. Log: %LOG%
exit /b 0
:failed
echo.
echo [ERRO] Preparacao ou arranque falhou. Ver: %LOG%
type "%LOG%"
pause
exit /b 1
