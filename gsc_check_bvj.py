import json, re, urllib.request
from google.oauth2 import service_account
from googleapiclient.discovery import build

SITE = "sc-domain:bewaardvoorjou.nl"
CRED = r"D:\APPS\agentos\google-credentials.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

creds = service_account.Credentials.from_service_account_file(CRED, scopes=SCOPES)
gsc = build("webmasters", "v3", credentials=creds)

out = {}

# 1. Sitemaps
try:
    sm = gsc.sitemaps().list(siteUrl=SITE).execute()
    out["sitemaps"] = sm.get("sitemap", [])
except Exception as e:
    out["sitemaps_error"] = str(e)

# 2. Live sitemap URL count (submitted)
try:
    req = urllib.request.Request("https://bewaardvoorjou.nl/sitemap.xml",
                                 headers={"User-Agent": "Hermes/1.0"})
    body = urllib.request.urlopen(req, timeout=20).read().decode()
    locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body)
    out["sitemap_url_count"] = len(locs)
    out["sitemap_urls_sample"] = locs[:15]
except Exception as e:
    out["sitemap_fetch_error"] = str(e)

# 3. Search analytics: totals last 7 days
from datetime import date, timedelta
end = date(2026, 8, 8)          # yesterday (GSC lag)
start = end - timedelta(days=6) # 7-day window inclusive
def qa(dimensions, rowlimit=10):
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": rowlimit,
        "startRow": 0,
    }
    return gsc.searchanalytics().query(siteUrl=SITE, body=body).execute()

try:
    tot = qa([])
    out["totals_7d"] = tot.get("rows", [{}])[0] if tot.get("rows") else {}
except Exception as e:
    out["totals_error"] = str(e)

try:
    q = qa(["query"], 5)
    out["top_queries_7d"] = [
        {"query": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
         "ctr": round(r["ctr"], 4), "position": round(r["position"], 1)}
        for r in q.get("rows", [])
    ]
except Exception as e:
    out["queries_error"] = str(e)

# 4. Top pages
try:
    p = qa(["page"], 10)
    out["top_pages_7d"] = [
        {"page": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
         "ctr": round(r["ctr"], 4), "position": round(r["position"], 1)}
        for r in p.get("rows", [])
    ]
except Exception as e:
    out["pages_error"] = str(e)

print(json.dumps(out, indent=2, ensure_ascii=False))
