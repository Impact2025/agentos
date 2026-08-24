import json, re, urllib.request, ssl

HOST = "bewaardvoorjou.nl"
KEY = "5ea345ef169f44a79679b5df61c1ea6b"
SITEMAP = "https://bewaardvoorjou.nl/sitemap.xml"
KEY_URL = f"https://{HOST}/{KEY}.txt"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 1) Verify the IndexNow key file is publicly reachable
print("=== KEY FILE VERIFICATION ===")
try:
    req = urllib.request.Request(KEY_URL, headers={"User-Agent": "Hermes/1.0"})
    r = urllib.request.urlopen(req, timeout=15, context=ctx)
    body = r.read().decode().strip()
    print(f" {KEY_URL} -> HTTP {r.getcode()} | Content-Type={r.headers.get('Content-Type')}")
    print(f" body_contains_key={KEY in body} | body={body[:40]!r}")
except Exception as e:
    print(f" KEY FILE ERROR: {e}")

# 2) Fetch sitemap + extract URLs
print("\n=== FETCH SITEMAP ===")
req = urllib.request.Request(SITEMAP, headers={"User-Agent": "Hermes/1.0"})
resp = urllib.request.urlopen(req, timeout=15, context=ctx)
xml = resp.read().decode()
urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)
print(f" sitemap={SITEMAP} -> HTTP {resp.getcode()} | URLs found={len(urls)}")

# 3) Ping IndexNow (single POST, all URLs)
payload = {
    "host": HOST,
    "key": KEY,
    "keyLocation": KEY_URL,
    "urlList": urls,
}
data = json.dumps(payload).encode()
req2 = urllib.request.Request(
    "https://api.indexnow.org/indexnow",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    r2 = urllib.request.urlopen(req2, timeout=20, context=ctx)
    print(f"\n=== INDEXNOW PING ===")
    print(f" HTTP {r2.getcode()} | body={r2.read().decode()!r}")
    print(f" pinged {len(urls)} URLs")
except urllib.error.HTTPError as e:
    print(f"\n=== INDEXNOW PING ERROR ===")
    print(f" HTTP {e.code} | body={e.read().decode()!r}")
except Exception as e:
    print(f"\n=== INDEXNOW PING ERROR === {e}")
