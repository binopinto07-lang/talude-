@echo off
setlocal
cd /d "%~dp0"
title TALUDE STUDIO - PREPARAR ZIP OFFLINE
if not exist "scripts\dev_portable.ps1" (
    echo [ERRO] O preparador scripts\dev_portable.ps1 nao foi encontrado.
    pause
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev_portable.ps1" -Action package
set "DEV_EXIT=%ERRORLEVEL%"
echo.
if "%DEV_EXIT%"=="0" (
    echo [OK] O ZIP completo foi preparado em DEV_RELEASES.
) else (
    echo [ERRO] Consulte .talude_dev\logs\.
)
pause
exit /b %DEV_EXIT%
