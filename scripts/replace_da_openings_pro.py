"""Vervang de 3 net geplaatste DA-openingsposts door de PRO-template versie.

1. Render pro-kaart per doelgroep (gradient + embleem + merknaam).
2. Verwijder de oude (rommelige) post van de pagina.
3. Plaats de nieuwe pro-kaart als foto-post op de juiste pagina.
4. Update social_posts (image_path + posted_result_json) + fb_posts log.
"""
import asyncio, sqlite3, json, os, httpx
from backend.shared import facebook as fb
from backend.shared import social_content as sc
from scripts.da_template import render_da_card

# (pack_id, leeftijd, merknaam)
PACKS = [
    ("sp_daDatingAssistent_01", "30+", "DatingAssistent voor 30-plussers"),
    ("sp_da40_01",             "40+", "DatingAssistent voor 40-plussers"),
    ("sp_da50_01",             "50+", "DatingAssistent voor 50-plussers"),
]

async def main():
    c = sqlite3.connect("data/impactos.db")
    async with httpx.AsyncClient() as client:
        for pack_id, age, brand in PACKS:
            p = sc.get_pack(pack_id)
            title = p.theme
            sub = (p.angle or "").split(" — ")[0][:60]
            # 1. render (v2 template, al gedaan vóór deze run)
            out = f"data/uploads/pro_{pack_id}.png"
            if not (os.path.exists(out)):
                render_da_card(None, title, sub, age, brand, out)
            # 2. vind oude post_id uit fb_posts
            old = c.execute("SELECT post_id FROM fb_posts WHERE site_name=? ORDER BY rowid DESC LIMIT 1", (p.project,)).fetchone()
            pid, tok = fb._get_site_data(p.project)
            # 3. verwijder oude post
            if old and old[0]:
                await client.delete(f"{fb.GRAPH_API}/{old[0]}", params={"access_token": tok}, timeout=30)
                print(f"  [verwijderd oud] {old[0]}")
            # 4. plaats nieuwe foto-post
            with open(out, "rb") as fh:
                r = await client.post(f"{fb.GRAPH_API}/{pid}/photos",
                    data={"message": (p.copy or {}).get("facebook", "")[:63000], "access_token": tok},
                    files={"source": fh}, timeout=60)
            if r.status_code == 200:
                new_id = r.json().get("id", "")
                # update social_posts
                ib = dict(p.image_brief or {})
                ib["image_path"] = out; ib["image_url"] = ""; ib["image_source"] = "template"
                p.image_brief = ib
                with c:
                    c.execute("UPDATE social_posts SET image_brief_json=?, status='posted', posted_result_json=? WHERE id=?",
                        (json.dumps(ib, ensure_ascii=False),
                         json.dumps({"facebook": {"success": True, "post_id": new_id, "url": f"https://www.facebook.com/{new_id}", "site": p.project}, "_platforms": ["facebook"]}, ensure_ascii=False),
                         pack_id))
                    # update fb_posts log (oude vervangen)
                    c.execute("UPDATE fb_posts SET post_id=?, placed_at=datetime('now') WHERE site_name=? AND post_id=?",
                        (new_id, p.project, old[0] if old else ""))
                    if old is None:
                        try:
                            from backend.domains.analytics.facebook_store import log_fb_post
                            log_fb_post(new_id, p.project, message=(p.copy or {}).get("facebook","")[:200])
                        except Exception:
                            pass
                print(f"  [GEPLAATST PRO] {p.project:20} {pack_id} -> {new_id}")
            else:
                print(f"  [FOUT] {p.project}: {r.json().get('error',{}).get('message','?')[:90]}")
    c.close()
    print("\nKlaar — 3 pro-posts vervangen.")

asyncio.run(main())
