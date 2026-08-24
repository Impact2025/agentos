"""Impact OS 'verse start' reset — wis alleen fout/systeem-ruis.
1) Maakt timestamped backup van data/impactos.db
2) Leegt agent_failure_streaks (rode fout-kaarten)
3) Verwijdert inbox_dismissals met kind='error' (onderdrukte fout-meldingen)
Raakt GEEN content_jobs / business-data aan."""
import sqlite3, os, shutil, datetime

DB = r"D:/apps/impactos/data/impactos.db"
assert os.path.exists(DB), "DB niet gevonden"
now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = f"D:/apps/impactos/data/impactos.db.bak-reset-{now}"
shutil.copy2(DB, BACKUP)
print("Backup:", BACKUP, "(%.1f MB)" % (os.path.getsize(BACKUP)/1e6))

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def has(t):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
    return bool(cur.fetchone())

# 1) failure streaks (rode ⚠ fout-kaarten)
if has("agent_failure_streaks"):
    cur.execute("SELECT COUNT(*) c FROM agent_failure_streaks")
    n1 = cur.fetchone()["c"]
    cur.execute("DELETE FROM agent_failure_streaks")
    print(f"agent_failure_streaks gewist: {n1} rij(en)")
else:
    n1 = 0
    print("agent_failure_streaks: tabel niet gevonden (0 gewist)")

# 2) error-kind dismissals
if has("inbox_dismissals"):
    cur.execute("SELECT COUNT(*) c FROM inbox_dismissals WHERE kind='error'")
    n2 = cur.fetchone()["c"]
    cur.execute("DELETE FROM inbox_dismissals WHERE kind='error'")
    print(f"inbox_dismissals(kind='error') gewist: {n2} rij(en)")
else:
    n2 = 0
    print("inbox_dismissals: tabel niet gevonden (0 gewist)")

con.commit()
con.close()
print(f"\nKLAAR. Totaal gewist: {n1} fout-streak(s) + {n2} error-dismissal(s). Content-tickets ongemoeid.")
