import os, sys
# Resolve .lnk target via PowerShell
import subprocess
lnk = r"C:\Users\v_mun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\OmnirouteGateway.lnk"
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');Write-Host 'TARGET:';Write-Host $s.TargetPath;Write-Host 'ARGS:';Write-Host $s.Arguments;Write-Host 'WINDOWSTYLE:';Write-Host $s.WindowStyle;Write-Host 'ICON:';Write-Host $s.IconLocation"],
    capture_output=True, text=True, timeout=15)
print("=== OmnirouteGateway.lnk ===")
print(r.stdout or "(no output)")
if r.stderr:
    print("STDERR:", r.stderr[:500])

for l in ["AgentOS.lnk", "AgentOS Nicole.lnk", "Mission Control.lnk", "OmnirouteGateway.lnk", "Ollama.lnk"]:
    full = os.path.join(r"C:\Users\v_mun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup", l)
    print(f"\n=== {l} ===")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{full}');Write-Host $s.TargetPath;Write-Host $s.Arguments;Write-Host ('WS='+$s.WindowStyle)"],
        capture_output=True, text=True, timeout=15)
    print(r.stdout or "(none)")
