"""Auto-reply op Facebook comments — hefboom #4 (responstijd → feed-ranking).

Haalt recente comments op de DA-posts op en reageert met een korte, menselijke
welkom + link naar de relevante gids. Slaat al-beantwoorde comments over.
Draait veilig via cron (idempotent: trackt beantwoorde comment-ids in een tabel).
"""
import asyncio, sqlite3, httpx
from typing import Optional
from backend.shared import facebook as fb

REPLY = (
    "Dankjewel voor je reactie! 🙏 Meer tips over veilig en zelfverzekerd daten "
    "vind je in onze gids: https://datingassistent.nl/kennisbank — of start je "
    "gratis profiel via de link in onze eerste reactie."
)


async def _ensure_table():
    with sqlite3.connect("data/impactos.db") as c:
        c.execute("""CREATE TABLE IF NOT EXISTS da_replied_comments (
            comment_id TEXT PRIMARY KEY, replied_at TEXT DEFAULT CURRENT_TIMESTAMP)""")


async def run():
    await _ensure_table()
    c = sqlite3.connect("data/impactos.db")
    c.row_factory = sqlite3.Row
    # haal de live post-ids op per DA-site
    posts = c.execute(
        "SELECT site_name, post_id FROM fb_posts WHERE site_name LIKE 'DatingAssistent%' "
        "AND post_id LIKE '%_%' ORDER BY placed_at DESC"
    ).fetchall()
    async with httpx.AsyncClient() as client:
        for p in posts:
            sid, pid = p["site_name"], p["post_id"]
            _, tok = fb._get_site_data(sid)
            if not tok:
                continue
            r = await client.get(f"{fb.GRAPH_API}/{pid}/comments",
                                 params={"fields": "id,message,from", "access_token": tok}, timeout=30)
            if r.status_code != 200:
                continue
            for cm in r.json().get("data", []):
                cid = cm.get("id", "")
                # overslaan als al beantwoord
                if c.execute("SELECT 1 FROM da_replied_comments WHERE comment_id=?", (cid,)).fetchone():
                    continue
                # niet op eigen comments reageren
                if "DatingAssistent" in (cm.get("from", {}) or {}).get("name", ""):
                    continue
                rr = await client.post(f"{fb.GRAPH_API}/{cid}/comments",
                                      data={"message": REPLY[:6000], "access_token": tok}, timeout=30)
                if rr.status_code == 200:
                    with c:
                        c.execute("INSERT OR IGNORE INTO da_replied_comments(comment_id) VALUES (?)", (cid,))
                    print(f"  [REPLY] {sid}: {cid}")
    c.close()
    print("Auto-reply klaar.")


if __name__ == "__main__":
    asyncio.run(run())
