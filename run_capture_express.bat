@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%~dp0launch.py"
) else (
  python "%~dp0launch.py"
)

