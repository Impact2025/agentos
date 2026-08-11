"""
Reparaties op wat al live staat — de andere helft van de waarheidsaudit.

Waarom deze module bestaat (4 aug 2026). Agent OS is opvallend goed in het
vínden van stille storingen: `iris/integrity.py` telt inmiddels tientallen
invarianten, elk met een echt incident eronder. Maar bij een meting over alle
projecten stonden er 82 bevindingen open, waarvan 54 blokkerend of stil, en
leverde `grep` over de hele codebase **nul** reparatiepaden op voor
`metatitel_afgekapt`, `cluster_kannibalisatie` of `afgewezen_maar_live`. Ze
produceerden een kaart en verder niets; zelfherstel raakt ze evenmin aan, want
`waarheidsaudit` staat in `_MENSELIJK_BESLUIT`.

Het gevolg is precies het gevoel dat dit systeem moest wegnemen: elke invariant
werd een to-do voor Vincent in plaats van werk voor een agent. Detectie zonder
remedie is een duurdere manier om een probleem te hebben.

Twee regels die deze module net zo streng maken als de rest:

  1. **Repareren is publiceren.** Elke reparatie hier loopt via dezelfde
     publicatieroute mét dezelfde gates als een gewone goedkeuring. Niet via
     rechtstreekse HTTP — dat is exact hoe `scripts/republish_bewaard_404.py`
     op 23 juli een niet-publiceerbare taaktitel live zette langs elke gate
     heen. Een gate die alleen op de nette route staat, beschermt alleen de
     nette route.
  2. **Een reparatie die niet bewezen is, is geen reparatie.** Na de push wordt
     de live pagina opnieuw opgehaald en getoetst. Klopt hij niet, dan meldt de
     functie dat als mislukt in plaats van de bevinding te sluiten — anders
     vervangen we een zichtbaar probleem door een onzichtbaar.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)


async def repareer_metatitel(job_id: str) -> Dict[str, Any]:
    """Herpubliceer één artikel zodat zijn <title> de correcte meta-titel krijgt.

    De oorzaak is al gerepareerd: `meta_title_for` (2 aug 2026) kapt op een
    woordgrens af in plaats van hard op 60 tekens. Alleen kreeg die fix nooit
    terugwerkende kracht, dus dragen de artikelen die vóór die datum live
    gingen nog steeds hun afgesneden titel — 'Hoe je met impactdata het gesprek
    aa | WeAreImpact'. Google toont die titel zoals hij is, en geen enkele
    herschrijfronde kan er iets aan doen omdat de titel buiten de body valt.

    Herpubliceren is hier voldoende én het juiste middel: de publicatieroute
    berekent `seoTitle` bij elke push opnieuw via `meta_title_for`, dus dezelfde
    body levert vanzelf de goede titel op. We schrijven niets nieuws en laten
    geen model los op de tekst — dat zou een titelfout inruilen voor het risico
    op inhoudsverlies (zie `_looks_like_article`).
    """
    from . import content_pipeline as cp

    with get_conn() as conn:
        job = conn.execute(
            "SELECT id, site_id, title, keyword, slug, seo_score, blog_html, publish_result "
            "FROM content_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            return {"ok": False, "reden": "job-onbekend"}
        site = conn.execute("SELECT * FROM sites WHERE id = ?", (job["site_id"],)).fetchone()
    if not site:
        return {"ok": False, "reden": "site-onbekend"}
    site = dict(site)

    volledig = _volledige_titel(job["blog_html"] or "")
    if not volledig:
        return {"ok": False, "reden": "geen-titel-in-body"}
    correct = cp.meta_title_for(volledig)

    resultaat = await cp._publish_to_project_site(
        site, job["title"] or volledig, job["blog_html"] or "",
        job["keyword"] or "", job["slug"] or "", int(job["seo_score"] or 0),
    )
    if not resultaat.get("success"):
        return {"ok": False, "reden": "publicatie-mislukt",
                "detail": resultaat.get("error", "")}

    # Bewijs, geen aanname: haal de titel op zoals Google hem zou zien.
    url = resultaat.get("url") or ""
    from ..iris.integrity import _live_metatitel, _METATITEL_CACHE

    _METATITEL_CACHE.pop(url, None)  # verse meting, geen antwoord van vóór de fix
    live = _live_metatitel(url)
    if live is None:
        return {"ok": False, "reden": "niet-verifieerbaar", "url": url,
                "detail": "pagina niet op te halen na herpublicatie"}
    kaal = live.split("|")[0].strip() if "|" in live else live
    if kaal != correct and live != correct:
        return {"ok": False, "reden": "titel-onveranderd", "url": url,
                "detail": f"live staat nog {kaal[:70]!r}, verwacht {correct[:70]!r}"}
    return {"ok": True, "url": url, "titel": correct}


async def repareer_alle_metatitels(project: Optional[str] = None,
                                    maximum: int = 25) -> Dict[str, Any]:
    """Repareer elke openstaande `metatitel_afgekapt`-bevinding.

    Werkt op de bevindingen, niet op een eigen zoekopdracht: twee antwoorden op
    de vraag "welke titels zijn stuk?" is precies hoe ze uit elkaar gaan lopen
    (dezelfde afweging als `is_same_topic` in `opportunity_quality`). Sluit een
    bevinding niet zelf — dat doet de audit als hij hem niet meer vindt, want
    een bevinding hoort te sluiten omdat de toets slaagt en niet omdat iemand
    zegt dat het gefikst is.

    `maximum` staat er omdat elke reparatie een deploy is: honderd pagina's in
    één ronde herpubliceren belast de ontvangende site zwaarder dan de winst
    rechtvaardigt, en een fout die zich herhaalt wil je na vijfentwintig keer
    kunnen zien in plaats van na honderd.
    """
    with get_conn() as conn:
        vraag = ("SELECT subject, project FROM integrity_findings "
                 "WHERE invariant = 'metatitel_afgekapt' AND resolved_at IS NULL")
        params: List[Any] = []
        if project:
            vraag += " AND project = ?"
            params.append(project)
        vraag += " ORDER BY first_seen LIMIT ?"
        params.append(maximum)
        rijen = conn.execute(vraag, params).fetchall()

    gelukt: List[str] = []
    mislukt: List[Dict[str, Any]] = []
    for r in rijen:
        job_id = (r["subject"] or "").split(":", 1)[-1]
        if not job_id:
            continue
        try:
            uit = await repareer_metatitel(job_id)
        except Exception as e:  # noqa: BLE001
            uit = {"ok": False, "reden": "uitzondering", "detail": str(e)[:200]}
        if uit.get("ok"):
            gelukt.append(uit.get("url") or job_id)
        else:
            mislukt.append({"job": job_id, "project": r["project"], **uit})
            logger.warning("[repair] Meta-titel %s niet gerepareerd: %s",
                           job_id[:8], uit.get("reden"))

    log_outcome(
        project or "SEO",
        "metatitel_reparatie",
        (f"{len(gelukt)} artikel(en) herpubliceerd met de juiste meta-titel"
         + (f"; {len(mislukt)} mislukt" if mislukt else "")),
        artifact=gelukt[0] if gelukt else None,
        next_step=(
            "Niets — de waarheidsaudit sluit deze bevindingen bij de volgende ronde."
            if not mislukt else
            "Bekijk de mislukte reparaties: staat de publicatie-endpoint van deze "
            "site nog aan, en is de pagina bereikbaar? Een titel die na herpublicatie "
            "onveranderd blijft, wijst op caching aan de kant van de site."
        ),
        # Alleen luid als er níéts lukte: een deelreparatie is vooruitgang en
        # verdient geen rode kaart naast de bevindingen die toch al openstaan.
        status="error" if (mislukt and not gelukt) else "ok",
    )
    return {"gerepareerd": len(gelukt), "mislukt": mislukt}


# Welke invarianten kunnen we écht zelf repareren? Eén register, want er zijn
# twee lezers: het endpoint /api/iris/integrity/repair/{invariant} en de knop
# "Analyseer & fix" in het Actiecentrum. Twee lijstjes zouden uit elkaar lopen,
# en dan belooft de knop een remedie die het endpoint niet kent (of andersom).
REMEDIES = {"metatitel_afgekapt": repareer_alle_metatitels}


def _volledige_titel(html_body: str) -> str:
    """De onafgekapte titel uit de body — dezelfde afleiding als de audit."""
    from ..iris.integrity import _volledige_titel as _uit_audit
    return _uit_audit(html_body)
