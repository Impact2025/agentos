"""
Daar.nl — Sitemap indienen bij Google Search Console.

Voorwaarde: de property sc-domain:daar.nl moet in GSC geverifieerd zijn
(DNS-verificatie van het domein) EN het service account
hermes-analytics@weareimpact-482912.iam.gserviceaccount.com moet als
gebruiker zijn toegevoegd aan de property.

Run:
  D:/APPS/agentos/.venv/Scripts/python.exe scripts/daar-submit-sitemap.py

Na succes: GSC toont de sitemap onder "Sitemaps" met status "Voltooid"
(duurt enkele minuten tot uren voor eerste crawl).
"""
import sys
sys.path.insert(0, r"D:/APPS/agentos")

from backend.domains.seo import gsc

PROPERTY = "sc-domain:daar.nl"
SITEMAP_PATH = "https://www.daar.nl/sitemap.xml"


def main():
    svc = gsc._get_service()
    try:
        resp = (
            svc.sitemaps()
            .submit(siteUrl=PROPERTY, feedpath=SITEMAP_PATH)
            .execute()
        )
        print(f"OK — sitemap ingediend: {SITEMAP_PATH}")
        print("Response:", resp if resp else "(leeg = succes)")
    except Exception as e:
        err = str(e)
        if "403" in err:
            print("403 — property nog niet geverifieerd OF service account")
            print("    mist toegang. Do:")
            print("    1. DNS-verificatie van daar.nl in GSC voltooien")
            print("    2. Service account toevoegen als gebruiker in GSC")
            print("    3. Script opnieuw runnen")
        else:
            print("Fout:", err[:300])


if __name__ == "__main__":
    main()
