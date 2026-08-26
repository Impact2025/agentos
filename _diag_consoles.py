import subprocess, json, os

# Check which python.exe processes have a VISIBLE console window (MainWindowHandle != 0)
ps = """
$wps = Get-Process -Name python* -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -ne '' -or $_.MainWindowHandle -ne 0 }
if ($wps) {
    $wps | Select-Object Id, Name, MainWindowTitle, MainWindowHandle,
        @{n='CLI';e={($_.CommandLine -replace '`"','')[0..160] -join ''}} |
        ConvertTo-Json -Compress -Depth 3
} else {
    Write-Output 'NONE'
}
$wps2 = Get-Process -Name python* -ErrorAction SilentlyContinue
Write-Host "`n=== ALL python* procs ==="
$wps2 | Select-Object Id,Name,MainWindowHandle,@{n='MW';e={if($_.MainWindowHandle){'V'}else{'.'}}},@{n='CLI';e={($_.CommandLine -replace '`"','')[0..90] -join ''}} |
    ConvertTo-Json -Compress -Depth 3
"""
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, timeout=20)
print("=== python* met zichtbaar console (MainWindowHandle!=0) ===")
out = r.stdout or ""
# print only the relevant summary
lines = out.splitlines()
for ln in lines:
    cli = ln.replace('"','')
    if 'python.exe' in cli or 'pythonw' in cli or 'NONE' in ln:
        print(ln[:220])
print("\n--- FULL (truncated) ---")
print(out[-1500:])
