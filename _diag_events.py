import subprocess, os, json

# 1. Event Viewer Application log — AppError (EventID 1000) crashes last 3h
ev_cmd = """
$ErrorActionPreference = 'SilentlyContinue'
$q = '*[System[(EventID=1000) and (TimeCreated[timediff(@SystemTime) <= 10800000])]]'
$err = wevtutil qe Application /q:"$q" /f:text /c:80 2>&1
$err | Out-String -Width 400
"""
r = subprocess.run(["powershell", "-NoProfile", "-Command", ev_cmd],
                   capture_output=True, text=True, timeout=30)
txt = (r.stdout or "") + (r.stderr or "")
print("=== Event Viewer Application (EventID 1000 / crashes, last 3h) ===")
print(txt[-3000:] if len(txt) > 3000 else txt)

# 2. Check notebooklm-mcp.cmd target (Node script that may spawn python.exe)
for p in [r"C:\Users\v_mun\AppData\Roaming\npm\notebooklm-mcp.cmd",
          r"C:\Users\v_mun\AppData\Roaming\npm\notebooklm-mcp"]:
    print(f"\n=== {p} ===")
    if os.path.exists(p):
        with open(p) as f:
            print(f.read()[:400])
    else:
        print("MISSING")

# 3. Read impactos_service_nicole.cmd
print("\n=== impactos_service_nicole.cmd ===")
with open(r"D:\APPS\agentos\impactos_service_nicole.cmd") as f:
    print(f.read()[:600])

# 4. Does the hoofd-impactos_service.cmd exist anywhere in agentos root?
for p in [r"D:\APPS\agentos\impactos_service.cmd",
          r"D:\apps\agentos\impactos_service.cmd"]:
    print(f"\n=== {p} exists: {os.path.exists(p)} ===")
