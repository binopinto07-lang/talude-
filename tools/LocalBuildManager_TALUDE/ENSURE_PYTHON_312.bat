@echo off
rem Resolve or install a private Python 3.12 runtime for Local Build Manager.
set "LBM_BASE_PY="
set "LBM_ROOT=%LOCALAPPDATA%\LBM"
set "LBM_RUNTIME=%LBM_ROOT%\py312"
set "LBM_RUNTIME_PY=%LBM_RUNTIME%\python.exe"
set "LBM_DOWNLOADS=%LBM_ROOT%\downloads"
set "LBM_PY_INSTALLER=%LBM_DOWNLOADS%\python-3.12.10-amd64.exe"
set "LBM_PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "LBM_PY_SHA256=67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB"

if exist "%LBM_RUNTIME_PY%" (
  for /f "delims=" %%P in ('"%LBM_RUNTIME_PY%" -c "import sys; print(sys.executable)" 2^>nul') do set "LBM_BASE_PY=%%P"
  if defined LBM_BASE_PY exit /b 0
)

where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "LBM_BASE_PY=%%P"
  if defined LBM_BASE_PY exit /b 0
)

where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('python -c "import sys; print(sys.executable if sys.version_info[:2] == (3,12) else '')" 2^>nul') do if not "%%P"=="" set "LBM_BASE_PY=%%P"
  if defined LBM_BASE_PY exit /b 0
)

if not exist "%LBM_DOWNLOADS%" mkdir "%LBM_DOWNLOADS%"

echo [INFO] Python 3.12 nao encontrado. A descarregar Python 3.12.10 oficial...
if not exist "%LBM_PY_INSTALLER%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%LBM_PY_URL%' -OutFile '%LBM_PY_INSTALLER%.part'; $h=(Get-FileHash -Algorithm SHA256 '%LBM_PY_INSTALLER%.part').Hash.ToUpperInvariant(); if ($h -ne '%LBM_PY_SHA256%') { Remove-Item -Force '%LBM_PY_INSTALLER%.part'; Write-Error ('SHA256 invalido: ' + $h); exit 2 }; Move-Item -Force '%LBM_PY_INSTALLER%.part' '%LBM_PY_INSTALLER%'"
  if errorlevel 1 exit /b 1
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$h=(Get-FileHash -Algorithm SHA256 '%LBM_PY_INSTALLER%').Hash.ToUpperInvariant(); if ($h -ne '%LBM_PY_SHA256%') { Write-Error ('SHA256 invalido no instalador em cache: ' + $h); exit 2 }"
  if errorlevel 1 (
    del /q "%LBM_PY_INSTALLER%" >nul 2>&1
    exit /b 1
  )
)

echo [INFO] A instalar Python 3.12.10 privado em %LBM_RUNTIME% ...
"%LBM_PY_INSTALLER%" /quiet InstallAllUsers=0 TargetDir="%LBM_RUNTIME%" Include_doc=0 Include_debug=0 Include_dev=1 Include_exe=1 Include_launcher=0 InstallLauncherAllUsers=0 Include_lib=1 Include_pip=1 Include_symbols=0 Include_tcltk=0 Include_test=0 Include_tools=1 PrependPath=0 Shortcuts=0 AssociateFiles=0
set "LBM_INSTALL_RC=%ERRORLEVEL%"
if not "%LBM_INSTALL_RC%"=="0" if not "%LBM_INSTALL_RC%"=="3010" (
  echo [ERRO] Falha ao instalar Python 3.12.10. Codigo: %LBM_INSTALL_RC%
  exit /b 1
)

if not exist "%LBM_RUNTIME_PY%" (
  echo [ERRO] Instalador terminou mas python.exe nao existe em %LBM_RUNTIME_PY%.
  exit /b 1
)

set "LBM_BASE_PY=%LBM_RUNTIME_PY%"
echo [OK] Python gerido pronto: %LBM_BASE_PY%
exit /b 0
