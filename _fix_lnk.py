import subprocess, os
# Fix AgentOS.lnk to point to the correct, existing impactos_service.cmd
lnk = r"C:\Users\v_mun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\AgentOS.lnk"
target = r"D:\APPS\agentos\impactos_service.cmd"
print(f"AgentOS.lnk exists: {os.path.exists(lnk)}")
print(f"impactos_service.cmd exists: {os.path.exists(target)}")
if os.path.exists(lnk) and os.path.exists(target):
    # Rewrite the .lnk TargetPath via COM
    ps = f"""
$WshShell = New-Object -ComObject WScript.Shell
$sc = $WshShell.CreateShortcut('{lnk}')
$sc.TargetPath = '{target}'
$sc.WorkingDirectory = 'D:\\APPS\\agentos'
$sc.WindowStyle = 7   # 7 = minimized (verborgen console)
$sc.Save()
Write-Host 'AgentOS.lnk repointed to impactos_service.cmd (WindowStyle=7/minimized)'
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=15)
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[:300])
else:
    print("!! Cannot fix: missing lnk or target")
