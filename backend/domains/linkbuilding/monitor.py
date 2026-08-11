"""Link-monitor — bewijst of een afgesproken link er staat (en blijft staan).

Dagelijkse controle in twee richtingen:
1. pending-placements van benaderde prospects: staat de link naar onze pagina
   inmiddels op hun pagina? → placement 'live' (+ echte ankertekst en rel),
   prospect → 'link_live'. Staat hij er op een látere dag nog steeds →
   prospect 'verified' (twee onafhankelijke waarnemingen, geen momentopname).
2. live-placements: verdwijnt de link, dan pas na 2 opeenvolgende missers →
   'lost' + status='error'-kaart in het Actiecentrum (geen vals alarm bij een
   eenmalige timeout of tijdelijke storing).

Beleefd crawlen: één GET per placement per run, nette User-Agent, timeout.
Publiceert/verstuurt zelf niets.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import service
from .service import norm_domain

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "AgentOS-LinkMonitor/1.0 (+https://weareimpact.nl)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}
# Na zoveel vergeefse checks op een pending placement stoppen we met crawlen —
# de afspraak is dan kennelijk niet doorgegaan (prospect blijft gewoon staan).
_MAX_PENDING_FAILS = 45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_link_in_html(html: str, target_url: str) -> Optional[Dict[str, str]]:
    """Zoek in HTML een <a> naar het target-domein. Returns {href, anchor, rel}.

    Een link naar een andere pagina op hetzelfde domein telt óók — beter een
    homepage-link registreren dan een geplaatste link 'niet gevonden' noemen.
    Een exacte URL-match wint van een domein-match."""
    from bs4 import BeautifulSoup

    target_dom = norm_domain(target_url)
    if not target_dom:
        return None
    soup = BeautifulSoup(html or "", "lxml")
    domain_hit: Optional[Dict[str, str]] = None
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        dom = norm_domain(href)
        if dom != target_dom and not dom.endswith("." + target_dom):
            continue
        rel_attr = a.get("rel") or []
        if isinstance(rel_attr, str):
            rel_attr = rel_attr.split()
        hit = {
            "href": href,
            "anchor": a.get_text(" ", strip=True)[:200],
            "rel": " ".join(r for r in rel_attr
                            if r in ("nofollow", "sponsored", "ugc")),
        }
        if href.rstrip("/") == (target_url or "").rstrip("/"):
            return hit
        domain_hit = domain_hit or hit
    return domain_hit


def _fetch(url: str) -> Optional[str]:
    try:
        with httpx.Client(headers=_HEADERS, timeout=12.0,
                          follow_redirects=True, verify=False) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception as e:
        logger.debug("[link-monitor] fetch %s: %s", url, e)
    return None


def _check_one(placement: Dict[str, Any]) -> None:
    now = _now()
    # Eerst de afgesproken pagina, daarna de homepage van het domein: veel
    # linkdeals belanden op een andere pagina dan waarnaar verwezen werd (een
    # "links"-pagina, de homepage, of een gastblog-op de site van de partner).
    # Alleen de domein-homepage bij als fallback — niet de hele site crawlen.
    source = placement["source_url"]
    dom = norm_domain(source)
    candidates = [source]
    if dom:
        home = f"https://{dom}"
        if home.rstrip("/") != source.rstrip("/"):
            candidates.append(home)
    hit = None
    for url in candidates:
        html = _fetch(url)
        if html:
            hit = find_link_in_html(html, placement["target_url"])
            if hit:
                break

    with get_conn() as conn:
        if hit:
            conn.execute(
                "UPDATE link_placements SET status = 'live', anchor_text = ?, "
                "rel = ?, first_seen = CASE WHEN first_seen = '' THEN ? ELSE first_seen END, "
                "last_checked = ?, check_fails = 0, updated_at = ? WHERE id = ?",
                (hit["anchor"], hit["rel"], now, now, now, placement["id"]),
            )
        else:
            new_fails = placement["check_fails"] + 1
            conn.execute(
                "UPDATE link_placements SET last_checked = ?, "
                "check_fails = ?, updated_at = ? WHERE id = ?",
                (now, new_fails, now, placement["id"]),
            )
            # Nadert de grens zonder ooit gevonden te zijn: de afspraak is
            # kennelijk niet doorgegaan. Eén kaart om Vincent dat te laten zien,
            # zodat de wachtrij niet eindeloos op een dode deal blijft wachten.
            if new_fails == _MAX_PENDING_FAILS:
                log_outcome(
                    "Linkbuilding", "link_placement_stalled",
                    f"Backlink-afspraak met {dom} ({source}) is na "
                    f"{_MAX_PENDING_FAILS} checks nog steeds niet live — "
                    f"waarschijnlijk niet doorgegaan.",
                    artifact=source,
                    next_step="Mail de partner na of wijs de linkkans af "
                              "(dan stopt de monitor met crawlen).",
                    status="error",
                )

    was = placement["status"]
    if hit:
        prospect = service.get_prospect(placement["prospect_id"])
        if not prospect:
            return
        if was != "live":
            # Vers gevonden: de mail heeft gewerkt.
            service.advance_prospect(prospect["id"], "link_live")
            rel_note = f" ({hit['rel']})" if hit["rel"] else " (dofollow)"
            log_outcome(
                "Linkbuilding", "link_live",
                f"Link naar {placement['target_url']} staat live op "
                f"{placement['source_url']}{rel_note}, anker: ‘{hit['anchor'][:60]}’",
                artifact=placement["source_url"],
                next_step="Niets — de monitor blijft de link bewaken.",
            )
        elif not prospect.get("verified_at") and (placement.get("first_seen") or "")[:10] < now[:10]:
            # Tweede waarneming op een latere dag: geverifieerd.
            service.advance_prospect(prospect["id"], "verified")
    elif was == "live" and new_fails >= 2:
        with get_conn() as conn:
            conn.execute(
                "UPDATE link_placements SET status = 'lost', updated_at = ? WHERE id = ?",
                (_now(), placement["id"]),
            )
        log_outcome(
            "Linkbuilding", "link_lost",
            f"Backlink op {placement['source_url']} (naar {placement['target_url']}) "
            "is verdwenen na 2 opeenvolgende checks",
            artifact=placement["source_url"],
            next_step="Mail de sitebeheerder of deze weloverwogen is verwijderd — "
                      "soms is het een redesign-ongelukje.",
            status="error",
        )


def check_placements() -> Dict[str, int]:
    """Controleer pending (na verzending) en live placements. Returns telling."""
    with get_conn() as conn:
        pending = [dict(r) for r in conn.execute(
            "SELECT pl.* FROM link_placements pl "
            "JOIN link_prospects p ON p.id = pl.prospect_id "
            "WHERE pl.status = 'pending' AND p.contacted_at != '' "
            "AND pl.check_fails < ?",
            (_MAX_PENDING_FAILS,),
        ).fetchall()]
        live = [dict(r) for r in conn.execute(
            "SELECT * FROM link_placements WHERE status = 'live'"
        ).fetchall()]
    for placement in pending + live:
        _check_one(placement)
    return {"pending_checked": len(pending), "live_checked": len(live)}


def run_link_monitor() -> None:
    """Scheduler entry-point (dagelijks)."""
    try:
        report = check_placements()
        if report["pending_checked"] or report["live_checked"]:
            logger.info("[link-monitor] %d pending en %d live placements gecontroleerd",
                        report["pending_checked"], report["live_checked"])
    except Exception as e:
        logger.exception("Link-monitor gefaald")
        log_outcome(
            "Linkbuilding", "link_monitor",
            f"Link-monitor gefaald: {e}",
            next_step="Bekijk logs/agentos.log en draai handmatig: POST /api/linkbuilding/monitor-run.",
            status="error",
        )
