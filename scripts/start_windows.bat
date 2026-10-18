@echo off
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup is needed. Open START_HERE.bat in the project folder.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\launch.py %*
if errorlevel 1 (
  echo.
  echo The app could not start. Keep this window open and copy the error above.
  pause
)
