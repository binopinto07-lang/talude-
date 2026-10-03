@echo off
cd /d "%~dp0"
if not exist "ALGORITM\CLASSIFY\classify_las_algorithm.py" (
  echo [ERRO] Pasta externa ALGORITM em falta. Extrair o ZIP completo.
  pause
  exit /b 2
)
if not exist "ALGORITM\TALUDE_AUTO\TALUDE_AUTO.py" (
  echo [ERRO] TALUDE_AUTO.py em falta na ALGORITM.
  pause
  exit /b 3
)
start "TALUDE STUDIO V3" "Talude_V3\Talude_V3.exe"
