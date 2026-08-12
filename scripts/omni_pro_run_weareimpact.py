"""Pro voorbeeldrun: SERP-Omni engine voor WeAreImpact, met LIVE websearch + LIVE LLM.

Draait de echte generate_for_keyword voor 5 sterke zoekwoorden en toont per
keyword het SERP-profiel + de gegenereerde assets. Schrijft de assets naar de
ECHTE omni_queue (staged) zodat Vincent ze in de Omni-tab kan goedkeuren.
Leest de site alleen uit de live DB; mutaties beperkt tot omni_queue-inserts.
"""
import os, sys, json, sqlite3, uuid, datetime, asyncio
sys.path.insert(0, "D:/APPS/agentos")

from backend.shared.database import get_conn
from backend.domains.omni.generator import generate_for_keyword

SID = "e197d4b7-c928-49e2-af11-fc68bd3cd2cc"
KEYWORDS = [
    "AI in het sociaal domein",
    "Gemeenten AI-beleid sociaal domein",
    "Administratieve lasten verlagen sociaal domein",
    "Change management AI implementatie",
    "Wmo ondersteuning gemeente",
]

def owned(site):
    dom = (site.get("base_url") or "").lower().replace("https://","").replace("http://","").rstrip("/")
    return [dom] if dom else []

async def main():
    with get_conn() as c:
        c.row_factory = sqlite3.Row
        site = dict(c.execute("SELECT * FROM sites WHERE id=?", (SID,)).fetchone())

    print("=" * 70)
    print("SERP-OMNI PRO RUN — WeAreImpact (%s)" % site["name"])
    print("=" * 70)

    for kw in KEYWORDS:
        print("\n" + "#" * 70)
        print("KEYWORD: %s" % kw)
        print("#" * 70)
        res = await generate_for_keyword(kw, site, "", owned_domains=owned(site))
        serp = res.get("serp", {})
        print("SERP status : %s" % serp.get("status"))
        print("Dominant    : %s" % (", ".join(serp.get("dominant", [])) or "-"))
        print("Video box   : %s | Reddit thread: %s" % (serp.get("has_video_box"), serp.get("has_reddit_thread")))
        print("Platformen  : %s" % serp.get("platforms"))
        print("Aanbevolen  : %s" % ", ".join(serp.get("recommended_assets", [])))
        for i, t in enumerate(serp.get("top_results", [])[:3], 1):
            print("  %d. [%s] %s" % (i, t.get("platform"), t.get("title", "")[:70]))
        for a in res.get("assets", []):
            print("\n  -- ASSET: %s (status=%s, score=%s)" % (a["asset_type"], a.get("status"), a.get("score")))
            print("     titel: %s" % (a.get("title") or "-")[:90])
            body = (a.get("body") or "")[:280].replace("\n", " ")
            print("     body : %s" % body)
        with get_conn() as c2:
            c2.row_factory = sqlite3.Row
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            for a in res.get("assets", []):
                qid = "omni_%s" % uuid.uuid4().hex[:12]
                c2.execute(
                    "INSERT INTO omni_queue (id, site_id, keyword, asset_type, platform, title, body, "
                    "serp_profile, angle, status, score, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, SID, kw, a["asset_type"], a["platform"], a.get("title",""), a.get("body",""),
                     json.dumps(serp), "", a.get("status","staged"), a.get("score",0), a.get("note",""),
                     now, now))
        print("  -> %d assets naar omni_queue (staged)" % len(res.get("assets", [])))

    print("\n" + "=" * 70)
    print("KLAAR. Assets staan staged in de Omni-tab voor WeAreImpact.")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
