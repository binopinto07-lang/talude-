@echo off
setlocal
cd /d "%~dp0"
title TALUDE STUDIO - DEV PORTATIL
if not exist "scripts\dev_portable.ps1" (
    echo [ERRO] scripts\dev_portable.ps1 nao encontrado.
    echo Extraia todo o ZIP, nunca execute o BAT dentro do ficheiro compactado.
    pause
    exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev_portable.ps1" -Action run
set "DEV_EXIT=%ERRORLEVEL%"
if not "%DEV_EXIT%"=="0" (
    echo.
    echo [ERRO] O Talude Studio DEV terminou com codigo %DEV_EXIT%.
    echo Consulte .talude_dev\logs\ para diagnosticos.
    pause
)
exit /b %DEV_EXIT%
