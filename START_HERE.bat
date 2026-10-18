@echo off
cd /d "%~dp0"
echo Setting up Kalshi Terminal. Existing paper accounts and credentials are preserved.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the error above before continuing.
  pause
  exit /b 1
)
echo.
echo Next: open CONNECT_KALSHI.bat to connect your key, then OPEN_TERMINAL.bat.
pause
