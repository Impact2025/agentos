"""
Google Search Console — query-niveau zoekprestaties via een service account.

Dit is de databron van de Demand Engine (Goldie's pijler 1): per zoekwoord
klikken, impressies, CTR en — cruciaal — de gemiddelde positie. GA4 geeft dit
niet; GSC wel.
"""
from datetime import date, timedelta
from pathlib import Path
import os
from typing import Dict, List

from ...shared.config import GSC_SERVICE_ACCOUNT_PATH, BASE_DIR

SCOPES = ["https://www.googleapis.com/auth/webmasters"]


def is_configured() -> bool:
    return bool(GSC_SERVICE_ACCOUNT_PATH)


def _resolve_credentials_path() -> str:
    """Maak relatieve paden (bijv. 'google-credentials.json') absoluut t.o.v. de
    projectroot, zodat het serviceaccount ook gevonden wordt los van de CWD."""
    raw = GSC_SERVICE_ACCOUNT_PATH
    # Vervang vertical-tab escapes (0x0B) uit Windows \v-mun paden
    raw = raw.replace("\x0b", "v")
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    resolved = str(p)
    # Als het bestand niet bestaat, fallback naar google-credentials.json op project root
    if not os.path.exists(resolved):
        fallback = str(BASE_DIR / "google-credentials.json")
        if os.path.exists(fallback):
            return fallback
    return resolved


def _get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        _resolve_credentials_path(), scopes=SCOPES,
    )
    # cache_discovery=False voorkomt een file-cache-waarschuwing zonder oauth2client.
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _query(site_url: str, dimensions: List[str], days: int, row_limit: int, end_offset: int = 0) -> List[Dict]:
    """
    GSC query with configurable end offset.

    end_offset=0  : today-28 .. today-2  (standaard, meest recente 'final' data)
    end_offset=28 : today-56 .. today-30 (de periode ervoor — handig voor w-o-w)
    """
    service = _get_service()
    # GSC-data loopt ~2 dagen achter; vraag alleen 'final' data op.
    end = date.today() - timedelta(days=2 + end_offset)
    start = end - timedelta(days=days - 1)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return resp.get("rows", [])


def fetch_query_performance(site_url: str, days: int = 90, row_limit: int = 1000, end_offset: int = 0) -> List[Dict]:
    """Zoekwoordprestaties voor één site over de afgelopen N dagen."""
    rows = _query(site_url, ["query"], days, row_limit, end_offset)
    out: List[Dict] = []
    for r in rows:
        out.append({
            "query": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)) * 100, 2),
            "position": round(float(r.get("position", 0.0)), 1),
        })
    return out


def fetch_page_performance(site_url: str, days: int = 90, row_limit: int = 1000, end_offset: int = 0) -> List[Dict]:
    """Paginaprestaties — gebruikt voor groei-terugkoppeling per URL."""
    rows = _query(site_url, ["page"], days, row_limit, end_offset)
    out: List[Dict] = []
    for r in rows:
        out.append({
            "page": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)) * 100, 2),
            "position": round(float(r.get("position", 0.0)), 1),
        })
    return out


def fetch_page_query_performance(site_url: str, days: int = 28, row_limit: int = 2000,
                                 end_offset: int = 0) -> List[Dict]:
    """Prestaties per pagina+zoekwoord-combinatie — nodig om per pagina het
    belangrijkste zoekwoord te kennen (SEO Optimizer: CTR/refresh-advies)."""
    rows = _query(site_url, ["page", "query"], days, row_limit, end_offset)
    out: List[Dict] = []
    for r in rows:
        out.append({
            "page": r["keys"][0],
            "query": r["keys"][1],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)) * 100, 2),
            "position": round(float(r.get("position", 0.0)), 1),
        })
    return out


def submit_sitemap(site_url: str, sitemap_url: str) -> tuple[bool, str]:
    """Dien een sitemap in bij Google Search Console. Returns (succes, detail)."""
    try:
        service = _get_service()
        service.sitemaps().submit(
            siteUrl=site_url,
            feedpath=sitemap_url,
        ).execute()
        return True, ""
    except Exception as e:
        detail = str(e)
        print(f"[gsc] Sitemap submit failed: {detail}")
        return False, detail


def inspect_url(site_url: str, page_url: str) -> Dict:
    """Vraag de Google URL Inspection API om de échte index-status van één pagina.

    Sluit de indexering-loop: we pingen IndexNow/Google bij publish, maar tot nu
    toe meten we niet of de pagina écht geïndexeerd is. Die terugkoppeling is
    precies Goldie's "24u indexeren"-belofte — zonder meting blijft het een
    schietgebed. Returns een dict met ten minste 'indexed' (bool), 'status'
    (raw) en 'detail' (mensleesbaar / fout).

    Bij een API-fout (quota, rechten) retourneren we een dict met indexed=False
    en de fout in 'detail' — nooit een exception, zodat de scheduler-job niet
    crasht op één mislukte inspectie.
    """
    result: Dict = {"indexed": False, "status": "unknown", "detail": "", "fetched_at": ""}
    try:
        from datetime import datetime
        service = _get_service()
        resp = service.urlInspection().index().inspect(
            body={"inspectionUrl": page_url, "siteUrl": site_url}
        ).execute()
        inspection = (resp.get("inspectionResult") or {})
        index_state = (
            inspection.get("indexStatusResult") or {}
        ).get("coverageState", "UNKNOWN")
        result["status"] = index_state
        # "Indexed" = daadwerkelijk in de index. "Discovered" telt niet als
        # geïndexeerd (Google kent de URL wel maar toont hem niet in zoekresultaten).
        result["indexed"] = index_state == "INDEXED" and not (
            inspection.get("indexStatusResult") or {}
        ).get("pageFetchState") == "PAGE_FETCH_STATE_ERROR"
        result["detail"] = index_state
        result["fetched_at"] = datetime.utcnow().isoformat() + "Z"
    except Exception as e:  # noqa: BLE001
        result["detail"] = f"inspectie mislukt: {str(e)[:200]}"
    return result


def fetch_daily_performance(site_url: str, days: int = 28, end_offset: int = 0) -> List[Dict]:
    """Dagelijkse prestaties (clicks, impressies, CTR, positie) voor trendlijnen."""
    rows = _query(site_url, ["date"], days, row_limit=days, end_offset=end_offset)
    out: List[Dict] = []
    for r in rows:
        out.append({
            "date": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0.0)) * 100, 2),
            "position": round(float(r.get("position", 0.0)), 1),
        })
    return sorted(out, key=lambda x: x["date"])
