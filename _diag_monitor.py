import subprocess, time, json
# Monitor for 25 seconds; detect new python.exe processes and whether they have a console window
samples = []
for i in range(5):  # 5 x 5s = 25s
    ps_cmd = """
$procs = Get-WmiObject Win32_Process -Filter "Name like 'python%'" |
    Select-Object ProcessId,ParentProcessId,Name,CommandLine,@{n='CT';e={Get-Date}}
$procs | ConvertTo-Json -Compress
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                       capture_output=True, text=True, timeout=10)
    try:
        items = json.loads(r.stdout) if r.stdout.strip() else []
    except Exception:
        items = []
    t = time.strftime("%H:%M:%S")
    pcount = len(items)
    print(f"[{t}] sample {i}: {pcount} python* procs")
    for it in items:
        cli = (it.get('CommandLine') or '')[:110]
        name = it.get('Name','?')
        pid = it.get('ProcessId')
        ppid = it.get('ParentProcessId')
        print(f"   PID={pid} PPID={ppid} {name}: {cli}")
    samples.append(pcount)
    time.sleep(5)
print("\n=== Counts per sample:", samples)
