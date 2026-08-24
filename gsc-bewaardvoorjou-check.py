import json
from datetime import date, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

CRED = r"D:\APPS\impactos\google-credentials.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE = "sc-domain:bewaardvoorjou.nl"

creds = service_account.Credentials.from_service_account_file(CRED, scopes=SCOPES)
gsc = build("webmasters", "v3", credentials=creds, cache_discovery=False)

print("=== SITES ACCESSIBLE TO THIS SERVICE ACCOUNT ===")
sites = gsc.sites().list().execute()
for s in sites.get("siteEntry", []):
    print(" -", s.get("siteUrl"), "|", s.get("permissionLevel"))

print("\n=== SITEMAPS FOR", SITE, "===")
try:
    sm = gsc.sitemaps().list(siteUrl=SITE).execute()
    for e in sm.get("sitemap", []):
        print(f" - path={e.get('path')} | submitted={e.get('lastSubmitted')} | "
              f"urls_submitted={e.get('contents',[{}])[0].get('submitted')} | "
              f"urls_indexed={e.get('contents',[{}])[0].get('indexed')}")
except Exception as ex:
    print(" ERROR sitemaps:", ex)

today = date(2026, 8, 23)
end = today - timedelta(days=1)   # yesterday (complete day)
start = end - timedelta(days=6)    # 7-day window inclusive
print(f"\n=== SEARCH ANALYTICS {start} -> {end} ===")

def query(dimensions, row_limit=10):
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "startRow": 0,
    }
    return gsc.searchanalytics().query(siteUrl=SITE, body=body).execute()

tot = query([])
rows = tot.get("rows", [])
if rows:
    r = rows[0]
    print(f" TOTAL: clicks={r.get('clicks'):.0f} | impressions={r.get('impressions'):.0f} | "
          f"ctr={r.get('ctr')*100:.2f}% | avg_position={r.get('position'):.1f}")
else:
    print(" TOTAL: no data (site likely not yet getting search traffic or not indexed)")

q = query(["query"], row_limit=5)
print("\n TOP 5 QUERIES:")
for r in q.get("rows", []):
    print(f"  '{r['keys'][0]}': clicks={r.get('clicks'):.0f} | impressions={r.get('impressions'):.0f} | "
          f"ctr={r.get('ctr')*100:.2f}% | pos={r.get('position'):.1f}")
if not q.get("rows"):
    print("  (no query data returned)")

print("\nDONE_GSC")
