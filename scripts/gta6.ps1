# Run any pipeline command with the skill's venv. Example: scripts\gta6.ps1 once
$skill = Split-Path -Parent $PSScriptRoot
Push-Location $skill
try { & "$skill\.venv\Scripts\python.exe" -m gta6clips @args; $code = $LASTEXITCODE }
finally { Pop-Location }
exit $code
