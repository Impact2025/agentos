# Registreert een Windows Task Scheduler taak die de Mission Control launcher
# (D:\apps\impactos\launcher\server.py) bij elke aanmelding op de achtergrond
# start. De launcher draait altijd op 127.0.0.1:8088 en biedt de "Impact OS"
# knop die Hermes + Impact OS server automatisch opstart als ze down zijn.
#
# Draai dit script eenmalig als Administrator:
#   powershell -ExecutionPolicy Bypass -File setup_launcher_task.ps1

$ProjectDir = "D:\apps\impactos"
$PythonExe  = "$ProjectDir\.venv\Scripts\python.exe"
$Launcher   = "$ProjectDir\launcher\server.py"
$TaskName   = "ImpactOS - Mission Control Launcher"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python venv niet gevonden op $PythonExe."
    exit 1
}
if (-not (Test-Path $Launcher)) {
    Write-Error "Launcher niet gevonden op $Launcher."
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Launcher `
    -WorkingDirectory $ProjectDir

# Bij aanmelding starten, en meteen opnieuw proberen als het mislukt.
$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `   # 0 = geen limiet (daemon)
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "Taak aangemaakt: '$TaskName'"
Write-Host "  Start bij elke Windows-aanmelding (op de achtergrond)."
Write-Host ""
Write-Host "Missie Controle dashboard:  http://127.0.0.1:8088"
Write-Host "Impact OS (na knop):         http://localhost:1250"
Write-Host ""
Write-Host "Handmatig starten/testen:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
