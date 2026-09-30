@echo off
setlocal EnableExtensions
set "LBM_ROOT=%LOCALAPPDATA%\LBM"
echo ================================================
echo  REPARAR AMBIENTE - LOCAL BUILD MANAGER
 echo ================================================
echo.
echo Esta operacao apaga apenas o ambiente Python e TEMP do gestor:
echo   %LBM_ROOT%\m
echo   %LBM_ROOT%\t
echo.
choice /C SN /N /M "Continuar? [S/N]: "
if errorlevel 2 exit /b 0
if exist "%LBM_ROOT%\m" rmdir /S /Q "%LBM_ROOT%\m"
if exist "%LBM_ROOT%\t" rmdir /S /Q "%LBM_ROOT%\t"
echo [OK] Ambiente removido. Execute START_LOCAL_BUILD_MANAGER.bat para recriar.
pause
