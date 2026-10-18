$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv\Scripts\python.exe')) {
    throw 'First-time setup is needed. Open START_HERE.bat in the project folder.'
}
$sessionName = 'paper_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
Write-Host 'Starting a separate recorded paper account. Your normal paper account is preserved.'
Write-Host "Session: $sessionName. Press Ctrl+C here to stop and finish recording."
& .venv\Scripts\python.exe scripts\launch.py --record $sessionName
if ($LASTEXITCODE -ne 0) { throw 'The recorded session could not start. Stop any running terminal before recording.' }
