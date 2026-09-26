@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -e .
)
.venv\Scripts\python.exe -m talude_v1 %*
endlocal
