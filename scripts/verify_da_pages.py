"""Verificatie vóór live posten: bereikt de token alle 3 DA-pagina's, welke
pack-IDs staan klaar, en is image-generatie beschikbaar? Read-only (GEEN posts)."""
import asyncio, os, sqlite3
from backend.shared import facebook as fb
from backend.shared import social_image as img_svc

async def main():
    print("=== 1. Token-bereik per DA-pagina (READ-ONLY GET) ===")
    reach = {}
    for site in ["DatingAssistent", "DatingAssistent 40+", "DatingAssistent 50+"]:
        pid, tok = fb._get_site_data(site)
        print(f"\n{site}\n  page_id={pid}  token_len={len(tok or '')}")
        if not pid or not tok:
            print("  !! geen page_id/token — posten onmogelijk"); reach[site]=False; continue
        import httpx
        try:
            r = await httpx.AsyncClient().get(
                f"https://graph.facebook.com/v25.0/{pid}",
                params={"fields": "name,fan_count", "access_token": tok}, timeout=30)
            j = r.json()
            if "error" in j:
                print(f"  !! FOUT: {j['error'].get('message','?')[:100]}"); reach[site]=False
            else:
                print(f"  OK -> naam='{j.get('name')}'  fans={j.get('fan_count')}"); reach[site]=True
        except Exception as e:
            print(f"  !! exception: {e}"); reach[site]=False

    print("\n=== 2. Klaarstaande DA-packs (campagne da-doelgroepen-2026) ===")
    c = sqlite3.connect("data/impactos.db"); c.row_factory = sqlite3.Row
    for r in c.execute(
        "SELECT id, project, campaign_post, status, scheduled_for FROM social_posts "
        "WHERE campaign='da-doelgroepen-2026' ORDER BY project, campaign_post"):
        print(f"  {r['id']:24} {r['project']:20} {r['campaign_post']:5} {r['status']:13} {r['scheduled_for']}")

    print("\n=== 3. Image-generatie beschikbaar? ===")
    try:
        fal = bool(os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"))
        pex = bool(os.environ.get("PEXELS_API_KEY") or os.environ.get("PEXELS_KEY"))
        print(f"  FAL_KEY aanwezig: {fal} | PEXELS aanwezig: {pex}")
        print(f"  social_image module geladen: OK")
    except Exception as e:
        print(f"  image-check fout: {e}")

    print("\n=== SAMENVATTING ===")
    ok = [s for s,v in reach.items() if v]
    print("Bereikbaar:", ", ".join(ok) if ok else "GEEN")
    print("Niet bereikbaar:", ", ".join(s for s,v in reach.items() if not v) or "-")

asyncio.run(main())
