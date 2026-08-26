$ErrorActionPreference = 'SilentlyContinue'
Write-Host "=== VERIFICATIE FIXES ==="

# 1. Hermes_Gateway.cmd uses pythonw.exe
$g = Get-Content "C:\Users\v_mun\AppData\Local\hermes\gateway-service\Hermes_Gateway.cmd" -Raw
Write-Host "[1] Hermes_Gateway.cmd uses pythonw.exe: " ($g -match 'pythonw\.exe')
Write-Host "    uses python.exe (should be False): "  ($g -match 'python\.exe' | Where-Object { $g -notmatch 'pythonw\.exe' })

# 2. Hermes_Gateway.vbs uses pythonw.exe
$v = Get-Content "C:\Users\v_mun\AppData\Local\hermes\gateway-service\Hermes_Gateway.vbs" -Raw
Write-Host "[2] Hermes_Gateway.vbs uses pythonw.exe: " ($v -match 'pythonw\.exe')

# 3. finance-expert.cmd exists + uses pythonw.exe
$fe = "C:\Users\v_mun\AppData\Local\hermes\gateway-service\Hermes_Gateway_finance-expert.cmd"
Write-Host "[3] Hermes_Gateway_finance-expert.cmd exists: " (Test-Path $fe)
Write-Host "    uses pythonw.exe: " ((Get-Content $fe -Raw 2>$null) -match 'pythonw\.exe')

# 4. _restart_impactos.py uses CREATE_NO_WINDOW
$r = Get-Content "D:/APPS/agentos/_restart_impactos.py" -Raw
Write-Host "[4] _restart_impactos.py CREATE_NO_WINDOW: " ($r -match '0x00000008')

# 5. impactos_service.cmd notebooklm /min
$s = Get-Content "D:/APPS/agentos/impactos_service.cmd" -Raw
Write-Host "[5] impactos_service.cmd notebooklm /min: " ($s -match '/min')

# 6. launch.ps1 Start-Process WindowStyle Hidden
$l = Get-Content "D:/APPS/agentos/launch.ps1" -Raw
Write-Host "[6] launch.ps1 Start-Process -WindowStyle Hidden: " ($l -match 'Start-Process.*WindowStyle Hidden')

# 7. start_omniroute.ps1 uses pythonw.exe
$o = Get-Content "D:/APPS/llm-proxy/start_omniroute.ps1" -Raw
Write-Host "[7] start_omniroute.ps1 uses pythonw.exe: " ($o -match 'pythonw\.exe')

# 8. start_supervisor_startup.ps1 uses pythonw.exe + dupe check
$ss = Get-Content "D:/APPS/llm-proxy/start_supervisor_startup.ps1" -Raw
Write-Host "[8] start_supervisor_startup.ps1 pythonw: " ($ss -match 'pythonw\.exe')
Write-Host "    has dupe check: " ($ss -match 'sup_running')

# 9. AgentOS.lnk repointed
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut('C:\Users\v_mun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\AgentOS.lnk')
Write-Host "[9] AgentOS.lnk target: " $lnk.TargetPath
Write-Host "    target exists: " (Test-Path $lnk.TargetPath)

Write-Host "`n=== ACTIEVE python* processen (MainWindowHandle check) ==="
Get-Process -Name python* -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Measure-Object | Select-Object -ExpandProperty Count |
    ForEach-Object { Write-Host "    python* met zichtbaar console: $_" }
$all = Get-Process -Name python* -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count
Write-Host "    totaal python* processen: $all (0 met zichtbaar console = geen popups)"
