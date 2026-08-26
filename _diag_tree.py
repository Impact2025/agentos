import wmi
c = wmi.WMI()
# Target PIDs: agentos-related python processes
targets = {2652, 7896, 26108, 26560, 25296, 1256, 6452, 7892, 9788, 12924, 15796, 16204, 18648, 19980, 22092, 22444, 22836, 25316, 768, 25296}
for p in c.Win32_Process():
    if p.ProcessId in targets or p.Name.lower().startswith('python'):
        pid = p.ProcessId
        ppid = p.ParentProcessId
        name = p.Name
        cli = (p.CommandLine or '')[:140]
        # resolve parent name
        pname = '?'
        try:
            parent = c.Win32_Process(ProcessId=ppid)[0]
            pname = parent.Name
            pcli = (parent.CommandLine or '')[:80]
        except Exception:
            pcli = ''
        print(f"PID={pid} PPID={ppid} name={name} parent={pname}")
        print(f"   CLI: {cli}")
        print(f"   PCLI: {pcli}")
        print()
