@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
endlocal
