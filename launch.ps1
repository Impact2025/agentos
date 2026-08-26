#Requires -Version 5.1
# Impact OS Launcher
# Opstartvolgorde: Hermes gateway -> Hermes API (8642) -> Impact OS (1250) -> browser

$HermesGatewayCmd = "C:\Users\v_mun\AppData\Local\hermes\gateway-service\Hermes_Gateway.cmd"
$HermesPid        = "C:\Users\v_mun\AppData\Local\hermes\gateway.pid"
$ImpactRoot       = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ImpactOsServiceCmd = Join-Path $ImpactRoot "impactos_service.cmd"
$ImpactOsPid       = Join-Path $ImpactRoot "impactos.pid"
$HermesApiUrl     = "http://127.0.0.1:8642/health"
$ImpactOsUrl       = "http://localhost:1250/api/status"
$HermesApiKey     = "impactos-hermes-local-2026"

function Test-Port {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 `
             -Headers @{Authorization = "Bearer $HermesApiKey"} -ErrorAction Stop
        return $r.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Test-ImpactOs {
    try {
        $r = Invoke-WebRequest -Uri $ImpactOsUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-For {
    param([scriptblock]$Check, [int]$Seconds = 30, [string]$Label = "")
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (& $Check) { return $true }
        Start-Sleep -Seconds 1
        if ($Label -and ($i % 5 -eq 4)) {
            Write-Host "  wachten op $Label... ($($i+1)s)" -ForegroundColor DarkGray
        }
    }
    return $false
}

function Get-PidFromFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $raw = (Get-Content $Path -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not $raw) { return $null }
    # Nieuwere Hermes schrijft JSON ({"pid": 12345, ...}); ouder: kaal nummer.
    if ($raw -match '"pid"\s*:\s*(\d+)') { return [int]$Matches[1] }
    if ($raw -match '^\d+$')             { return [int]$raw }
    return $null
}

function Is-HermesGatewayRunning {
    $hpid = Get-PidFromFile $HermesPid
    if (-not $hpid) { return $false }
    $proc = Get-Process -Id $hpid -ErrorAction SilentlyContinue
    return $proc -ne $null
}

function Is-ImpactOsRunning {
    if (-not (Test-Path $ImpactOsPid)) { return $false }
    try {
        $pid = [int](Get-Content $ImpactOsPid -ErrorAction Stop)
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        return $proc -ne $null
    } catch { return $false }
}

# ── Stap 1: Hermes gateway starten als nodig ────────────────────────────────
Write-Host ""
Write-Host "  Impact OS + Hermes -- opstarten" -ForegroundColor Cyan
Write-Host "  --------------------------------" -ForegroundColor DarkGray
Write-Host ""

$hermesApiReady = Test-Port -Url $HermesApiUrl

if ($hermesApiReady) {
    Write-Host "  [OK] Hermes API al actief op poort 8642" -ForegroundColor Green
} else {
    if (Is-HermesGatewayRunning) {
        Write-Host "  [..] Hermes gateway actief, wachten op API..." -ForegroundColor Yellow
    } else {
        Write-Host "  [->] Hermes gateway starten..." -ForegroundColor DarkGray
        if (Test-Path $HermesGatewayCmd) {
            Start-Process "cmd.exe" -ArgumentList "/c `"$HermesGatewayCmd`"" -WindowStyle Hidden
        } else {
            Write-Host "  [!]  Hermes_Gateway.cmd niet gevonden op:" -ForegroundColor Red
            Write-Host "       $HermesGatewayCmd" -ForegroundColor Red
        }
    }

    $hermesApiReady = Wait-For -Check { Test-Port -Url $HermesApiUrl } -Seconds 30 -Label "Hermes API"
    if ($hermesApiReady) {
        Write-Host "  [OK] Hermes API actief op poort 8642" -ForegroundColor Green
    } else {
        # Gateway draait maar API server niet -- herstel door gateway opnieuw te starten
        Write-Host "  [..] API niet bereikbaar, gateway herstarten met API server..." -ForegroundColor Yellow
        $oldPid = Get-PidFromFile $HermesPid
        if ($oldPid) {
            Get-Process -Id $oldPid -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        if (Test-Path $HermesGatewayCmd) {
            Start-Process "cmd.exe" -ArgumentList "/c `"$HermesGatewayCmd`"" -WindowStyle Hidden
        }
        $hermesApiReady = Wait-For -Check { Test-Port -Url $HermesApiUrl } -Seconds 30 -Label "Hermes API (herstart)"
        if ($hermesApiReady) {
            Write-Host "  [OK] Hermes API actief op poort 8642" -ForegroundColor Green
        } else {
            Write-Host "  [!]  Hermes API niet bereikbaar na herstart" -ForegroundColor Yellow
            Write-Host "       Controleer of API_SERVER_ENABLED=true in Hermes .env staat" -ForegroundColor DarkGray
        }
    }
}

# ── Stap 2: Impact OS starten als nodig ─────────────────────────────────────
$impactOsReady = Test-ImpactOs

if ($impactOsReady) {
    Write-Host "  [OK] Impact OS al actief op poort 1250" -ForegroundColor Green
} else {
    # Zorg dat de venv en packages aanwezig zijn
    $venvPip = Join-Path $ImpactRoot ".venv\Scripts\pip.exe"
    $reqFile = Join-Path $ImpactRoot "requirements.txt"
    $stamped = Join-Path $ImpactRoot ".venv\.installed_stamp"
    if (-not (Test-Path (Join-Path $ImpactRoot ".venv\Scripts\uvicorn.exe"))) {
        Write-Host "  [->] Venv aanmaken..." -ForegroundColor DarkGray
        $python = "python"
        # Verborgen start: venv/pip zijn console-subsystem binaries; Start-Process
        # met -WindowStyle Hidden + -RedirectStandard* voorkomt zwarte popups.
        Start-Process -FilePath $python -ArgumentList "-m","venv","$(Join-Path $ImpactRoot '.venv')" -NoNewWindow -Wait -WindowStyle Hidden -RedirectStandardOutput NUL -RedirectStandardError NUL
        Start-Process -FilePath $venvPip -ArgumentList "install","-q","-r",$reqFile -NoNewWindow -Wait -WindowStyle Hidden -RedirectStandardOutput NUL -RedirectStandardError NUL
        New-Item -ItemType File -Force $stamped | Out-Null
        Write-Host "  [OK] Venv klaar" -ForegroundColor Green
    } elseif ((-not (Test-Path $stamped)) -or ((Get-Item $reqFile).LastWriteTime -gt (Get-Item $stamped).LastWriteTime)) {
        Write-Host "  [->] Dependencies installeren..." -ForegroundColor DarkGray
        Start-Process -FilePath $venvPip -ArgumentList "install","-q","-r",$reqFile -NoNewWindow -Wait -WindowStyle Hidden -RedirectStandardOutput NUL -RedirectStandardError NUL
        New-Item -ItemType File -Force $stamped | Out-Null
        Write-Host "  [OK] Dependencies klaar" -ForegroundColor Green
    }

    Write-Host "  [->] Impact OS starten (achtergrond)..." -ForegroundColor DarkGray
    $proc = Start-Process "cmd.exe" -ArgumentList "/c `"$ImpactOsServiceCmd`"" -WindowStyle Hidden -PassThru
    if ($proc) { $proc.Id | Out-File $ImpactOsPid -Encoding ascii }

    $impactOsReady = Wait-For -Check { Test-ImpactOs } -Seconds 30 -Label "Impact OS"
    if ($impactOsReady) {
        Write-Host "  [OK] Impact OS actief op poort 1250" -ForegroundColor Green
    } else {
        Write-Host "  [!]  Impact OS niet bereikbaar na 30s" -ForegroundColor Yellow
    }
}

# ── Stap 3: Browser openen ──────────────────────────────────────────────────
Write-Host ""
Write-Host "  Browser openen: http://localhost:1250" -ForegroundColor Cyan
Start-Process "http://localhost:1250"
