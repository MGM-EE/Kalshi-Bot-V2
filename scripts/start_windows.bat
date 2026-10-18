@echo off
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup_windows.ps1 first.
  exit /b 1
)
".venv\Scripts\python.exe" scripts\launch.py %*
