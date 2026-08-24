"""Plaats de 3 DA-openingsposts (met foto) op de juiste pagina via de page-tokens
uit de DB. Verifieert daarna live of ze op FB staan en logt naar fb_posts.
"""
import asyncio, sqlite3, json, httpx
from backend.shared import facebook as fb
from backend.shared import social_content as sc
import os

PACKS = ["sp_daDatingAssistent_01", "sp_da40_01", "sp_da50_01"]
UPLOADS = os.path.join("data", "uploads")

async def post_with_photo(pack_id, client):
    p = sc.get_pack(pack_id)
    ib = p.image_brief or {}
    img_path = ib.get("image_path", "")
    text = (p.copy or {}).get("facebook", "")
    pid, tok = fb._get_site_data(p.project)
    if not pid or not tok:
        return False, f"geen page-id/token voor {p.project}"
    # foto?
    if img_path and os.path.exists(img_path):
        with open(img_path, "rb") as fh:
            r = await client.post(f"{fb.GRAPH_API}/{pid}/photos",
                data={"message": text[:63000], "access_token": tok},
                files={"source": fh}, timeout=60)
        if r.status_code == 200:
            post_id = r.json().get("id", "")
            return True, post_id
        # anders tekst-only
    r = await client.post(f"{fb.GRAPH_API}/{pid}/feed",
        data={"message": text[:63000], "access_token": tok}, timeout=30)
    if r.status_code == 200:
        return True, r.json().get("id", "")
    return False, str(r.json().get("error", {}).get("message", ""))[:100]

async def main():
    c = sqlite3.connect("data/impactos.db")
    results = []
    async with httpx.AsyncClient() as client:
        for pack_id in PACKS:
            ok, res = await post_with_photo(pack_id, client)
            if ok:
                # log naar fb_posts + status=posted
                p = sc.get_pack(pack_id)
                with c:
                    c.execute("UPDATE social_posts SET status='posted' WHERE id=?", (pack_id,))
                    try:
                        from backend.domains.analytics.facebook_store import log_fb_post
                        log_fb_post(res, p.project, message=(p.copy or {}).get("facebook","")[:200])
                    except Exception as e:
                        print(f"  (log overslaan: {e})")
                # verifieer live
                vr = await client.get(f"{fb.GRAPH_API}/{res}", params={"fields":"id","access_token":res.split("_")[0] and fb._get_site_data(p.project)[1]}, timeout=30)
                live = "JA" if vr.json().get("id") else "NEE"
                print(f"  {p.project:20} {pack_id:24} GEPLAATST -> {res} | live={live}")
                results.append((p.project, True))
            else:
                print(f"  {pack_id}: MISLUKT -> {res}")
                results.append((pack_id, False))
    c.close()
    print(f"\n=== {sum(1 for _,ok in results if ok)}/{len(PACKS)} openings live ===")

asyncio.run(main())
