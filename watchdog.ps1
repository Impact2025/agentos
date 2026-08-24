# Impact OS watchdog
#
# Waarom dit bestaat: tussen 20 en 27 juli 2026 stond de server drie van de acht
# dagen stil (22, 26 en 27 juli). De scheduler haalt bij het opstarten de gemiste
# runs van vandaag alsnog op, maar een dag die volledig zonder draaiende server
# voorbijgaat is onherstelbaar: gsc_sync mist die dag, en Iris' voorspellingen
# verlopen ongetoetst. Een startscript dat alleen bij logon draait is daarvoor te
# weinig - de machine sliep tussendoor.
#
# Dit script controleert of de API antwoordt en start hem anders. Het is
# idempotent: draait de server al, dan doet het niets. Bedoeld om elke paar
# minuten door Taakplanner aangeroepen te worden.

$ErrorActionPreference = 'Stop'
$Root       = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ServiceCmd = Join-Path $Root 'impactos_service.cmd'
$LogFile    = Join-Path $Root 'logs\watchdog.log'
$HealthUrl  = 'http://localhost:1250/api/status'
$LockFile   = Join-Path $Root 'logs\watchdog.lock'

function Write-Log([string]$Message) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    $dir = Split-Path -Parent $LogFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# Race-bescherming: als een eerdere watchdog-run korter dan 60s geleden een start
# in gang zette, doen we nu niets. Voorkomt dat de watchdog én de boot-taak
# tegelijk een tweede uvicorn op poort 1250 beginnen (port-conflict).
function Test-RecentStart {
    if (-not (Test-Path $LockFile)) { return $false }
    try {
        $age = (Get-Date) - (Get-Item $LockFile).LastWriteTime
        return $age.TotalSeconds -lt 60
    } catch { return $false }
}
function Touch-StartLock {
    $dir = Split-Path -Parent $LockFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    New-Item -ItemType File -Force -Path $LockFile | Out-Null
}

# Een 401 telt als gezond: de API leeft en vraagt alleen om inloggen. Alleen een
# transportfout (connection refused) betekent dat er niets luistert.
function Test-ImpactOsAlive {
    try {
        Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null
        return $true
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($resp -and $resp.StatusCode) { return $true }
        return $false
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { return $true }
        return $false
    }
}

if (Test-ImpactOsAlive) { exit 0 }

# Een andere run is korter dan 60s geleden al begonnen met starten — niet dubbel starten.
if (Test-RecentStart) { exit 0 }

Write-Log 'Impact OS antwoordt niet - opnieuw starten'
Touch-StartLock
try {
    Start-Process -FilePath $ServiceCmd -WorkingDirectory $Root -WindowStyle Hidden
} catch {
    Write-Log ('Starten mislukt: {0}' -f $_.Exception.Message)
    exit 1
}

# Even wachten en verifieren, zodat de log het verschil laat zien tussen
# "herstart gelukt" en "blijft plat" - anders is een terugkerende storing niet
# van een eenmalige blip te onderscheiden.
Start-Sleep -Seconds 25
if (Test-ImpactOsAlive) {
    Write-Log 'Herstart gelukt'
    exit 0
}
Write-Log 'Herstart gaf nog geen antwoord (server start mogelijk nog op)'
exit 0
