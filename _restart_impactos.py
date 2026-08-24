import os, subprocess, time, sys

ROOT = r"D:\apps\impactos"
LOG = os.path.join(ROOT, "impactos.log")
PORT = 1250

def find_pid_on_port(port):
    try:
        out = subprocess.check_output(
            f'netstat -ano | findstr :{port} | findstr LISTEN',
            shell=True, text=True)
        for line in out.splitlines():
            parts = line.split()
            if parts:
                return parts[-1]
    except Exception:
        pass
    return None

def main():
    pid = find_pid_on_port(1250)
    if pid:
        print(f"[restart] killing old server pid={pid}", flush=True)
        subprocess.run(f"taskkill /PID {pid} /F", shell=True)
        time.sleep(2)
    else:
        print("[restart] no server on port 1250", flush=True)

    exe = os.path.join(ROOT, ".venv", "Scripts", "uvicorn.exe")
    cmd = f'"{exe}" backend.main:app --host localhost --port {PORT}'
    print(f"[restart] starting: {cmd}", flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        subprocess.Popen(cmd, shell=True, cwd=ROOT,
                         stdout=f, stderr=subprocess.STDOUT)
    # Wacht tot de health-endpoint antwoordt
    for i in range(30):
        time.sleep(1)
        try:
            r = subprocess.run(f'curl -s -m 3 http://localhost:{PORT}/api/status',
                               shell=True, capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and "online" in r.stdout:
                print(f"[restart] server up after {i+1}s", flush=True)
                return 0
        except Exception:
            pass
    print("[restart] server did not come up in 30s — check impactos.log", flush=True)
    return 1

if __name__ == "__main__":
    sys.exit(main())
