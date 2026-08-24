# Registreert een Windows Task Scheduler taak voor het wekelijkse GA rapport.
# Draai dit script eenmalig als Administrator.
# Fallback voor wanneer de FastAPI app om 08:00 nog niet actief is.

$ProjectDir = "D:\apps\impactos"
$PythonExe  = "$ProjectDir\.venv\Scripts\python.exe"
$Script     = "$ProjectDir\scripts\run_weekly_analytics.py"
$TaskName   = "ImpactOS - Weekly GA Report"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python venv niet gevonden op $PythonExe. Start eerst .\start.ps1 zodat de venv aangemaakt wordt."
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Script `
    -WorkingDirectory $ProjectDir

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday `
    -At "08:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "Taak aangemaakt: '$TaskName'"
Write-Host "  Draait elke maandag om 08:00 (ook als de app offline is)"
Write-Host ""
Write-Host "Handmatig testen:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
