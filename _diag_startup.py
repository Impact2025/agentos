import winreg as regbase
import os

# 1. User Startup
try:
    k = regbase.OpenKey(regbase.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, regbase.KEY_READ)
    print("=== HKCU Run ===")
    i = 0
    while True:
        try:
            name = regbase.EnumValue(k, i)
            print(f"{name[0]} => {name[1]}")
            i += 1
        except OSError:
            break
    regbase.CloseKey(k)
except Exception as e:
    print("HKCU Run error:", e)

# 2. Startup folder contents
import os
startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
common = os.path.join(os.environ["PROGRAMDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
print(f"\n=== User Startup ({startup}) ===")
for f in sorted(os.listdir(startup)) if os.path.isdir(startup) else []:
    print(f"  {f}")
print(f"\n=== Common Startup ({common}) ===")
for f in sorted(os.listdir(common)) if os.path.isdir(common) else []:
    print(f"  {f}")

# 3. Scheduled tasks related to agentos/python/launch
print("\n=== Task Scheduler (root tasks) ===")
os.system(r'schtasks /query /fo CSV 2>nul | findstr /I "agentos impactos nicole launch supervisor hermes watchdog"')
