"""Publieke image-host voor Instagram/Reels.

Instagram Content Publishing vereist een PUBLIEKE image_url. Deze module probeert
meerdere hosts (voor resilience) en doet retries bij tijdelijke 5xx-storingen.

Hosts (zonder auth-node, wel betrouwbaar genoeg voor IG-tijdelijke posts):
  1. Imgur anoniem (Client-ID)
  2. fallback: catbox.moe (geen key nodig)

Gebruik:
  url = await host.upload("data/uploads/pro_sp_x.png")
"""
import asyncio, httpx, base64, os

IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID", "546c25a59c58ad3")
IMGUR_EP = "https://api.imgur.com/3/image"
CATBOX_EP = "https://catbox.moe/api.php"


async def _imgur(data_b64: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(IMGUR_EP,
            headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
            data={"image": data_b64, "type": "base64"}, timeout=60)
        if r.status_code == 200:
            return r.json()["data"]["link"]
        raise RuntimeError(f"Imgur {r.status_code}: {r.text[:120]}")


async def _catbox(path: str) -> str:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(CATBOX_EP, data={"reqtype": "fileupload"},
                              files={"fileToUpload": open(path, "rb")}, timeout=90)
        if r.status_code == 200 and r.text.startswith("https://"):
            return r.text.strip()
        raise RuntimeError(f"Catbox {r.status_code}: {r.text[:120]}")


async def upload(local_path: str) -> str:
    """Upload een lokaal bestand naar een publieke host, retourneer URL.
    Probeert Imgur, valt terug op catbox. Retry bij tijdelijke fouten."""
    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)
    b64 = base64.b64encode(open(local_path, "rb").read()).decode()
    last = None
    for attempt in range(3):
        try:
            try:
                return await _imgur(b64)
            except Exception as e:
                last = e
                return await _catbox(local_path)
        except Exception as e:
            last = e
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Alle hosts faalden: {last}")
