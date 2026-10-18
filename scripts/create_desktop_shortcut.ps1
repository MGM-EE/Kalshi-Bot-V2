$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $PSScriptRoot 'start_windows.bat'
if (!(Test-Path $launcher)) { throw 'Cannot find start_windows.bat.' }

# Windows resolves the actual Desktop location, including OneDrive redirection.
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Kalshi Terminal.lnk'))
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Open the Kalshi paper trading terminal'
$shortcut.Save()
Write-Host 'Created Kalshi Terminal on your desktop. Keep the project folder in its current location.'
