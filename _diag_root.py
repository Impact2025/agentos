import subprocess, os
# Check AgentOS.lnk — does it target a file that exists?
lnk = r"C:\Users\v_mun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\AgentOS.lnk"
print("=== AgentOS.lnk target ===")
r = subprocess.run(["powershell","-NoProfile","-Command",
    f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');Write-Host ('TARGETPATH='+$s.TargetPath);Write-Host ('ARGS='+$s.Arguments)"],
    capture_output=True, text=True, timeout=10)
print(r.stdout)

# Verify the target exists
target = r.stdout.split("TARGETPATH=")[-1].strip().splitlines()[0].strip() if "TARGETPATH=" in r.stdout else ""
print(f"Target string: {target!r}")
print(f"Exists: {os.path.exists(target)}")

# Check finance gateway (known broken earlier)
fgl = r"C:\Users\v_mun\AppData\Local\hermes\gateway-service\Hermes_Gateway_finance-expert.cmd"
print(f"\n=== finance gateway ===\nPath: {fgl}\nExists: {os.path.exists(fgl)}")

# Check the omniroute server.py do_POST and start_service for CREATE_NO_WINDOW
print("\n=== supervisor.py start_service ===")
sup = r"D:\APPS\llm-proxy\supervisor.py"
with open(sup) as f:
    txt = f.read()
# print the start_service function
import re
m = re.search(r"def start_service.*?(?=\ndef )", txt, re.S)
print(m.group(0)[:600] if m else "not found")
