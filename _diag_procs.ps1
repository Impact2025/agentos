$agentos = @(2652, 7896, 26108, 26560, 25296)
Write-Host "=== AGENTOS PYTHON PROCESSEN ==="
foreach ($id in $agentos) {
    try {
        $p = Get-WmiObject Win32_Process -Filter "ProcessId=$id" -ErrorAction Stop
        Write-Host "PID=$id"
        Write-Host "  Path : $($p.ExecutablePath)"
        Write-Host "  CLI  : $($p.CommandLine)"
    } catch { Write-Host "PID=$id FAILED: $($_.Exception.Message)" }
}

Write-Host ""
Write-Host "=== ALLE CMD / POWERSHELL PARENTS ==="
$cmds = @(2288, 2832, 3884, 6764, 11360, 12256, 12412, 14912, 16980, 20404, 20500, 22276, 22348, 25172, 25532)
foreach ($id in $cmds) {
    try {
        $p = Get-WmiObject Win32_Process -Filter "ProcessId=$id" -ErrorAction Stop
        $short = if ($p.CommandLine.Length -gt 120) { $p.CommandLine.Substring(0, 120) + "..." } else { $p.CommandLine }
        Write-Host "PID=$id  CLI: $short"
    } catch { Write-Host "PID=$id FAILED" }
}

Write-Host ""
Write-Host "=== PROCESSEN MET VENSTER (MainWindowTitle) ==="
Get-Process python,pythonw,cmd,powershell -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -or $_.Name } |
    Select Id, Name, MainWindowTitle |
    Format-Table -AutoSize
