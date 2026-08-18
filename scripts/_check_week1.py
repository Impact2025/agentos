import sqlite3
c = sqlite3.connect("data/agentos.db")
c.row_factory = sqlite3.Row
for r in c.execute("SELECT id, project, campaign_post, status, scheduled_for FROM social_posts WHERE campaign='da-week1' ORDER BY scheduled_for"):
    print("%-26s %-22s %-14s %s" % (r["id"], r["project"], r["status"], str(r["scheduled_for"])))
c.close()
