import os, sys, sqlite3, datetime
os.chdir("/d/APPS/agentos")
DB = os.path.join("data", "agentos.db")
print("DB exists:", os.path.exists(DB))
c = sqlite3.connect(DB, timeout=60, isolation_level=None)
c.row_factory = sqlite3.Row
c.execute("PRAGMA busy_timeout=60000")
now = datetime.datetime.utcnow().isoformat()
print("now:", now)
CAMPAIGNS = ("da-doelgroepen-2026", "da-week1", "da-week2", "da-week3", "da-week4")
ph = ",".join("?" * len(CAMPAIGNS))
due = c.execute(
    f"SELECT id, project, campaign, post_type, status, scheduled_for FROM social_posts "
    f"WHERE campaign IN ({ph}) AND status='pending_review' AND scheduled_for <= ? "
    f"ORDER BY scheduled_for", (*CAMPAIGNS, now)
).fetchall()
print("due count:", len(due))
for r in due:
    print("  ", dict(r))
# specifiek pack
s = c.execute("SELECT status,scheduled_for FROM social_posts WHERE id='sp_da_main_w3_tt4'").fetchone()
print("sp_da_main_w3_tt4 row:", dict(zip(["status","scheduled_for"], s)))
