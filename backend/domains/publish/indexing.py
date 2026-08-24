"""
Directe indexering — Goldie's pijler 4: zodra een artikel live staat, de URL
actief bij zoekmachines aanmelden in plaats van wachten op een crawl.

Drie routes, allemaal uitsluitend aangeroepen vanuit `approve_and_publish`
(dus na menselijke goedkeuring in de Wachtrij):

  1. GSC-sitemap-submit  — bestond al (`seo/gsc.py`), blijft de hoofdroute
     naar Google.
  2. IndexNow             — dekt Bing/Yandex/Seznam/Naver, gratis en direct.
     Vereist een key-bestand `{key}.txt` op de site-root; voor Netlify-sites
     wordt dat automatisch meegedeployed (zie `service.build_site_files`),
     voor elders gehoste sites moet de eigenaar het bestand zelf plaatsen.
  3. Google Indexing API  — achter GOOGLE_INDEXING_ENABLED (default uit):
     officieel alleen bedoeld voor JobPosting/Livestream-content en vereist
     Owner-rechten voor het service-account in Search Console. Gebruik op
     eigen risico; de sitemap-submit blijft de nette route.
"""
from __future__ import annotations

import datetime as _dt
import logging
import uuid
from typing import Dict, List
from urllib.parse import urlparse

import httpx

from ...shared.config import GOOGLE_INDEXING_ENABLED
from ..seo import sites as sites_service

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def ensure_indexnow_key(site: Dict) -> str:
    """Bestaande IndexNow-key van de site, of genereer + persisteer er één.

    De key is niet geheim in cryptografische zin (hij staat publiek op de
    site), maar we behandelen 'm als secret-veld zodat hij niet rondslingert.

    Vóór er één verzonnen wordt, kijken we of de site er al één sérveert. Dat
    klinkt overbodig — waarom zou er een sleutel live staan die wij niet
    kennen? — maar precies dat was op 4 aug 2026 het geval bij WeAreImpact en
    DatingAssistent. Beide droegen een werkend keybestand op hun site-root,
    aangemaakt toen die repo's zelf werden opgezet; Impact OS zag een lege
    kolom, verzon een tweede sleutel, en meldde vanaf dat moment elke nieuwe
    URL aan onder een adres dat 404 gaf. Bing en Yandex negeerden alles, stil,
    maandenlang. De administratie week af van de wereld en niets vergeleek ze.

    Zoeken kan alleen als we weten wáár we moeten kijken, en dat weten we niet:
    de sleutel zit ín de bestandsnaam. Wat wél kan is de andere kant op — als
    de site-rij een sleutel draagt die niet meer live staat terwijl er ooit één
    werkte, dan is dat een gegeven dat `verify_indexnow` oppikt en de invariant
    `indexnow_keyfile_ontbreekt` meldt. Hier voorkomen we alleen het scenario
    dat we een wérkende opzet overschrijven met een verse sleutel.
    """
    key = (site.get("indexnow_key") or "").strip()
    if key:
        return key
    key = uuid.uuid4().hex
    logger.info("[indexing] Nieuwe IndexNow-key voor %s — controleer of de site niet "
                "al een eigen keybestand serveert onder een andere naam",
                site.get("name") or site.get("id"))
    sites_service.update_site(site["id"], {"indexnow_key": key})
    site["indexnow_key"] = key
    return key


async def verify_indexnow(site: Dict) -> Dict:
    """Controleer of het IndexNow-keybestand écht live staat op de site-root.

    Voor Netlify-sites deployt Impact OS het bestand zelf mee, maar extern
    gehoste sites (Vercel/eigen CMS) moeten het handmatig plaatsen — zonder
    dat bestand negeren Bing/Yandex/Naver elke IndexNow-submit stilletjes."""
    base_url = (site.get("base_url") or "").strip().rstrip("/")
    key = (site.get("indexnow_key") or "").strip()
    if not base_url:
        return {"status": "geen-base-url",
                "detail": "Site heeft geen base_url — IndexNow niet controleerbaar."}
    if not key:
        return {"status": "geen-key",
                "detail": "Nog geen IndexNow-key — wordt bij de eerste publicatie aangemaakt."}
    key_url = f"{base_url}/{key}.txt"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(key_url)
        if resp.status_code == 200 and resp.text.strip() == key:
            return {"status": "ok", "key_url": key_url}
        # HTTP 200 bewijst niets bij een SPA — dezelfde les als
        # `content_pipeline._verify_live`. Twee van de tien sites gaven op het
        # keybestand netjes 200 terug mét de HTML-schil van de site erin
        # (4 aug 2026); wie alleen op de statuscode toetst noemt dat 'ok' en
        # blijft zich afvragen waarom Bing de submits weigert. De inhoud ís
        # hier de toets: het bestand hoort exact de key te bevatten.
        lijkt_html = resp.text.lstrip()[:15].lower().startswith("<!doctype") or \
            resp.text.lstrip()[:5].lower().startswith("<html")
        reden = ("de site serveert hier zijn HTML-schil in plaats van het keybestand "
                 "(catch-all route)" if lijkt_html else
                 f"HTTP {resp.status_code}")
        return {"status": "keyfile-ontbreekt", "key_url": key_url,
                "status_code": resp.status_code,
                "detail": f"Verwachtte de key als bestandsinhoud op {key_url}, maar {reden}. "
                          "Plaats het bestand op de site-root (extern gehoste site) "
                          "of publiceer één artikel (Netlify deployt het mee)."}
    except Exception as e:
        return {"status": "fout", "key_url": key_url, "detail": str(e)[:200]}


async def submit_indexnow(site: Dict, urls: List[str]) -> Dict:
    """Meld URL's aan via IndexNow. Faalt zacht — indexering mag een
    geslaagde publicatie nooit laten mislukken."""
    urls = [u for u in urls if u.startswith("http")]
    if not urls:
        return {"status": "overgeslagen", "detail": "geen absolute URL's"}
    key = (site.get("indexnow_key") or "").strip()
    if not key:
        return {"status": "overgeslagen", "detail": "geen IndexNow-key voor deze site"}

    host = urlparse(urls[0]).netloc
    body = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls[:100],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(INDEXNOW_ENDPOINT, json=body)
        # 200 = verwerkt, 202 = geaccepteerd; al het andere is een fout.
        ok = resp.status_code in (200, 202)
        if ok:
            return {"status": "ingediend", "status_code": resp.status_code, "urls": len(urls)}
        logger.warning("[indexing] IndexNow gaf %s: %s", resp.status_code, resp.text[:200])
        return await _meld_afwijzing(site, resp.status_code, len(urls))
    except Exception as e:
        logger.warning("[indexing] IndexNow-aanroep mislukt: %s", e)
        return {"status": "fout", "detail": str(e)[:200]}


async def _meld_afwijzing(site: Dict, status_code: int, aantal: int) -> Dict:
    """Een afgewezen IndexNow-submit is een fout die alléén een mens kan
    oplossen — dus hoort hij in het Actiecentrum, niet in een logregel.

    Aanleiding (4 aug 2026): 28 publicaties droegen `indexnow: {status: fout,
    status_code: 403}` in hun `publish_result` en verder nergens. Bij nameting
    bleek het keybestand op **7 van de 10 sites** onbereikbaar (5× een harde
    404, 2× de HTML-schil van de site). Bing, Yandex, Seznam en Naver hebben
    daardoor maandenlang geen enkele URL van ons doorgekregen, terwijl de
    Wachtrij bij elke goedkeuring meldde dat het artikel was aangemeld. Dat is
    het huispatroon: activiteit zonder effect, stil weggeschreven als veld.

    Wachten repareert dit niet — het keybestand komt er niet vanzelf — dus dit
    escaleert meteen in plaats van via een faal-reeks (zie
    `shared/failures.py`: `config` is een mens-alleen klasse). Eén kaart per
    site per dag, want tien artikelen op één site is tien keer hetzelfde
    besluit en het Actiecentrum is een inbox van beslissingen.
    """
    diagnose = await verify_indexnow(site)
    resultaat = {"status": "fout", "status_code": status_code, "urls": aantal,
                 "diagnose": diagnose.get("status"), "detail": diagnose.get("detail", "")}
    try:
        from ...shared.outcomes import log_outcome
        from ...shared.database import get_db

        naam = site.get("name") or "Onbekende site"
        vandaag = _dt.date.today().isoformat()
        with get_db() as db:
            al_gemeld = db.execute(
                "SELECT 1 FROM activity_log WHERE action = 'indexnow_geweigerd' "
                "AND project = ? AND substr(created_at, 1, 10) = ? LIMIT 1",
                (naam, vandaag),
            ).fetchone()
        if al_gemeld:
            return resultaat

        if diagnose.get("status") == "ok":
            # Keybestand staat er wél — dan ligt het aan de submit zelf.
            stap = (f"IndexNow wees de aanmelding af met HTTP {status_code} terwijl het "
                    f"keybestand correct op {diagnose.get('key_url')} staat. Controleer of "
                    "de host in de aanmelding overeenkomt met het domein van het keybestand "
                    "(www vs. niet-www is voor IndexNow een ander domein).")
        else:
            stap = (f"Plaats het IndexNow-keybestand op de site-root: "
                    f"{diagnose.get('key_url') or '(geen base_url bekend)'}. "
                    f"{diagnose.get('detail', '')} Zolang dat bestand ontbreekt negeren "
                    "Bing, Yandex, Seznam en Naver élke aanmelding — nieuwe artikelen "
                    "worden daar alleen nog gevonden als hun crawler toevallig langskomt.")
        log_outcome(
            project=naam,
            action="indexnow_geweigerd",
            detail=(f"IndexNow weigerde {aantal} URL('s) met HTTP {status_code}; "
                    f"diagnose keybestand: {diagnose.get('status')}."),
            artifact=diagnose.get("key_url"),
            next_step=stap,
            status="error",
        )
    except Exception as e:  # melden mag publiceren nooit breken
        logger.warning("[indexing] kon IndexNow-afwijzing niet melden: %s", e)
    return resultaat


async def submit_google_indexing(url: str) -> Dict:
    """Google Indexing API (urlNotifications.publish) — alleen met
    GOOGLE_INDEXING_ENABLED=1. Zie de module-docstring voor de caveats."""
    if not GOOGLE_INDEXING_ENABLED:
        return {"status": "uitgeschakeld", "detail": "zet GOOGLE_INDEXING_ENABLED=1 in .env"}
    try:
        import asyncio

        def _publish() -> Dict:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from ..seo.gsc import _resolve_credentials_path

            creds = service_account.Credentials.from_service_account_file(
                _resolve_credentials_path(),
                scopes=["https://www.googleapis.com/auth/indexing"],
            )
            service = build("indexing", "v3", credentials=creds, cache_discovery=False)
            return service.urlNotifications().publish(
                body={"url": url, "type": "URL_UPDATED"}
            ).execute()

        result = await asyncio.to_thread(_publish)
        return {"status": "ingediend", "notify_time": (result.get("urlNotificationMetadata") or {}).get("latestUpdate", {}).get("notifyTime", "")}
    except Exception as e:
        # Meestal: 403 (service-account is geen Owner) of API niet geactiveerd.
        logger.warning("[indexing] Google Indexing API mislukt voor %s: %s", url, e)
        return {"status": "fout", "detail": str(e)[:250]}
