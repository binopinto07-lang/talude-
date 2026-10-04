@echo off
setlocal EnableExtensions
set "LBM_EMBEDDED_REPO=%~dp0"
set "LBM_EMBEDDED_PROJECT_ID=talude_v1"
call "%~dp0tools\LocalBuildManager_TALUDE\START_LOCAL_BUILD_MANAGER.bat"
exit /b %ERRORLEVEL%
