import subprocess, time, os
# Simulate: pythonw.exe with print that would-be output to a console
# If pythonw.exe, no console should appear
pyw = r"C:\Users\v_mun\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
# Use a quick python -c via pythonw; but pythonw -c prints to nowhere - check no console
r = subprocess.run([pyw, "-c", "print('popup test'); import sys; sys.stderr.write('err test')"],
                   creationflags=0, capture_output=False, timeout=5)
print(f"pythonw exit code: {r.returncode} (0 + no console = GOOD)")

# Compare: python.exe with CREATE_NO_WINDOW should also have no console
py = r"C:\Users\v_mun\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
r2 = subprocess.run([py, "-c", "pass"], creationflags=0x00000008, capture_output=False, timeout=5)
print(f"python.exe + CREATE_NO_WINDOW exit: {r2.returncode} (0 = no console popup)")
