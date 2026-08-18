"""DA geautomatiseerde post-engine — wereldklasse, hands-off (beide campagnes).

Pakt elke DA-pack uit campagne 'da-doelgroepen-2026' OF 'da-week1' die:
  - status='pending_review'
  - scheduled_for <= nu
  - een beeld heeft (MJ-specifiek of fallback naar openings-beeld)
doet:
  1. ververs page-tokens (~1u geldig)
  2. monteer beeld + template (echt logo + optionele leeftijd-badge)
  3. post naar FB
  4. auto-comment met leeftijd-specifieke CTA-link + hashtags
  5. IG-post als ig_business gekoppeld (publieke host)

Idempotent: slaat over als al gepost. Veilig voor Cron.
"""
import asyncio, sqlite3, json, os, glob, httpx
from datetime import datetime
from backend.shared import facebook as fb
from backend.shared import social_content as sc
from backend.shared import social_auto_comment as sac
from backend.shared import instagram as ig_svc
from backend.shared import public_host as host
from scripts.da_template import render_da_card

DB = "data/agentos.db"
MJ = "data/uploads/da_mj"
CAMPAIGNS = ("da-doelgroepen-2026", "da-week1", "da-week2", "da-week3", "da-week4")
AGE = {"DatingAssistent": "30+", "DatingAssistent 40+": "40+", "DatingAssistent 50+": "50+"}
BRAND = {"DatingAssistent": "DatingAssistent",
         "DatingAssistent 40+": "DatingAssistent voor 40-plussers",
         "DatingAssistent 50+": "DatingAssistent voor 50-plussers"}
# specifieke beeld-mapping (geplande reeks)
IMG_MAP = {
    "sp_daDatingAssistent_02": "da30_2", "sp_daDatingAssistent_03": "da30_3", "sp_daDatingAssistent_04": "da30_4",
    "sp_da40_02": "da40_2", "sp_da40_03": "da40_3", "sp_da40_04": "da40_4",
    "sp_da50_02": "da50_2", "sp_da50_03": "da50_3", "sp_da50_04": "da50_4",
}
# fallback-beelden (openings-set) per doelgroep
FALLBACK = {"DatingAssistent": "30_Warm_natural-light_portrait",
            "DatingAssistent 40+": "40_Genuine_smile_of_a_man_and_woman",
            "DatingAssistent 50+": "50_Two_people_in_their_50s"}

def _find(frag):
    hits = sorted(glob.glob(os.path.join(MJ, f"*{frag}*.png")))
    for h in hits:
        if h.endswith("_0.png") or "_0 (" in h:
            return h
    return hits[0] if hits else None

async def main():
    await fb.refresh_page_tokens()
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(CAMPAIGNS))
    due = c.execute(
        f"SELECT id, project, campaign FROM social_posts "
        f"WHERE campaign IN ({ph}) AND status='pending_review' AND post_type='image' AND scheduled_for <= ? "
        f"ORDER BY scheduled_for", (*CAMPAIGNS, now)
    ).fetchall()
    if not due:
        print("Geen posts die nu moeten (alles op schema of al gepost)."); c.close(); return
    async with httpx.AsyncClient() as client:
        for r in due:
            pid, proj = r["id"], r["project"]
            frag = IMG_MAP.get(pid)
            photo = _find(frag) if frag else None
            if not photo:
                photo = _find(FALLBACK.get(proj, ""))
            if not photo:
                print(f"  [WACHT] {pid}: geen beeld in da_mj/ — sla over"); continue
            p = sc.get_pack(pid)
            sub = (p.angle or "").split(" - ")[0][:60]
            age = AGE.get(proj, "")
            out = f"data/uploads/pro_{pid}.png"
            render_da_card(photo, p.theme, sub, age, BRAND.get(proj, proj), out)
            page_id, tok = fb._get_site_data(proj)
            rr = await client.post(f"{fb.GRAPH_API}/{page_id}/photos",
                data={"message": (p.copy or {}).get("facebook", "")[:63000], "access_token": tok},
                files={"source": open(out, "rb")}, timeout=60)
            if rr.status_code != 200:
                print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
            new_id = rr.json().get("id", "")
            await sac.auto_comment_after_post(new_id, proj, age)
            # IG
            ig_id, ig_tok = ig_svc._get_site_data(proj)
            if ig_id and ig_tok:
                try:
                    url = await host.upload(out)
                    ir = await ig_svc.post_image(url, (p.copy or {}).get("instagram", (p.copy or {}).get("facebook",""))[:2200], proj)
                    print(f"  [IG] {proj}: {'LIVE '+str(ir.get('post_id')) if ir.get('success') else 'SKIP '+str(ir.get('error',''))[:50]}")
                except Exception as e:
                    print(f"  [IG ERR] {proj}: {str(e)[:50]}")
            ib = dict(p.image_brief or {}); ib.update({"image_path": out, "image_source": "midjourney"})
            p.image_brief = ib
            with c:
                c.execute("UPDATE social_posts SET image_brief_json=?, status='posted', posted_result_json=? WHERE id=?",
                    (json.dumps(ib, ensure_ascii=False),
                     json.dumps({"facebook": {"success": True, "post_id": new_id, "url": f"https://www.facebook.com/{new_id}", "site": proj}, "_platforms": ["facebook"]}, ensure_ascii=False),
                     pid))
            print(f"  [LIVE] {proj:22} {pid} -> {new_id}")
    c.close(); print("\nKlaar.")

asyncio.run(main())
