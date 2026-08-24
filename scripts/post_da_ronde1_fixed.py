"""FIX: haal per-pagina access-tokens op uit /me/accounts (voor de 3 DA-pagina's),
sla ze op in sites.facebook_page_token, en post de 3 openings direct in dezelfde
run (tokens zijn ~1u geldig). FB-module valt bij foto-misluk terug op tekst-only.

Na deze run staan de tokens in de DB; voor toekomstige posts (18-26 aug) moeten
ze ververst worden (cron of handmatig) — dat meld ik achteraf.
"""
import asyncio, sqlite3, json
from backend.shared import facebook as fb
from backend.shared import social_content as sc
import httpx

PACKS = ["sp_daDatingAssistent_01", "sp_da40_01", "sp_da50_01"]
DA_PAGE_IDS = ["107835799327006", "174410412641281", "123632714408933"]

async def fetch_page_tokens():
    tok = fb.FACEBOOK_PAGE_TOKEN
    r = await httpx.AsyncClient().get(
        "https://graph.facebook.com/v25.0/me/accounts",
        params={"fields": "name,id,access_token", "access_token": tok, "limit": 100}, timeout=30)
    j = r.json()
    m = {}
    for acc in j.get("data", []):
        if acc.get("id") in DA_PAGE_IDS:
            m[acc["id"]] = acc.get("access_token")
    return m

async def main():
    c = sqlite3.connect("data/impactos.db")
    tokens = await fetch_page_tokens()
    print("Opgehaalde page-tokens:", {k: (v[:8]+"..." if v else None) for k, v in tokens.items()})

    # sla op per site
    for pid in DA_PAGE_IDS:
        if tokens.get(pid):
            c.execute("UPDATE sites SET facebook_page_token=? WHERE facebook_page_id=?",
                      (tokens[pid], pid))
    c.commit()

    # post de 3 openings (met foto-probe, fallback tekst)
    results = []
    for pack_id in PACKS:
        p = sc.get_pack(pack_id)
        if not p:
            print(f"  {pack_id}: pack niet gevonden"); continue
        # ensure approved
        sc.approve_pack(pack_id)
        r = await sc.publish_pack(pack_id, "facebook")
        ok = r.get("success") and not r.get("manual")
        # status terugzetten indien mislukt zodat hij later opnieuw kan
        if not ok:
            with c:
                c.execute("UPDATE social_posts SET status='approved' WHERE id=?", (pack_id,))
        print(f"  {p.project:20} {pack_id:24} {'GEPLAATST' if ok else 'MISLUKT'}  {r.get('url') or r.get('error')}")
        results.append((p.project, ok))

    c.close()
    n_ok = sum(1 for _, ok in results if ok)
    print(f"\n=== RONDE 1: {n_ok}/{len(PACKS)} geplaatst ===")
    if n_ok < len(PACKS):
        print("Niet alle posts gelukt — zie errors hierboven.")

asyncio.run(main())
