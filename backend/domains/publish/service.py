"""
Netlify Publisher — zet Loop-geproduceerde SEO-artikelen live op een Netlify-site.

Sluit de Demand Engine-keten: zoekwoord → schrijven (Loop) → PUBLICEREN → (GSC-terugkoppeling).

Werkwijze (atomic full-site deploy):
  * Elke publicatie wordt opgeslagen in `published_pages` (per site, op slug).
  * Bij elke publicatie bouwen we de VOLLEDIGE statische site opnieuw op (een index
    met alle artikelen + één pagina per artikel) en deployen die als één zip naar
    Netlify. Een Netlify-deploy is altijd een volledige momentopname, dus zo gaan
    eerdere artikelen niet verloren.

Config:
  * Per site (sites-tabel): `publish_api_url` = Netlify **site API ID**,
    `publish_api_key` = (optioneel) Netlify Personal Access Token voor die site.
  * Globale fallback-token: `NETLIFY_TOKEN` in .env.
"""
from __future__ import annotations

import base64
import html as _html
import io
import logging
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

import httpx

from ...shared.config import NETLIFY_TOKEN
from ...shared.database import get_conn
from ...domains.seo import sites as sites_service

logger = logging.getLogger(__name__)

NETLIFY_API = "https://api.netlify.com/api/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "artikel"


# ── Persistence ──────────────────────────────────────────────────────────────

def _upsert_page(site_id: str, slug: str, title: str, html_body: str,
                  image_bytes: Optional[bytes] = None,
                  infographic_bytes: Optional[bytes] = None) -> Dict:
    now = _now()
    image_b64 = base64.b64encode(image_bytes).decode("ascii") if image_bytes else None
    infographic_b64 = base64.b64encode(infographic_bytes).decode("ascii") if infographic_bytes else None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, created_at FROM published_pages WHERE site_id = ? AND slug = ?",
            (site_id, slug),
        ).fetchone()
        if row:
            sets, params = ["title = ?", "html = ?", "updated_at = ?"], [title, html_body, now]
            if image_b64 is not None:
                sets.append("image_b64 = ?")
                params.append(image_b64)
            if infographic_b64 is not None:
                sets.append("infographic_b64 = ?")
                params.append(infographic_b64)
            params.append(row["id"])
            conn.execute(f"UPDATE published_pages SET {', '.join(sets)} WHERE id = ?", params)
            pid = row["id"]
        else:
            pid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO published_pages (id, site_id, slug, title, html, image_b64, infographic_b64, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, site_id, slug, title, html_body, image_b64 or "", infographic_b64 or "", now, now),
            )
    return {"id": pid, "slug": slug, "title": title}


def list_pages(site_id: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, slug, title, url, created_at, updated_at FROM published_pages "
            "WHERE site_id = ? ORDER BY updated_at DESC",
            (site_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _all_pages_full(site_id: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slug, title, html, image_b64, infographic_b64, updated_at FROM published_pages "
            "WHERE site_id = ? ORDER BY updated_at DESC",
            (site_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _set_page_url(site_id: str, slug: str, url: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE published_pages SET url = ? WHERE site_id = ? AND slug = ?",
            (url, site_id, slug),
        )


# ── Statische site bouwen ────────────────────────────────────────────────────

_CSS = (
    "*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,"
    "Helvetica,Arial,sans-serif;color:#1f2933;line-height:1.7;background:#fff}"
    ".wrap{max-width:720px;margin:0 auto;padding:48px 20px}"
    "article h1{font-size:2rem;line-height:1.25;margin:.2em 0 .6em}"
    "article h2{font-size:1.4rem;margin:1.6em 0 .4em}article h3{font-size:1.15rem;margin:1.3em 0 .3em}"
    "article p{margin:0 0 1em}article ul,article ol{padding-left:1.4em;margin:0 0 1em}"
    "article table{border-collapse:collapse;width:100%;margin:1em 0}"
    "article th,article td{border:1px solid #d8dee4;padding:8px 12px;text-align:left}"
    "a{color:#3b5bdb}.back{margin-top:3em;font-size:.9rem}"
    "ul.index{list-style:none;padding:0}ul.index li{margin:.4em 0;font-size:1.1rem}"
    "header.site{border-bottom:1px solid #e6e8eb;margin-bottom:2em;padding-bottom:1em}"
)

_PAGE_TMPL = (
    "<!doctype html><html lang=\"nl\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<title>{title}</title><meta name=\"description\" content=\"{desc}\">"
    "<style>{css}</style></head><body><main class=\"wrap\"><article>{body}</article>"
    "<p class=\"back\"><a href=\"/\">← Terug naar overzicht</a></p></main></body></html>"
)

_INDEX_TMPL = (
    "<!doctype html><html lang=\"nl\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<title>{title}</title><style>{css}</style></head><body><main class=\"wrap\">"
    "<header class=\"site\"><h1>{title}</h1></header>"
    "<ul class=\"index\">{items}</ul></main></body></html>"
)


def _site_base_url(site: Dict) -> str:
    """Absolute basis-URL van de site: ingesteld base_url, anders afgeleid uit
    een eerder gedeployde pagina-URL (Netlify-URL's zijn stabiel per site)."""
    base = (site.get("base_url") or "").strip().rstrip("/")
    if base:
        return base
    with get_conn() as conn:
        row = conn.execute(
            "SELECT url FROM published_pages WHERE site_id = ? AND url != '' "
            "ORDER BY updated_at DESC LIMIT 1",
            (site["id"],),
        ).fetchone()
    if row and row["url"].startswith("http"):
        parsed = row["url"].split("/", 3)
        return f"{parsed[0]}//{parsed[2]}"
    return ""


def _sitemap_xml(base_url: str, pages: List[Dict]) -> str:
    """sitemap.xml voor de statische site — deze wordt na elke publicatie bij
    Google Search Console ingediend, dus hij moet ook echt bestaan."""
    entries = [f"  <url><loc>{_html.escape(base_url)}/</loc></url>"]
    for p in pages:
        lastmod = (p.get("updated_at") or "")[:10]
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        entries.append(
            f"  <url><loc>{_html.escape(base_url)}/{p['slug']}/</loc>{lastmod_tag}</url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) + "\n</urlset>\n"
    )


def build_site_files(site_id: str, site_name: str, base_url: str = "",
                     indexnow_key: str = "") -> Dict[str, Union[str, bytes]]:
    """Bouw alle bestanden voor de volledige statische site (testbaar zonder netwerk).

    Elke pagina met een opgeslagen quote-card (image_b64) krijgt ook een
    `images/{slug}.png`-bestand mee, zodat er een publieke image-url bestaat
    (nodig voor Instagram, dat zelf geen bestanden accepteert — alleen URL's).
    Met een bekende `base_url` komt er ook een sitemap.xml mee (die wordt na
    publicatie bij GSC ingediend); met een `indexnow_key` het key-bestand dat
    IndexNow op de site-root verwacht."""
    pages = _all_pages_full(site_id)
    files: Dict[str, Union[str, bytes]] = {}
    items = "".join(
        f'<li><a href="/{p["slug"]}/">{_html.escape(p["title"] or p["slug"])}</a></li>'
        for p in pages
    ) or "<li>Nog geen artikelen.</li>"
    files["index.html"] = _INDEX_TMPL.format(
        title=_html.escape(site_name or "Blog"), css=_CSS, items=items
    )
    for p in pages:
        title = _html.escape(p["title"] or p["slug"])
        files[f'{p["slug"]}/index.html'] = _PAGE_TMPL.format(
            title=title, desc=title, css=_CSS, body=p["html"] or "",
        )
        if p.get("image_b64"):
            try:
                files[f'images/{p["slug"]}.png'] = base64.b64decode(p["image_b64"])
            except Exception:
                pass
        if p.get("infographic_b64"):
            try:
                files[f'images/{p["slug"]}-infographic.png'] = base64.b64decode(p["infographic_b64"])
            except Exception:
                pass
    if base_url:
        files["sitemap.xml"] = _sitemap_xml(base_url.rstrip("/"), pages)
    if indexnow_key:
        files[f"{indexnow_key}.txt"] = indexnow_key
    return files


def embed_infographic_html(html_body: str, slug: str, title: str) -> str:
    """Zet de infographic als <figure> in het artikel — ná de eerste sectie
    (vóór de tweede <h2>), zodat hij boven de vouw van het inhoudelijke deel
    zit. Google Afbeeldingen indexeert alleen afbeeldingen die écht in een
    pagina staan, met alt-tekst en een sprekende bestandsnaam. Idempotent."""
    src = f"/images/{slug}-infographic.png"
    if src in (html_body or ""):
        return html_body
    alt = _html.escape(f"Infographic: {title}", quote=True)
    caption = _html.escape(title)
    figure = (
        f'\n<figure><img src="{src}" alt="{alt}" width="1080" height="1350" '
        f'loading="lazy"><figcaption>{caption} — samengevat in beeld</figcaption></figure>\n'
    )
    h2s = [m.start() for m in re.finditer(r"<h2[\s>]", html_body, re.IGNORECASE)]
    if len(h2s) >= 2:
        pos = h2s[1]
        return html_body[:pos] + figure + html_body[pos:]
    return html_body + figure


def _zip_files(files: Dict[str, Union[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(path, content)
    return buf.getvalue()


# ── Deploy ───────────────────────────────────────────────────────────────────

async def _deploy_zip(site_api_id: str, token: str, zip_bytes: bytes) -> Dict:
    url = f"{NETLIFY_API}/sites/{site_api_id}/deploys"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/zip"}
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, content=zip_bytes, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def publish_article(
    site_id: str, title: str, html_body: str, slug: Optional[str] = None,
    image_bytes: Optional[bytes] = None, infographic_bytes: Optional[bytes] = None,
) -> Dict:
    """Sla het artikel op, herbouw de site en deploy naar Netlify. Retourneert live-URL."""
    site = sites_service.get_site(site_id)  # volledige rij incl. token
    if not site:
        raise ValueError("Site niet gevonden.")
    site_api_id = (site.get("publish_api_url") or "").strip()
    token = (site.get("publish_api_key") or "").strip() or NETLIFY_TOKEN
    if not site_api_id:
        raise ValueError(
            "Deze site heeft geen Netlify site-ID. Vul het 'Publicatie-API-URL'-veld "
            "(= Netlify site API ID) in bij de site."
        )
    if not token:
        raise ValueError(
            "Geen Netlify-token gevonden. Zet NETLIFY_TOKEN in .env of vul een "
            "publicatie-sleutel in bij de site."
        )
    if not (title or "").strip():
        raise ValueError("Een titel is verplicht.")
    if not (html_body or "").strip():
        raise ValueError("Lege artikelinhoud.")

    slug = slugify(slug or title)
    if infographic_bytes:
        html_body = embed_infographic_html(html_body, slug, title.strip())
    _upsert_page(site_id, slug, title.strip(), html_body,
                 image_bytes=image_bytes, infographic_bytes=infographic_bytes)

    # Sitemap (voor de GSC-submit) + IndexNow-key-bestand meedeployen. Bij de
    # allereerste deploy is de basis-URL nog onbekend — dan komt de sitemap
    # vanaf de tweede publicatie mee (Netlify-URL's zijn stabiel).
    from . import indexing as indexing_service
    base_url = _site_base_url(site)
    indexnow_key = indexing_service.ensure_indexnow_key(site)
    files = build_site_files(site_id, site.get("name") or "Blog",
                             base_url=base_url, indexnow_key=indexnow_key)
    zip_bytes = _zip_files(files)
    deploy = await _deploy_zip(site_api_id, token, zip_bytes)

    base = (deploy.get("ssl_url") or deploy.get("url") or "").rstrip("/")
    page_url = f"{base}/{slug}/" if base else f"/{slug}/"
    _set_page_url(site_id, slug, page_url)
    image_url = f"{base}/images/{slug}.png" if (base and f'images/{slug}.png' in files) else None
    infographic_url = (f"{base}/images/{slug}-infographic.png"
                       if (base and f'images/{slug}-infographic.png' in files) else None)
    logger.info("Gepubliceerd: %s → %s (deploy %s)", slug, page_url, deploy.get("state"))

    return {
        "slug": slug,
        "image_url": image_url,
        "infographic_url": infographic_url,
        "title": title.strip(),
        "url": page_url,
        "site_url": base,
        "deploy_state": deploy.get("state"),
        "pages": len(files) - 1,  # minus index.html
    }
