"""Debug: waarom 403 op DA-pagina's maar (vermoedelijk) wel op LVI?
Controleer:
  - heeft LVI een eigen facebook_page_token in de DB (verklaart waarom LVI wél gepost werd)?
  - welke pages komen terug in /me/accounts mét access_token voor de System User?
  - probeer een test-post naar LVI via de globale token (sanity check).
GEEN schrijfacties naar DA-pagina's. Wel 1 test-post naar LVI (bekend werkend).
"""
import asyncio, sqlite3
from backend.shared import facebook as fb
import httpx

async def main():
    c = sqlite3.connect("data/agentos.db"); c.row_factory = sqlite3.Row

    print("=== 1. LVI eigen token vs DA tokens in sites-tabel ===")
    for r in c.execute("SELECT name, facebook_page_id, facebook_page_token FROM sites WHERE name LIKE 'DatingAssistent%' OR name='Liefde voor Iedereen'"):
        tok = r["facebook_page_token"] or ""
        print(f"  {r['name']:22} fb={r['facebook_page_id']}  token={'JA('+str(len(tok))+')' if tok else 'NEE (fallback globaal)'}")

    tok = fb.FACEBOOK_PAGE_TOKEN
    print(f"\nGlobale FACEBOOK_PAGE_TOKEN lengte: {len(tok or '')}")

    print("\n=== 2. /me/accounts — welke pages mét access_token? ===")
    r = await httpx.AsyncClient().get(
        "https://graph.facebook.com/v25.0/me/accounts",
        params={"fields": "name,id,access_token", "access_token": tok, "limit": 100}, timeout=30)
    j = r.json()
    if "error" in j:
        print("  /me/accounts FOUT:", j["error"].get("message","?")[:120]); return
    for acc in j.get("data", []):
        at = acc.get("access_token")
        if any(d in acc["name"] for d in ["Dating", "Liefde"]):
            print(f"  {acc['name']:35} id={acc['id']}  page_token={'JA' if at else 'NEE'}")

    print("\n=== 3. Sanity: probeer LVI (118351184938436) bereik + 1 test-post ===")
    pid_lvi = "118351184938436"
    info = await httpx.AsyncClient().get(
        f"https://graph.facebook.com/v25.0/{pid_lvi}",
        params={"fields": "name", "access_token": tok}, timeout=30)
    print("  LVI info:", info.json().get("name"), "| error:", info.json().get("error"))

asyncio.run(main())
