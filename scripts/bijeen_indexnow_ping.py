"""IndexNow-ping voor Bijeen na elke deploy.

Leest de gepubliceerde blog-slugs uit de Neon-DB (via de repo's db-import is lastig
in een los script, dus lezen we ze uit de AgentOS content_jobs die 'published' zijn
voor Bijeen) en pingt ze naar IndexNow met de gevalideerde key.

Gebruik: python scripts/bijeen_indexnow_ping.py
"""
import sqlite3, json, sys, urllib.request, urllib.error
sys.path.insert(0, r"D:\APPS\agentos")

DB = r"D:\APPS\agentos\data\agentos.db"
KEY = "82cbb4725e849b2ebf8196e279e62ae0"  # gevalideerd op bijeen.app (public/82cbb...txt)
HOST = "bijeen.app"

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT cj.slug FROM content_jobs cj
        JOIN sites s ON cj.site_id = s.id
        WHERE s.name='Bijeen' AND cj.status='published' AND cj.slug IS NOT NULL
    """).fetchall()
    # schone slugs (zonder 'herschrijf-' prefix)
    import re
    slugs = set()
    for r in rows:
        s = r["slug"].replace("herschrijf-het-artikel-voor-bijeen-", "").replace("herschrijf-het-artikel-", "")
        s = re.sub(r"[^a-z0-9-]", "", s.lower())
        if s:
            slugs.add(s)
    if not slugs:
        print("IndexNow: geen gepubliceerde Bijeen-slugs gevonden.")
        return
    urls = [f"https://{HOST}/blog/{s}" for s in sorted(slugs)]
    body = json.dumps({"host": HOST, "key": KEY, "urlList": urls}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        print(f"IndexNow HTTP {resp.status} — {len(urls)} URLs gepingd.")
    except urllib.error.HTTPError as e:
        print(f"IndexNow HTTP {e.code} — {e.read().decode()[:200]}")
    except Exception as e:
        print(f"IndexNow ERR {e}")

if __name__ == "__main__":
    main()
