"""
Daar.nl — dagelijkse IndexNow-ping.

Haalt de live sitemap op, extraheert alle URLs en pingt ze in batches van 10
naar api.indexnow.org. Draait als no_agent cron job (0 8 * * *) zodat nieuwe
content die tussen deploys door gepubliceerd wordt snel geindexeerd blijft.

Key: 650b5a5027da410fbccebd304bc176ec (staat ook in ImpactOS sites-DB).
"""
import urllib.request
import urllib.error
import json
import re
import time
import sys

SITEMAP_URL = "https://www.daar.nl/sitemap.xml"
HOST = "www.daar.nl"
KEY = "650b5a5027da410fbccebd304bc176ec"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
INDEXNOW_EP = "https://api.indexnow.org/indexnow"


def fetch_urls() -> list[str]:
    req = urllib.request.Request(SITEMAP_URL, headers={"User-Agent": "Hermes/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        xml = resp.read().decode("utf-8", "replace")
    return re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)


def ping_batch(batch: list[str]) -> int:
    payload = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": batch,
    }
    req = urllib.request.Request(
        INDEXNOW_EP,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.getcode()


def main() -> None:
    try:
        urls = fetch_urls()
    except Exception as e:
        print(f"FETCH_ERROR sitemap: {e}")
        sys.exit(1)

    if not urls:
        print("GEEN_URLS in sitemap")
        sys.exit(0)

    batches = [urls[i : i + 10] for i in range(0, len(urls), 10)]
    ok = 0
    for i, batch in enumerate(batches, 1):
        try:
            code = ping_batch(batch)
            print(f"batch {i}/{len(batches)}: HTTP {code} ({len(batch)} urls)")
            ok += 1
        except urllib.error.HTTPError as e:
            print(f"batch {i}: HTTP {e.code} {e.read().decode()[:120]}")
        except Exception as e:
            print(f"batch {i}: ERR {e}")
        time.sleep(1)

    print(f"INDEXNOW_DONE {ok}/{len(batches)} batches, {len(urls)} urls")


if __name__ == "__main__":
    main()
