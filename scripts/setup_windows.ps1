$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw 'Node.js was not found. Install Node.js LTS from https://nodejs.org, then close this window and run START_HERE.bat again.'
}
if (!(Test-Path '.venv\Scripts\python.exe')) {
    if (!(Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Python was not found. Install Python 3.12 with the Python launcher, then run START_HERE.bat again.'
    }
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
& .venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& npm.cmd ci --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
& (Join-Path $PSScriptRoot 'create_desktop_shortcut.ps1')
Write-Host 'Setup complete. Your Kalshi Terminal desktop shortcut is ready.'
Write-Host 'To connect Kalshi: open CONNECT_KALSHI.bat, select your private-key file, and save the encrypted copy.'
Write-Host 'To launch: open OPEN_TERMINAL.bat or the desktop shortcut. Keep the terminal window open.'
