"""READ-ONLY inspectie van Impact OS failure-streaks + error-inbox.
Schrijft NIETS weg. Print wat er gewist zou worden bij een reset."""
import sqlite3, os, glob

CANDIDATES = [
    r"D:/apps/impactos/data/impactos.db",
    r"D:/apps/impactos/backend/data/impactos.db",
    r"D:/apps/impactos/impactos.db",
]

def open_first():
    for p in CANDIDATES:
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            return p
    # fallback: elke .db groter dan 1MB onder impactos
    for p in glob.glob(r"D:/apps/impactos/**/*.db", recursive=True):
        if os.path.getsize(p) > 1_000_000:
            return p
    return None

DB = open_first()
print("GEKOZEN DB:", DB, "size_MB=%.1f" % (os.path.getsize(DB) / 1e6) if DB else "NONE")

if not DB:
    print("Geen DB gevonden."); raise SystemExit

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def has(t):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
    return bool(cur.fetchone())

print("\n=== agent_failure_streaks (rode ⚠ fout-kaarten) ===")
if has("agent_failure_streaks"):
    cur.execute("SELECT key, fail_count, failure_class, escalated, substr(last_detail,1,90) AS d FROM agent_failure_streaks ORDER BY fail_count DESC")
    rows = cur.fetchall()
    print("  TOTAL:", len(rows))
    for r in rows:
        print(f"  - [{r['failure_class'] or '?'}] x{r['fail_count']} esc={r['escalated']} {r['key']}: {r['d']}")
else:
    print("  (tabel bestaat niet)")

print("\n=== inbox_dismissals ===")
if has("inbox_dismissals"):
    cur.execute("SELECT kind, COUNT(*) c FROM inbox_dismissals GROUP BY kind ORDER BY c DESC")
    for r in cur.fetchall():
        print(f"  {r['kind']}: {r['c']}")
    cur.execute("SELECT COUNT(*) c FROM inbox_dismissals")
    print("  TOTAL:", cur.fetchone()["c"])
else:
    print("  (tabel bestaat niet)")

print("\n=== errors-tabel? ===")
if has("errors"):
    cur.execute("SELECT COUNT(*) c FROM errors")
    print("  errors rows:", cur.fetchone()["c"])
else:
    print("  (geen 'errors'-tabel)")

print("\n=== content_jobs (voor de 75 'WACHT OP JOU' tickets) ===")
if has("content_jobs"):
    cur.execute("SELECT status, COUNT(*) c FROM content_jobs GROUP BY status ORDER BY c DESC")
    for r in cur.fetchall():
        print(f"  status={r['status']}: {r['c']}")
    cur.execute("SELECT COUNT(*) c FROM content_jobs")
    print("  TOTAL content_jobs:", cur.fetchone()["c"])
else:
    print("  (geen content_jobs-tabel)")

con.close()
print("\nKLAAR — niets gewijzigd.")
