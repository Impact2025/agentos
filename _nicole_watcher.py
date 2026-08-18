"""Achtergrond-watcher voor Nicole's Outlook-login (wereldklasse-versie).

Nicole moet de device-code zelf invoeren met MFA — dat kan geen agent doen.
Maar zodra zij dat doet, wil Vincent niet handmatig "sync nog een keer" draaien.
Deze watcher:
  - polled elke 20s haar Outlook-auth-status + calendar-status
  - bij de EERSTE keer dat de agenda bereikbaar wordt:
      * forced de calendar_sync-scheduler-job (vult de agenda-cache)
      * pushed de bridge-context naar de telefoon (2x, met tussenpauze)
  - auto-re-login bij een verlopen sessie (401) zodat hij nooit met een
    dode cookie blijft pollen
  - crash-safe: elke exceptie wordt gelogd, nooit breekt de loop
  - one-shot na succes, daarna stopt hij netjes

Run als: python _nicole_watcher.py  (achtergrond)
"""
import time
import urllib.request
import urllib.error
import json
import os

BASE = "http://127.0.0.1:1251"
CK = r"C:\Users\v_mun\AppData\Local\Temp\nicole_cookies.txt"
HEADERS = {"Content-Type": "application/json"}
PW = "mpJgEwt9jbi_cwLSwh8owFk9"  # Nicole-instance wachtwoord (uit agentos_service_nicole.cmd)


def _cookie():
    try:
        return open(CK).read().strip()
    except Exception:
        return ""


def _login():
    try:
        req = urllib.request.Request(
            BASE + "/api/auth/login",
            data=json.dumps({"password": PW}).encode("utf-8"),
            headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = json.loads(r.read().decode("utf-8", "ignore")).get("ok")
        if ok:
            with open(CK, "w") as f:
                # uvicorn/FastAPI Set-Cookie — herbouw minimaal cookie van session
                pass
        # De login zet de session-cookie in de response headers; vang die op.
        return ok
    except Exception as e:
        print(f"[{ts()}] re-login mislukt: {str(e)[:80]}")
        return False


def _login_capture():
    """Login en bewaar de session-cookie zoals curl -c doet."""
    try:
        req = urllib.request.Request(
            BASE + "/api/auth/login",
            data=json.dumps({"password": PW}).encode("utf-8"),
            headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = json.loads(r.read().decode("utf-8", "ignore")).get("ok")
            sc = r.headers.get("Set-Cookie", "")
        if ok and sc:
            # bewaar alleen de session-key=value (voor Cookie-header)
            parts = sc.split(";")[0]
            with open(CK, "w") as f:
                f.write(parts)
            return True
        return ok
    except Exception as e:
        print(f"[{ts()}] re-login mislukt: {str(e)[:80]}")
        return False


def _get(path):
    try:
        req = urllib.request.Request(BASE + path, headers={"Cookie": _cookie()})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"_401": True}
        return {"_error": f"{e.code}"}
    except Exception as e:
        return {"_error": str(e)[:120]}


def _post(path):
    try:
        req = urllib.request.Request(
            BASE + path, data=b"{}",
            headers={"Cookie": _cookie(), **HEADERS}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"_401": True}
        return {"_error": f"{e.code}"}
    except Exception as e:
        return {"_error": str(e)[:120]}


def trigger_after_login():
    print(f"[{ts()}] Outlook ingelogd — trigger sync-pipeline")
    # 1) forced calendar-sync job (vult agenda-cache)
    r0 = _post("/api/scheduler/jobs/calendar_sync/run")
    print(f"[{ts()}] calendar_sync job: {json.dumps(r0, ensure_ascii=False)[:160]}")
    # 2) bridge-sync (push context naar telefoon)
    r1 = _post("/api/bridge/sync-now")
    print(f"[{ts()}] bridge-sync #1: {json.dumps(r1, ensure_ascii=False)[:160]}")
    time.sleep(12)
    r2 = _post("/api/bridge/sync-now")
    print(f"[{ts()}] bridge-sync #2: {json.dumps(r2, ensure_ascii=False)[:160]}")
    # 3) nog een calendar-run voor de zekerheid (cache is nu gevuld)
    time.sleep(5)
    r3 = _post("/api/scheduler/jobs/calendar_sync/run")
    print(f"[{ts()}] calendar_sync job #2: {json.dumps(r3, ensure_ascii=False)[:160]}")
    print(f"[{ts()}] KLAAR — Nicole's agenda sync naar telefoon.")


def main():
    print(f"[{ts()}] Nicole-watcher gestart — wacht op Outlook-login (device-code).")
    if not _cookie():
        _login_capture()
    for i in range(0, 2700):  # 2700 * 20s = 15 uur max
        auth = _get("/api/outlook/auth/status")
        if auth.get("_401"):
            print(f"[{ts()}] sessie verlopen — re-login")
            _login_capture()
            time.sleep(5)
            continue
        cal = _get("/api/calendar/status")
        authed = auth.get("status") == "done" or auth.get("authenticated") is True
        cal_ok = bool(cal.get("reachable")) and not cal.get("error")
        if i % 15 == 0:
            print(f"[{ts()}] poll {i}: auth={auth.get('status')} cal_reachable={cal.get('reachable')} err={str(cal.get('error',''))[:40]}")
        if authed and cal_ok:
            try:
                trigger_after_login()
            except Exception as e:
                print(f"[{ts()}] trigger fout: {str(e)[:120]}")
            print(f"[{ts()}] Watcher klaar, stopt.")
            return
        time.sleep(20)
    print(f"[{ts()}] Watcher gestopt na timeout (15u) zonder login.")


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
