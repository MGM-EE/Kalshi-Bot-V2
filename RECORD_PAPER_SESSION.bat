@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\record_windows.ps1"
if errorlevel 1 pause
