"""Monteer DA-posts uit MJ-beelden + v4-template, vervang live posts.

Matcht per doelgroep het juiste MJ-beeld (lange gegenereerde naam) en bouwt de
pro-kaart (echt logo + merknaam + leeftijd-badge), vervangt de 3 live openings.
"""
import asyncio, sqlite3, json, os, glob, httpx, re
from backend.shared import facebook as fb
from backend.shared import social_content as sc
from scripts.da_template import render_da_card

MJ_DIR = "data/uploads/da_mj"

# koppel doelgroep -> MJ-bestandsfragment + pack + brand
PACKS = [
    ("sp_daDatingAssistent_01", "30_Warm_natural-light_portrait", "30+", "DatingAssistent voor 30-plussers"),
    ("sp_da40_01",             "40_Genuine_smile_of_a_man_and_woman", "40+", "DatingAssistent voor 40-plussers"),
    ("sp_da50_01",             "50_Two_people_in_their_50s", "50+", "DatingAssistent voor 50-plussers"),
]

def _pick(frag):
    hits = sorted(glob.glob(os.path.join(MJ_DIR, f"*{frag}*.png")))
    # neem variant _0 indien aanwezig, anders de eerste
    for h in hits:
        if h.endswith("_0.png") or "_0 (" in h:
            return h
    return hits[0] if hits else None

async def main():
    c = sqlite3.connect("data/agentos.db")
    async with httpx.AsyncClient() as client:
        for pack_id, frag, age, brand in PACKS:
            photo = _pick(frag)
            if not photo:
                print(f"  [MIS] geen beeld voor {pack_id} ({frag})")
                continue
            p = sc.get_pack(pack_id)
            sub = (p.angle or "").split(" - ")[0][:60]
            out = f"data/uploads/pro_{pack_id}.png"
            render_da_card(photo, p.theme, sub, age, brand, out)
            old = c.execute("SELECT post_id FROM fb_posts WHERE site_name=? ORDER BY rowid DESC LIMIT 1", (p.project,)).fetchone()
            pid, tok = fb._get_site_data(p.project)
            if old and old[0]:
                await client.delete(f"{fb.GRAPH_API}/{old[0]}", params={"access_token": tok}, timeout=30)
                print(f"  [oud verwijderd] {old[0]}")
            with open(out, "rb") as fh:
                r = await client.post(f"{fb.GRAPH_API}/{pid}/photos",
                    data={"message": (p.copy or {}).get("facebook", "")[:63000], "access_token": tok},
                    files={"source": fh}, timeout=60)
            if r.status_code == 200:
                new_id = r.json().get("id", "")
                ib = dict(p.image_brief or {})
                ib.update({"image_path": out, "image_source": "midjourney", "image_url": ""})
                p.image_brief = ib
                with c:
                    c.execute("UPDATE social_posts SET image_brief_json=?, status='posted', posted_result_json=? WHERE id=?",
                        (json.dumps(ib, ensure_ascii=False),
                         json.dumps({"facebook": {"success": True, "post_id": new_id, "url": f"https://www.facebook.com/{new_id}", "site": p.project}, "_platforms": ["facebook"]}, ensure_ascii=False),
                         pack_id))
                    c.execute("UPDATE fb_posts SET post_id=? WHERE site_name=? AND post_id=?", (new_id, p.project, old[0] if old else ""))
                print(f"  [LIVE] {p.project:20} {pack_id} -> {new_id}  (beeld: {os.path.basename(photo)})")
            else:
                print(f"  [FOUT] {p.project}: {r.json().get('error',{}).get('message','?')[:90]}")
    c.close()
    print("\nKlaar.")

asyncio.run(main())
