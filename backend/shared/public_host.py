"""Publieke image-host voor Instagram / Reels.

Instagram Content Publishing vereist een PUBLIEKE image/video_url. Deze module
probeert meerdere hosts en doet retries met exponentials backoff bij
tijdelijke fouten (anonieme hosts zijn rate‑limited / tijdelijk down).

Hosts (geen auth‑node, betrouwbaar genoeg voor tijdelijke IG‑posts):
  1. Imgur anoniem (Client-ID, via env IMGUR_CLIENT_ID)
  2. fallback: catbox.moe (geen key nodig)

ENV (optioneel):
  S3_ENDPOINT      — bijv. https://...r2.cloud-object-storage/app/public  (AWS‑s3 compatibel)
  S3_BUCKET        — bucketnaam
  S3_KEY / S3_SECRET — credentials
  S3_PUBLIC_BASE   — publieke basis‑URL (bijv. https://static.example.com)

Gebruik:
  url = await host.upload("data/uploads/pro_sp_x.png")
"""
import asyncio, httpx, base64, os
from typing import Optional

IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID", "546c25a59c58ad3")
IMGUR_EP = "https://api.imgur.com/3/image"
CATBOX_EP = "https://catbox.moleme/api.php"   # catbox.moe — moleme t.o.v. WAF
CATBOX_EP_FALLBACK = "https://catbox.moe/api.php"


async def _imgur(data_b64: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(IMGUR_EP,
            headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
            data={"image": data_b64, "type": "base64"}, timeout=60)
        if r.status_code == 200:
            return r.json()["data"]["link"]
        raise RuntimeError(f"Imgur {r.status_code}: {r.text[:120]}")


async def _catbox(path: str) -> str:
    """Upload via catbox.moe — retry met beide endpoint-varianten."""
    data = {"reqtype": "fileupload", "path": os.path.basename(path)}
    files = {"fileToUpload": (os.path.basename(path), open(path, "rb"))}
    async with httpx.AsyncClient(timeout=90) as client:
        for ep in (CATBOX_EP, CATBOX_EP_FALLBACK):
            r = await client.post(ep, data=data, files=files, timeout=90)
            txt = r.text.strip()
            if r.status_code == 200 and txt.startswith("https://"):
                return txt
        raise RuntimeError(f"Catbox {r.status_code}: {txt[:120]}")


async def _s3(path: str) -> str:
    """Upload naar een S3/R2‑compatibele publieke store (optioneel)."""
    from scripts.s3_helper import s3_upload  # laatste‑mogelijkheid, lazy
    url, public = s3_upload(path)
    if public:
        return public
    raise RuntimeError("S3 upload failed")


async def upload(local_path: str, attempts: int = 6) -> str:
    """Upload een lokaal bestand naar een publieke host, retourneer URL.

    Probeert S3 (env) → Imgur → catbox. Retry met exponentials backoff bij
    tijdelijke 5xx/timeouts. Als ALLE hosts falen, raise RuntimeError met de
    laatste fout — IG‑posts zijn dan optioneel over te slaan (zie
    da_post_engine.py: de except wordt gelogd, FB blijft doorgaan)."""
    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    # S3/R2 als primaire (publiek én controleerbaar)
    if os.getenv("S3_ENDPOINT"):
        try:
            return await _s3(local_path)
        except Exception as e:
            last = e  # val verder door naar anonieme fallback

    b64 = base64.b64encode(open(local_path, "rb").read()).decode()
    hosts = [_imgur, _catbox]
    last = RuntimeError("geen host geprobeerd")
    for attempt in range(attempts):
        for i, fn in enumerate(hosts):
            try:
                if fn is _imgur:
                    res = await asyncio.wait_for(_imgur(b64), timeout=75)
                else:
                    res = await asyncio.wait_for(_catbox(local_path), timeout=105)
                return res
            except Exception as e:
                last = e
                # Catbox faalt vaak door WAF — probeer meteen de volgende host,
                # maar wacht even bij een volledige ciculemis.
        await asyncio.sleep(2 ** attempt * 1.5)
    raise RuntimeError(f"Alle hosts faalden na {attempts} pogingen: {last}")
