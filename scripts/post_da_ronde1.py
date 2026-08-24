"""PLAN: start DA-doelgroepen vandaag.
Ronde 1 (vandaag): openingsposts 30.1 / 40.1 / 50.1, elk met Pexels-foto,
elk op de juiste pagina. Rest (30.2-4, 40.2-4, 50.2-4) volgt op de
ingeroosterde datums (18-26 aug) met gevarieerde uren.

Flow per post:
  1. genereer on-brand foto (Pexels) -> sla op in data/uploads
  2. koppel image_path aan pack
  3. approve_pack (status -> approved)
  4. publish_pack(pack_id, 'facebook') -> echte post op de juiste pagina
     (facebook._get_site_data(project) kiest de pagina via de sites-tabel)
"""
import asyncio, os, sqlite3, json
from datetime import datetime
from backend.shared import social_image as img_svc
from backend.shared import social_content as sc

PACKS = ["sp_daDatingAssistent_01", "sp_da40_01", "sp_da50_01"]

async def gen_photo(pack_id):
    p = sc.get_pack(pack_id)
    ib = p.image_brief or {}
    res = img_svc.generate_social_image(
        p.theme, p.project,
        headline=ib.get("headline", ""),
        subtext=ib.get("subtext", ""),
    )
    if res.get("success"):
        print(f"  [{pack_id}] foto OK ({res.get('source')}): {res.get('url')}")
        return res.get("path", "")
    print(f"  [{pack_id}] foto MISLUKT ({res.get('error')}) -> val terug op tekst-only")
    return ""

async def main():
    c = sqlite3.connect("data/impactos.db")
    results = []
    for pid in PACKS:
        print(f"== {pid} ==")
        p = sc.get_pack(pid)
        if not p:
            print("  pack niet gevonden"); continue
        print(f"  project={p.project}  thema={p.theme}")
        img_path = await gen_photo(pid)
        # koppel foto + zet scheduled_for op vandaag (start)
        ib = dict(p.image_brief or {})
        if img_path:
            ib["image_path"] = img_path
            ib["image_url"] = ""
            ib["image_source"] = "pexels"
        with c:
            c.execute("UPDATE social_posts SET image_brief_json=? WHERE id=?",
                      (json.dumps(ib, ensure_ascii=False), pid))
            c.execute("UPDATE social_posts SET scheduled_for=? WHERE id=?",
                      (datetime.now().isoformat(), pid))
        # approve + publish
        sc.approve_pack(pid)
        r = await sc.publish_pack(pid, "facebook")
        ok = r.get("success") and not r.get("manual")
        print(f"  POST: {'OK' if ok else 'FOUT'} -> {r.get('url') or r.get('error')}")
        results.append((pid, p.project, ok, r.get("url") or r.get("error")))
    c.close()

    print("\n=== RONDE 1 RESULTAAT ===")
    for pid, proj, ok, url in results:
        print(f"  {proj:20} {pid:24} {'GEPLAATST' if ok else 'MISLUKT'}  {url}")

asyncio.run(main())
