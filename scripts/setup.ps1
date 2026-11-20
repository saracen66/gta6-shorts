# One-time setup on Windows: venv + deps + workspace. Usage: powershell -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$skill = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg not found on PATH. Install it (winget install Gyan.FFmpeg) and re-run."
}
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Python 3.10+ is required (winget install Python.Python.3.12)" }
if (-not (Test-Path "$skill\.venv")) { & $py -m venv "$skill\.venv" }
& "$skill\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$skill\.venv\Scripts\python.exe" -m pip install -q -r "$skill\requirements.txt"
Push-Location $skill
& "$skill\.venv\Scripts\python.exe" -m gta6clips init
Pop-Location
Write-Host "Setup done. Fill in the .env shown above, then run: scripts\gta6.ps1 doctor"
