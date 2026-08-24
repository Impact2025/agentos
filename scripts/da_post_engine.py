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

DB = "data/impactos.db"
MJ = "data/uploads/da_mj"
CAMPAIGNS = ("da-doelgroepen-2026", "da-week1", "da-week2", "da-week3", "da-week4")

# Wereldklasse-fix 21 aug 2026: alle DA-campagnepacks staan in de DB met
# project='DatingAssistent' (uniform, ongeacht doelgroep) — de oude routing
# via `fb._get_site_data(proj)` gaf daardoor VOOR ELKE campagne (30+/40+/50+
# én hoofdpagina) dezelfde ene pagina terug, namelijk waar sites-rij
# 'DatingAssistent' toevallig naar wees. Op 21 aug landde een 40+-post zo op
# de 30+-pagina. `campaign` is wel per doelgroep uniek (zie de seed-scripts:
# '...-40plus'/'...-50plus'-suffix, 'da-doelgroepen-2026' zonder suffix = 30+,
# 'da-week1..4' zonder suffix = hoofdpagina) — routeer daarop, niet op project.
# De hoofdpagina staat in de sites-tabel (enige echte ImpactOS-site); de drie
# doelgroeppagina's bewust niet (zie backend/shared/facebook.py:check_age_targeting)
# en krijgen hun page-token hier rechtstreeks uit /me/accounts.
PAGE_IDS = {"30+": "107835799327006", "40+": "174410412641281", "50+": "123632714408933"}
AGE = {"30+": "30+", "40+": "40+", "50+": "50+", "main": ""}
BRAND = {"30+": "DatingAssistent", "40+": "DatingAssistent voor 40-plussers",
         "50+": "DatingAssistent voor 50-plussers", "main": "DatingAssistent.nl"}
FALLBACK = {"30+": "30_Warm_natural-light_portrait",
            "40+": "40_Genuine_smile_of_a_man_and_woman",
            "50+": "50_Two_people_in_their_50s",
            "main": "30_Warm_natural-light_portrait"}
# specifieke beeld-mapping (geplande reeks)
IMG_MAP = {
    "sp_daDatingAssistent_02": "da30_2", "sp_daDatingAssistent_03": "da30_3", "sp_daDatingAssistent_04": "da30_4",
    "sp_da40_02": "da40_2", "sp_da40_03": "da40_3", "sp_da40_04": "da40_4",
    "sp_da50_02": "da50_2", "sp_da50_03": "da50_3", "sp_da50_04": "da50_4",
}


def _doelgroep_voor_campagne(campaign: str) -> str:
    c = campaign or ""
    if c.endswith("-40plus"):
        return "40+"
    if c.endswith("-50plus"):
        return "50+"
    if c == "da-doelgroepen-2026":
        return "30+"
    return "main"  # da-week1..4 zonder leeftijdssuffix = hoofdpagina


def _find(frag):
    hits = sorted(glob.glob(os.path.join(MJ, f"*{frag}*.png")))
    for h in hits:
        if h.endswith("_0.png") or "_0 (" in h:
            return h
    return hits[0] if hits else None


async def _resolve_pagina(client, doelgroep, cache):
    """(page_id, token) voor een doelgroep. Hoofdpagina loopt via de sites-tabel
    (heeft een eigen, stabiel page-token); de drie leeftijdspagina's hebben
    geen sites-rij (bewust, zie check_age_targeting) en krijgen hun kortlevende
    page-token (~1u) rechtstreeks uit /me/accounts van het globale token."""
    if doelgroep == "main":
        return fb._get_site_data("DatingAssistent")
    if doelgroep in cache:
        return PAGE_IDS[doelgroep], cache[doelgroep]
    _, glob_token = fb._get_site_data(None)
    r = await client.get(f"{fb.GRAPH_API}/me/accounts",
                          params={"fields": "id,access_token", "access_token": glob_token, "limit": 200},
                          timeout=20)
    if r.status_code == 200:
        for acc in r.json().get("data", []):
            for dg, pid in PAGE_IDS.items():
                if acc.get("id") == pid and acc.get("access_token"):
                    cache[dg] = acc["access_token"]
    return PAGE_IDS[doelgroep], cache.get(doelgroep)

async def main():
    await fb.refresh_page_tokens()
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(CAMPAIGNS))
    # Haal zowel image- als video-posts (Reels/TikTok) op die nu due zijn.
    due = c.execute(
        f"SELECT id, project, campaign, post_type FROM social_posts "
        f"WHERE campaign IN ({ph}) AND status='pending_review' AND post_type IN ('image','video') AND scheduled_for <= ? "
        f"ORDER BY scheduled_for", (*CAMPAIGNS, now)
    ).fetchall()
    if not due:
        print("Geen posts die nu moeten (alles op schema of al gepost)."); c.close(); return
    page_token_cache = {}
    async with httpx.AsyncClient() as client:
        for r in due:
            pid, proj, ptype = r["id"], r["project"], r["post_type"]
            doelgroep = _doelgroep_voor_campagne(r["campaign"])
            p = sc.get_pack(pid)
            age = AGE.get(doelgroep, "")
            cta = (p.copy or {}).get("cta", "") or ""   # eigen CTA uit de post (auto-comment)
            page_id, tok = await _resolve_pagina(client, doelgroep, page_token_cache)
            if not page_id or not tok:
                print(f"  [FOUT] {pid}: geen page-token voor doelgroep {doelgroep} — sla over"); continue
            if doelgroep == "main":
                guard_err = fb.check_age_targeting("DatingAssistent", (p.copy or {}).get("facebook", ""))
                if guard_err:
                    print(f"  [GEBLOKKEERD] {pid}: {guard_err}"); continue

            if ptype == "video":
                # ── VIDEO / REEL ──────────────────────────────────────────
                vp = (p.video_path or "").strip()
                if not vp or not os.path.exists(vp):
                    print(f"  [WACHT] {pid}: geen video_path — sla over"); continue
                rr = await client.post(f"{fb.GRAPH_API}/{page_id}/videos",
                    data={"description": (p.copy or {}).get("facebook", "")[:63000], "access_token": tok},
                    files={"source": open(vp, "rb")}, timeout=120)
                if rr.status_code != 200:
                    print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
                new_id = rr.json().get("id", "")
                ig_id, ig_tok = ig_svc._get_site_data(proj)
                if ig_id and ig_tok:
                    print(f"  [IG-VIDEO NIET ONDERSTEUND] {proj}: ig_svc heeft geen post_video() — sla IG over")
                else:
                    print(f"  [IG SKIP] {proj}: geen IG business-id gekoppeld")
                try:
                    await sac.auto_comment_after_post(new_id, proj, age, cta_text=cta, token_override=tok)
                except Exception as e:
                    print(f"  [COMMENT FOUT] {proj} {new_id}: {str(e)[:80]}")
                ib = dict(p.image_brief or {}); ib.update({"video_path": vp, "image_source": "video"})
                p.image_brief = ib
                with c:
                    c.execute("UPDATE social_posts SET image_brief_json=?, status='posted', posted_result_json=? WHERE id=?",
                        (json.dumps(ib, ensure_ascii=False),
                         json.dumps({"facebook": {"success": True, "post_id": new_id, "url": f"https://www.facebook.com/{new_id}", "site": proj}, "_platforms": ["facebook"]}, ensure_ascii=False),
                         pid))
                print(f"  [LIVE VIDEO] {proj:22} {pid} -> {new_id}")
                continue

            # ── IMAGE (bestaande flow) ───────────────────────────────────
            frag = IMG_MAP.get(pid)
            photo = _find(frag) if frag else None
            if not photo:
                photo = _find(FALLBACK.get(doelgroep, ""))
            if not photo:
                print(f"  [WACHT] {pid}: geen beeld in da_mj/ — sla over"); continue
            sub = (p.angle or "").split(" - ")[0][:60]
            out = f"data/uploads/pro_{pid}.png"
            render_da_card(photo, p.theme, sub, age, BRAND.get(doelgroep, proj), out)
            rr = await client.post(f"{fb.GRAPH_API}/{page_id}/photos",
                data={"message": (p.copy or {}).get("facebook", "")[:63000], "access_token": tok},
                files={"source": open(out, "rb")}, timeout=60)
            if rr.status_code != 200:
                print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
            new_id = rr.json().get("id", "")
            try:
                await sac.auto_comment_after_post(new_id, proj, age, cta_text=cta, token_override=tok)
            except Exception as e:
                print(f"  [COMMENT FOUT] {proj} {new_id}: {str(e)[:80]}")
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
