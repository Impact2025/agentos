"""DA geautomatiseerde post-engine — wereldklasse, hands-off (alle leeftijdsgroepen).

Pakt elke DA-pack uit campagne 'da-doelgroepen-2026' of 'da-week1..4' die:
  - status='pending_review'
  - scheduled_for <= nu
doet:
  1. ververs page-tokens (~1u geldig)
  2. classificeer per pack: video → Reel, image → foto‑card, tekst/link → feed‑post
  3. post naar FB (tekst + lokale foto-upload via Graph API)
  4. IG‑post (image of Reel) als ig_business gekoppeld (publieke host)
  5. auto-comment met leeftijd‑specifieke CTA‑link + hashtags (eérste reactie)

Idempotent: slaat over als al gepost. Veilig voor Cron.
"""
import asyncio, sqlite3, json, os, sys, glob, httpx
from datetime import datetime
# Zorg dat de repo‑root (bevat `backend/`) op sys.path zit, onafhankelijk
# van hoe het script wordt gestart (cron / direct / -m / exec). Mingw‑bash op
# Windows vertaalt geen env‑vars automatisch voor native python.exe, dus een
# expliciete sys.path‑bootstrap is de enige robuuste fix (Vincent, 31‑8‑2026).
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from backend.shared import facebook as fb
from backend.shared import social_content as sc
from backend.shared import social_auto_comment as sac
from backend.shared import instagram as ig_svc
from backend.shared import public_host as host
from scripts.da_template import render_da_card

# De echte AgentOS‑DB is data/agentos.db. Het oude harde pad 'data/impactos.db'
# was 0 bytes → de engine postte stilletjes tegen een lege DB ("Geen posts die
# nu moeten"). Env‑override voor lokale/dev runs, default = repo DB. Fallback
# zoekt ../data/agentos.db zodat cron in elke cwd werkt.
DB = str(os.environ.get("IMPACTOS_DB_PATH",
         os.path.join(os.path.dirname(__file__), "..", "data", "agentos.db")))
if not os.path.exists(DB):
    for cand in (os.path.join(os.path.dirname(__file__), "..", "data", "agentos.db"),
                 "data/agentos.db"):
        if os.path.exists(cand):
            DB = cand; break
MJ = "data/uploads/da_mj"
CAMPAIGNS = ("da-doelgroepen-2026", "da-week1", "da-week2", "da-week3", "da-week4")

# Wereldklasse-fix 21 aug 2026: alle DA-campagnepacks staan in de DB met
# project='DatingAssistent' (uniform, ongeacht doelgroep) — de oude routing
# via `fb._get_site_data(proj)` gaf daardoor VOOR ELKE campagne (30+/40+/50+
# én hoofdpagina) dezelfde ene pagina terug. `campaign` is per doelgroep uniek
# (zie seed-scripts: '...-40plus'/'...-50plus'-suffix, 'da-doelgroepen-2026'
# zonder suffix = 30+, 'da-week1..4' zonder suffix = hoofdpagina) — routeer
# daarop, niet op project. De hoofdpagina staat in de sites-tabel; de drie
# doelgroeppagina's bewust niet (zie facebook.check_age_targeting) en krijgen
# hun page-token hier rechtstreeks uit /me/accounts.
PAGE_IDS = {"30+": "107835799327006", "40+": "174410412641281", "50+": "123632714408933"}
AGE = {"30+": "30+", "40+": "40+", "50+": "50+", "main": ""}
BRAND = {"30+": "DatingAssistent", "40+": "DatingAssistent voor 40-plussers",
         "50+": "DatingAssistent voor 50-plussers", "main": "DatingAssistent.nl"}
FALLBACK = {"30+": "30_Warm_natural-light_portrait",
            "40+": "40_Genuine_smile_of_a_man_and_woman",
            "50+": "50_Two_people_in_their_50s",
            "main": "30_Warm_natural-light_portrait"}
IMG_MAP = {
    "sp_daDatingAssistent_02": "da30_2", "sp_daDatingAssistent_03": "da30_3", "sp_daDatingAssistent_04": "da30_4",
    "sp_da40_02": "da40_2", "sp_da40_03": "da40_3", "sp_da40_04": "da40_4",
    "sp_da50_02": "da50_2", "sp_da50_03": "da50_3", "sp_da50_04": "da50_4",
}


def _doelgroep_voor_campagne(campaign: str) -> str:
    c = campaign or ""
    if c.endswith("-40plus"): return "40+"
    if c.endswith("-50plus"): return "50+"
    if c == "da-doelgroepen-2026": return "30+"
    return "main"  # da-week1..4 zonder leeftijdssuffix = hoofdpagina


def _find(frag):
    hits = sorted(glob.glob(os.path.join(MJ, f"*{frag}*.png")))
    for h in hits:
        if h.endswith("_0.png") or "_0 (" in h:
            return h
    return hits[0] if hits else None


def _resolve_image_for_pack(p, pid, doelgroep):
    """Kies de juiste lokale beeldpoort voor de image‑branch. Retourneert (photo,
    is_remote) of (None, False) als er geen beeld beschikbaar is."""
    ib = p.image_brief or {}
    own_img = (ib.get("image_path") or ib.get("image_url") or "").strip()
    if own_img.startswith("http"):
        return own_img, True                       # reeds gepubliceerd URL
    if own_img and os.path.exists(own_img):
        return own_img, False                      # lokaal MJ‑beeld van de pack
    # fallback naar map‑specifieke of doelgroep‑fallback in da_mj/
    frag = IMG_MAP.get(pid)
    fb_local = _find(frag) if frag else None
    if not fb_local:
        fb_local = _find(FALLBACK.get(doelgroep, ""))
    return (fb_local, False) if fb_local else (None, False)


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
    if not glob_token:
        return PAGE_IDS[doelgroep], None
    r = await client.get(f"{fb.GRAPH_API}/me/accounts",
                         params={"fields": "id,access_token", "access_token": glob_token, "limit": 200},
                         timeout=25)
    if r.status_code == 200:
        for acc in r.json().get("data", []):
            for dg, pid in PAGE_IDS.items():
                if acc.get("id") == pid and acc.get("access_token"):
                    cache.setdefault(dg, acc["access_token"])
    return PAGE_IDS[doelgroep], cache.get(doelgroep)


def _persist(c, pid, ib_out, new_id, proj):
    with c:
        c.execute("UPDATE social_posts SET image_brief_json=?, status='posted', posted_result_json=? WHERE id=?",
                  (json.dumps(ib_out, ensure_ascii=False),
                   json.dumps({"facebook": {"success": True, "post_id": new_id,
                                           "url": f"https://www.facebook.com/{new_id}", "site": proj},
                               "_platforms": ["facebook"]}, ensure_ascii=False),
                   pid))


async def _post_to_ig(c, pid, p, fb_copy, proj, logger=print):
    """IG‑image post (Reels wordt in de video‑branch al gehandeld)."""
    ig_id, ig_tok = ig_svc._get_site_data(proj)
    if not ig_id or not ig_tok:
        logger(f"  [IG SKIP] {proj}: geen IG business-id/ token gekoppeld")
        return
    try:
        out_path = (p.image_brief or {}).get("image_path") or f"data/uploads/pro_{pid}.png"
        if out_path and os.path.exists(out_path):
            url = await host.upload(out_path)
        else:
            url = await host.upload(f"data/uploads/pro_{pid}.png")
        ir = await ig_svc.post_image(url, (p.copy or {}).get("instagram", fb_copy)[:2200], proj)
        logger(f"  [IG] {proj}: {'LIVE '+str(ir.get('post_id')) if ir.get('success') else 'SKIP '+str(ir.get('error',''))[:50]}")
    except Exception as e:
        logger(f"  [IG ERR] {proj}: {str(e)[:50]}")


async def main():
    print("  [STEP 1] Ververs page-tokens via fb.refresh_page_tokens() ...", flush=True)
    await fb.refresh_page_tokens()
    print("  [STEP 1] OK", flush=True)
    c = sqlite3.connect(DB, timeout=60, isolation_level=None)  # autocommit: per‑statement commit
    c.row_factory = sqlite3.Row
    # busy_timeout voor WAL lock contention met de AgentOS‑daemon
    c.execute("PRAGMA busy_timeout=60000")
    now = datetime.utcnow().isoformat()
    ph = ",".join("?" * len(CAMPAIGNS))
    # Alle due packs — image, video, link, text. Classificatie is data‑gedreven
    # (video_path / image_brief / feed) in plaats van via post_type, want
    # post_type is historisch leeg bij sommige da-weekN‑packs (Vincent 31‑8).
    due = c.execute(
        f"SELECT id, project, campaign, post_type FROM social_posts "
        f"WHERE campaign IN ({ph}) AND status='pending_review' AND scheduled_for <= ? "
        f"ORDER BY scheduled_for", (*CAMPAIGNS, now)
    ).fetchall()
    if not due:
        print("Geen posts die nu moeten (alles op schema of al gepost).", flush=True); c.close(); return
    print(f"  [{len(due)} due pack(s) gevonden]", flush=True)
    page_token_cache = {}
    async with httpx.AsyncClient() as client:
        for r in due:
            pid, proj, ptype = r["id"], r["project"], r["post_type"]
            doelgroep = _doelgroep_voor_campagne(r["campaign"])
            p = sc.get_pack(pid)
            if not p:
                print(f"  [FOUT] {pid}: pack niet gevonden — sla over", flush=True); continue
            try:
                age = AGE.get(doelgroep, "")
                cta = (p.copy or {}).get("cta", "") or ""
                print(f"  [STEP resolve] pagina voor {pid} ({doelgroep}) ...", flush=True)
                page_id, tok = await _resolve_pagina(client, doelgroep, page_token_cache)
                print(f"  [STEP resolve] OK: has_page={page_id is not None} has_tok={tok is not None}", flush=True)
            except Exception as e:
                print(f"  [FOUT] {pid}: pagina resolve failed: {str(e)[:80]}", flush=True); continue
            if not page_id or not tok:
                print(f"  [FOUT] {pid}: geen page-token voor doelgroep {doelgroep} — sla over", flush=True); continue
            if doelgroep == "main":
                guard_err = fb.check_age_targeting("DatingAssistent", (p.copy or {}).get("facebook", ""))
                if guard_err:
                    print(f"  [GEBLOKKEERD] {pid}: {guard_err}"); continue

            # Data‑gedreven classificatie.
            has_video = bool((p.video_path or "").strip()) and os.path.exists(p.video_path)
            own_img = ((p.image_brief or {}).get("image_path") or
                       (p.image_brief or {}).get("image_url") or "").strip()
            has_local_image = bool(own_img) and not own_img.startswith("http") and os.path.exists(own_img)
            photo_resolved, is_remote = _resolve_image_for_pack(p, pid, doelgroep)
            fb_copy = (p.copy or {}).get("facebook", "") or (p.copy or {}).get("text", "") or p.theme or ""

            if has_video:
                # ── VIDEO / REEL ───────────────────────────────────────
                print(f"  [STEP video] post {pid} ({proj}) naar FB videos/ ...", flush=True)
                vp = (p.video_path or "").strip()
                rr = await client.post(f"{fb.GRAPH_API}/{page_id}/videos",
                    data={"description": fb_copy[:63000], "access_token": tok},
                    files={"source": open(vp, "rb")}, timeout=120)
                if rr.status_code != 200:
                    print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
                new_id = rr.json().get("id", "")
                ig_id, ig_tok = ig_svc._get_site_data(proj)
                if ig_id and ig_tok:
                    try:
                        pub_url = await host.upload(vp)
                        ir = await ig_svc.post_video(pub_url, fb_copy[:2200], proj)
                        print(f"  [IG-REELS] {proj}: {'LIVE '+str(ir.get('post_id')) if ir.get('success') else 'SKIP '+str(ir.get('error',''))[:50]}")
                    except Exception as e:
                        print(f"  [IG VIDEO FOUT] {proj}: {str(e)[:60]}")
                else:
                    print(f"  [IG SKIP] {proj}: geen IG business-id gekoppeld")
                try:
                    await sac.auto_comment_after_post(new_id, proj, age, cta_text=cta, token_override=tok)
                except Exception as e:
                    print(f"  [COMMENT FOUT] {proj} {new_id}: {str(e)[:80]}")
                ib_out = dict(p.image_brief or {}); ib_out.update({"video_path": vp, "image_source": "video"})
                _persist(c, pid, ib_out, new_id, proj)
                print(f"  [LIVE VIDEO] {proj:22} {pid} -> {new_id}")
                continue

            if photo_resolved:
                # ── IMAGE (MJ‑beeld van de pack, fallback, of remote URL) ─
                sub = (p.angle or "").split(" - ")[0][:60]
                out = f"data/uploads/pro_{pid}.png"
                print(f"  [STEP image] post {pid} ({proj}) naar FB photos/ ({'remote' if is_remote else 'local'})...", flush=True)
                if is_remote:
                    # FB fetched de image zelf via url param.
                    rr = await client.post(f"{fb.GRAPH_API}/{page_id}/photos",
                        data={"message": fb_copy[:63000], "url": photo_resolved, "access_token": tok}, timeout=60)
                    src_tag = "remote"
                else:
                    render_da_card(photo_resolved, p.theme, sub, age, BRAND.get(doelgroep, proj), out)
                    rr = await client.post(f"{fb.GRAPH_API}/{page_id}/photos",
                        data={"message": fb_copy[:63000], "access_token": tok},
                        files={"source": open(out, "rb")}, timeout=60)
                    src_tag = "midjourney"
                if rr.status_code != 200:
                    print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
                new_id = rr.json().get("id", "")
            else:
                # ── TEKST / LINK (geen image, geen video) ────────────────
                print(f"  [STEP text] post {pid} ({proj}) naar FB feed/ ...", flush=True)
                rr = await client.post(f"{fb.GRAPH_API}/{page_id}/feed",
                    data={"message": fb_copy[:63000], "access_token": tok}, timeout=30)
                if rr.status_code != 200:
                    print(f"  [FOUT] {pid}: {rr.json().get('error',{}).get('message','?')[:80]}"); continue
                new_id = rr.json().get("id", "")
                print(f"  [LIVE TEXT] {proj:22} {pid} -> {new_id}")
                ib_out = dict(p.image_brief or {}); ib_out.update({"image_source": "tekstpost"})
                _persist(c, pid, ib_out, new_id, proj, )
                try:
                    await sac.auto_comment_after_post(new_id, proj, age, cta_text=cta, token_override=tok)
                except Exception as e:
                    print(f"  [COMMENT FOUT] {proj} {new_id}: {str(e)[:80]}")
                continue

            # Gemounte gedeelde epilog: auto-comment + IG‑image + persist.
            print(f"  [STEP comment] auto-comment voor {pid} ...", flush=True)
            try:
                await sac.auto_comment_after_post(new_id, proj, age, cta_text=cta, token_override=tok)
            except Exception as e:
                print(f"  [COMMENT FOUT] {proj} {new_id}: {str(e)[:80]}")
            print(f"  [STEP ig] IG-image post voor {pid} ...", flush=True)
            await _post_to_ig(c, pid, p, fb_copy, proj)
            ib_out = dict(p.image_brief or {}); ib_out.update({"image_path": f"data/uploads/pro_{pid}.png", "image_source": src_tag})
            _persist(c, pid, ib_out, new_id, proj)
            print(f"  [LIVE] {proj:22} {pid} -> {new_id}")
    c.close(); print("\nKlaar.")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=240))

