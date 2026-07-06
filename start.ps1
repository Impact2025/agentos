#Requires -Version 5.1

$ErrorActionPreference = "Stop"
$Root    = $PSScriptRoot
$VenvDir = Join-Path $Root ".venv"
$EnvFile = Join-Path $Root ".env"

Write-Host ""
Write-Host "  Agent OS -- Mission Control" -ForegroundColor Cyan
Write-Host "  ----------------------------" -ForegroundColor DarkGray
Write-Host ""

# .env controleren
if (-not (Test-Path $EnvFile)) {
    Write-Host "  [WAARSCHUWING] .env niet gevonden." -ForegroundColor Yellow
    $example = Join-Path $Root ".env.example"
    if (Test-Path $example) {
        Copy-Item $example $EnvFile
        Write-Host "  .env.example gekopieerd naar .env" -ForegroundColor Yellow
        Write-Host "  -> Open .env en vul je API keys in." -ForegroundColor Yellow
        Write-Host ""
    }
}

# Python controleren
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.([89]|1[0-9])") {
            $python = $cmd
            Write-Host "  [OK] Python gevonden: $ver" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "  [FOUT] Python 3.8+ niet gevonden. Installeer via https://python.org" -ForegroundColor Red
    exit 1
}

# Virtuele omgeving
if (-not (Test-Path (Join-Path $VenvDir "Scripts\activate.ps1"))) {
    Write-Host "  -> Virtuele omgeving aanmaken..." -ForegroundColor DarkGray
    & $python -m venv $VenvDir
    Write-Host "  [OK] Venv aangemaakt" -ForegroundColor Green
} else {
    Write-Host "  [OK] Venv gevonden" -ForegroundColor Green
}

$pip     = Join-Path $VenvDir "Scripts\pip.exe"
$uvicorn = Join-Path $VenvDir "Scripts\uvicorn.exe"

# Dependencies installeren
$stamped = Join-Path $VenvDir ".installed_stamp"
$reqFile = Join-Path $Root "requirements.txt"
$needInstall = (-not (Test-Path $stamped)) -or ((Get-Item $reqFile).LastWriteTime -gt (Get-Item $stamped).LastWriteTime)
if ($needInstall) {
    Write-Host "  -> Dependencies installeren..." -ForegroundColor DarkGray
    & $pip install -q -r $reqFile
    New-Item -ItemType File -Force $stamped | Out-Null
    Write-Host "  [OK] Dependencies geinstalleerd" -ForegroundColor Green
} else {
    Write-Host "  [OK] Dependencies up-to-date (stamp)" -ForegroundColor Green
}

# Structuur info
Write-Host ""
Write-Host "  Domeinen: chat / pipeline / prospecting / seo / delegate /" -ForegroundColor DarkGray
Write-Host "            loop / finance / analytics / publish / outlook" -ForegroundColor DarkGray
Write-Host "  Projecten:" -ForegroundColor DarkGray
Get-ChildItem (Join-Path $Root "projects") -Directory | ForEach-Object {
    Write-Host "            - $($_.Name)" -ForegroundColor DarkGray
}

# Server starten
Write-Host ""
Write-Host "  -----------------------------------------" -ForegroundColor DarkGray
Write-Host "  Dashboard:  http://localhost:1250" -ForegroundColor Cyan
Write-Host "  API docs:   http://localhost:1250/docs" -ForegroundColor Cyan
Write-Host "  Projects:   http://localhost:1250/api/projects" -ForegroundColor Cyan
Write-Host "  Stop:       Ctrl+C" -ForegroundColor DarkGray
Write-Host "  -----------------------------------------" -ForegroundColor DarkGray
Write-Host ""

Set-Location $Root
& $uvicorn backend.main:app --host localhost --port 1250
