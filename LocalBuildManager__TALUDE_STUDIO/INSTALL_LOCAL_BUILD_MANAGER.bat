@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Keep installed application path short too. The Python environment is separate in %LOCALAPPDATA%\LBM\m.
set "DEST=%LOCALAPPDATA%\LBM\app"
echo ================================================
echo   LOCAL BUILD MANAGER - INSTALADOR WINDOWS
echo ================================================
echo.
echo Destino: %DEST%
echo Ambiente Python: %LOCALAPPDATA%\LBM\m
echo.

if not exist "%DEST%" mkdir "%DEST%"
robocopy "%~dp0" "%DEST%" /E /XD .venv build dist __pycache__ .pytest_cache /XF settings.json >nul
if errorlevel 8 (
  echo [ERRO] Falha ao copiar ficheiros.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Local Build Manager.lnk');" ^
  "$s.TargetPath = '%DEST%\START_LOCAL_BUILD_MANAGER.bat';" ^
  "$s.WorkingDirectory = '%DEST%';" ^
  "$s.Description = 'Local Build Manager - CI e builds locais';" ^
  "$s.Save()"

if errorlevel 1 (
  echo [AVISO] Aplicacao copiada, mas nao foi possivel criar o atalho.
) else (
  echo [OK] Atalho criado no Ambiente de Trabalho.
)

echo [OK] Instalado em %DEST%
echo.
echo Pode iniciar por "Local Build Manager" no Ambiente de Trabalho.
pause
