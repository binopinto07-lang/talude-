@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LBM_EMBEDDED_REPO=%~dp0"
set "LBM_EMBEDDED_PROJECT_ID=talude_v1"
call "%~dp0tools\LocalBuildManager_TALUDE\START_LOCAL_BUILD_MANAGER.bat"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo O arranque falhou. Consulte LOCAL_BUILDER_STARTUP.log nesta pasta.
  pause
)
exit /b %RC%
