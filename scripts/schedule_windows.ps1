# Register a Windows Task Scheduler job that runs one pipeline cycle every N minutes.
# Usage: powershell -File scripts\schedule_windows.ps1 [-Minutes 30] [-Remove]
param([int]$Minutes = 30, [switch]$Remove)
$name = "GTA6-Shorts"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "Removed $name"; exit }
$skill = Split-Path -Parent $PSScriptRoot
$ws = if ($env:GTA6_SHORTS_HOME) { $env:GTA6_SHORTS_HOME } else { Join-Path $HOME "gta6-shorts" }
$cmd = "`$env:GTA6_SHORTS_HOME='$ws'; & '$skill\scripts\gta6.ps1' once"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"Scheduled $name every $Minutes min (workspace $ws). Remove with -Remove."
