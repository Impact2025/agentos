import urllib.request, urllib.error, json, re

HOST = "bewaardvoorjou.nl"
KEY = "5ea345ef169f44a79679b5df61c1ea6b"
KEYLOC = f"https://{HOST}/{KEY}.txt"
SITEMAP = f"https://{HOST}/sitemap.xml"

# Fetch sitemap + extract URLs
req = urllib.request.Request(SITEMAP, headers={"User-Agent": "Hermes/1.0"})
body = urllib.request.urlopen(req, timeout=20).read().decode()
urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body)
print(f"Extracted {len(urls)} URLs from sitemap")

# Verify key file reachable
kr = urllib.request.urlopen(urllib.request.Request(KEYLOC, headers={"User-Agent": "Hermes/1.0"}), timeout=15)
kbody = kr.read().decode().strip()
print(f"Key file HTTP {kr.getcode()}, content matches key: {kbody == KEY}")

# Ping IndexNow
payload = {"host": HOST, "key": KEY, "keyLocation": KEYLOC, "urlList": urls}
data = json.dumps(payload).encode()
req2 = urllib.request.Request(
    "https://api.indexnow.org/indexnow",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    resp = urllib.request.urlopen(req2, timeout=20)
    print(f"IndexNow HTTP {resp.getcode()}")
    print(resp.read().decode()[:200])
except urllib.error.HTTPError as e:
    print(f"IndexNow HTTP ERROR {e.code}: {e.read().decode()[:300]}")
