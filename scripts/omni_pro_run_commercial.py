"""Pro run — verbeterde SERP-Omni engine op de commerciële portfolio-projecten.

Bewijst dat de verbeterde detectie (video-intentie + 'other'-dominantie -> brede
omnipresence-set) vuurt op zoekwoorden waar Reddit/YouTube daadwerkelijk
domineren (Pootgelukkig, DatingAssistent). Schrijft assets naar de echte
omni_queue (staged). Leest sites alleen uit de live DB.
"""
import sys, json, sqlite3, uuid, datetime, asyncio
sys.path.insert(0, "D:/APPS/impactos")

from backend.shared.database import get_conn
from backend.domains.omni.generator import generate_for_keyword

RUNS = [
    ("a123630e-f5e5-47d2-98f8-ffea0dc7b17e", "Pootgelukkig", [
        "hond adopteren uit asiel",
        "asieldier adoptie kosten",
        "wat kost een hond per maand",
        "puppy adopteren tips",
    ]),
    ("datingassistent", "DatingAssistent", [
        "dating tips eerste date",
        "hoe krijg ik een relatie",
        "online daten profiel tekst",
        "waarom wordt ik steeds ghosted",
    ]),
]

def owned(site):
    dom = (site.get("base_url") or "").lower().replace("https://","").replace("http://","").rstrip("/")
    return [dom] if dom else []

async def main():
    for sid, name, kws in RUNS:
        with get_conn() as c:
            c.row_factory = sqlite3.Row
            site = dict(c.execute("SELECT * FROM sites WHERE id=?", (sid,)).fetchone())
        print("\n" + "=" * 72)
        print("PRO RUN — %s (%s)" % (name, site["base_url"]))
        print("=" * 72)
        total = 0
        for kw in kws:
            res = await generate_for_keyword(kw, site, "", owned_domains=owned(site))
            serp = res.get("serp", {})
            print("\n• %s" % kw)
            print("  dominant: %s | video_box=%s | rec: %s" % (
                ", ".join(serp.get("dominant", [])) or "-",
                serp.get("has_video_box"),
                ", ".join(serp.get("recommended_assets", []))))
            with get_conn() as c2:
                c2.row_factory = sqlite3.Row
                now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
                for a in res.get("assets", []):
                    qid = "omni_%s" % uuid.uuid4().hex[:12]
                    c2.execute(
                        "INSERT INTO omni_queue (id, site_id, keyword, asset_type, platform, title, body, "
                        "serp_profile, angle, status, score, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (qid, sid, kw, a["asset_type"], a["platform"], a.get("title",""), a.get("body",""),
                         json.dumps(serp), "", a.get("status","staged"), a.get("score",0), a.get("note",""),
                         now, now))
                    total += 1
            print("  -> %d assets staged" % len(res.get("assets", [])))
        print("\n  TOTAAL %s: %d assets" % (name, total))
    print("\nKLAAR — alle assets staged in de Omni-tab.")

if __name__ == "__main__":
    asyncio.run(main())
