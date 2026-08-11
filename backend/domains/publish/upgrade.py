"""
Opschoonronde: bestaande artikelen naar wereldklasse (85+) tillen.

De kwaliteitsgate staat op 80 (`CONTENT_MIN_SCORE`) en dat is een
publicéérbaarheidsgrens: eronder mag niets naar buiten. "Wereldklasse" is
een andere vraag — dat is de lat waar je zélf op wilt staan, niet de lat
waaronder het schadelijk wordt. Die twee door elkaar halen door de globale
gate op 85 te zetten zou élke run strenger maken en de contentmotor laten
vastlopen op stukken die prima zijn; daarom tilt deze module de lat
alléén voor deze aanroep, via `review_and_improve(target_score=...)`.

Twee dingen die deze module anders doet dan een simpele lus:

**1. Eén meting boven 85 is geen bewijs.** De reviewer varieert flink op
identieke invoer — 65 tot 92 op hetzelfde artikel is waargenomen (zie
CLAUDE.md punt 6). `review_and_improve` stopt zodra hij één keer de lat
haalt, en dát is precies het probleem: een lus die afbreekt op de eerste
meting boven de grens selecteert systematisch op mázzel. Over 52
artikelen levert dat een lijst op die op papier volledig 85+ is en in
werkelijkheid rond het gemiddelde blijft hangen — de klassieke
optimizer's curse, en exact het soort "succes gemeld zonder effect" dat
de waarheidsaudit (punt 16) bestaat om te vangen.

Daarom bevestigt deze module: haalt een artikel de lat, dan volgt één
onafhankelijke hermeting, en de **laagste** van de twee wordt opgeslagen.
Twee onafhankelijke metingen boven 85 is bescheiden maar écht bewijs; de
opgeslagen score is dan een ondergrens in plaats van een hoogtepunt.
Zakt de hermeting eronder, dan krijgt het artikel nog één verbeterronde
mét die feedback, en anders wordt de lagere waarheid opgeslagen — niet de
hogere hoop.

**2. De opgeslagen score is een oude meting, geen huidig feit.** Een job
die op 82 staat kan vandaag op 88 of op 74 uitkomen. Vergelijken met
`seo_score` om te beslissen of er werk nodig is klopt dus alleen als
grove voorselectie; het oordeel valt op de verse meting. Dat betekent ook
dat een artikel na deze ronde lager kan staan dan ervoor. Dat is geen
regressie maar een correctie: de 82 was een gok van weken geleden.

Zakt een artikel bij verse meting onder de échte gate (80), dan volgt de
bestaande pijplijn-regel — `needs_work`, met de knoppen "Verbeter met AI"
en "Wijs af" in het Actiecentrum. Het alternatief (de oude score laten
staan omdat hij mooier was) is precies de stille leugen waar dit systeem
op stukloopt: `approve_and_publish` zou dan publiceren op een cijfer dat
niemand meer gemeten heeft.

Deze module publiceert of verstuurt NOOIT. Alles blijft achter de
Wachtrij-gate; het enige dat verandert is de tekst van het artikel en de
gemeten score.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import content_pipeline as cp
from ..seo import sites as sites_service

logger = logging.getLogger(__name__)

# De wereldklasse-lat. Bewust een eigen constante en niet CONTENT_MIN_SCORE+5:
# de twee getallen beantwoorden verschillende vragen (mag dit naar buiten? vs.
# is dit goed genoeg voor ons?) en horen los te kunnen bewegen.
WORLDCLASS_TARGET = 85

# Statussen waarin een artikel nog te verbeteren valt. 'published' staat live —
# dat doorverbeteren vraagt om herpublicatie en is een andere operatie met een
# ander risico. 'rejected' is een menselijk besluit; dat overrulen we niet.
UPGRADABLE_STATUSES = ("pending_review", "needs_work", "stuck")


def _keyword_for(job: Dict) -> str:
    """Het zoekwoord waarop de reviewer beoordeelt.

    Jobs uit de goal-engine hebben een leeg `keyword`-veld (zie CLAUDE.md
    punt 7a). Zonder terugval op de titel beoordeelt de reviewer dan tegen
    een lege string en trekt hij punten af voor 'zoekwoord ontbreekt' —
    een gebrek dat geen herschrijving kan repareren.
    """
    return (job.get("keyword") or "").strip() or (job.get("title") or "").strip()


async def _measure(site: Dict, keyword: str, html: str) -> Optional[Dict]:
    """Eén onafhankelijke hermeting. None als de reviewer niets bruikbaars gaf.

    Bewust geen terugval op score 0 zoals in de verbeter-loop: daar dwingt
    een 0 een herschrijfronde af, hier zou hij een geslaagd artikel
    onterecht afkeuren. Een mislukte bevestiging is 'onbekend', niet 'slecht'.
    """
    try:
        review = await cp._review_article(site, keyword, html)
    except Exception as e:
        logger.warning("[upgrade] Hermeting mislukt: %s", str(e)[:160])
        return None
    if not review or not isinstance(review.get("score"), (int, float)):
        return None
    return review


async def upgrade_job(job_id: str, target: int = WORLDCLASS_TARGET) -> Dict:
    """Til één artikel naar `target`, met bevestigde meting.

    Retourneert een verslag met `before`, `after`, `confirmed` en `reached`.
    `reached` is alleen True als twee onafhankelijke metingen de lat haalden —
    dat is de enige vorm waarin "boven 85" iets betekent.
    """
    job = cp.get_job(job_id)
    if not job:
        raise ValueError("Content-job niet gevonden.")
    site = sites_service.get_site(job.get("site_id")) or {}
    keyword = _keyword_for(job)
    html = (job.get("blog_html") or "").strip()
    before = float(job.get("seo_score") or 0)
    report = {"job_id": job_id, "title": job.get("title") or "", "keyword": keyword,
              "before": before, "after": before, "confirmed": False,
              "reached": False, "changed": False, "status": job.get("status"),
              "note": ""}

    if len(html) < 80:
        report["note"] = "Geen artikeltekst — niets te verbeteren."
        return report
    if not site:
        report["note"] = "Site niet gevonden — reviewer mist het siteprofiel."
        return report

    best_html, best_score, confirmed = html, None, False
    # Twee pogingen: de tweede bestaat alleen om een mislukte bevestiging op te
    # vangen. Meer zou de kosten per artikel onbegrensd maken zonder dat er
    # bewijs is dat een derde ronde nog iets toevoegt.
    for attempt in (1, 2):
        from ...shared.outcomes import llm_budget_exceeded
        if llm_budget_exceeded():
            report["note"] = "LLM-budget/quota op — gestopt, beste versie behouden."
            break
        new_html, review = await cp.review_and_improve(
            site, keyword, best_html, target_score=target, exclude_job_id=job_id)
        score = float(review.get("score") or 0)
        if best_score is None or score >= best_score:
            best_html, best_score = new_html, score
        if score < target:
            # De verbeter-loop heeft zijn rondes opgebruikt en haalt de lat niet.
            # Bevestigen heeft dan geen zin: we weten al dat het niet gelukt is.
            break
        check = await _measure(site, keyword, new_html)
        if check is None:
            # Onbekend is niet geslaagd: zonder tweede meting blijft dit een
            # enkele waarneming, en die claim doen we niet.
            best_html, best_score = new_html, score
            report["note"] = "Bevestigingsmeting mislukt — score is één waarneming."
            break
        confirm_score = float(check.get("score") or 0)
        # De laagste telt. Een gemiddelde zou de mázzel half meenemen; het
        # minimum is de enige waarde waarvan we kunnen zeggen dat het artikel
        # hem in twee onafhankelijke metingen haalde.
        settled = min(score, confirm_score)
        best_html, best_score = new_html, settled
        if settled >= target:
            confirmed = True
            break
        if attempt == 1:
            # Bevestiging viel tegen — nog één ronde, mét de feedback van juist
            # die tegenvallende meting, want dat is de kritiek die de eerste
            # loop niet gezien heeft.
            logger.info("[upgrade] Bevestiging %s < %s voor '%s' — nog één ronde",
                        confirm_score, target, job.get("title", "")[:60])
        else:
            report["note"] = "Lat niet bevestigd na twee rondes — laagste meting opgeslagen."

    if best_score is None:
        return report

    # Wegschrijven. Titel/slug kunnen door de herschrijving veranderd zijn.
    changed = best_html.strip() != html
    title = cp._extract_title(best_html, fallback=job.get("title") or "")
    fields = {"seo_score": int(round(best_score)), "reviewed_at": cp._now()}
    if changed:
        fields.update(blog_html=best_html, title=title, slug=cp.slugify_title(title))

    # Zakt het artikel bij verse meting onder de publiceer-gate, dan volgt de
    # bestaande pijplijn-regel. De oude, hogere score laten staan zou
    # approve_and_publish laten publiceren op een cijfer dat niemand meer meet.
    from ...shared.config import CONTENT_MIN_SCORE
    if job.get("status") == "pending_review" and best_score < CONTENT_MIN_SCORE:
        fields["status"] = "needs_work"
        report["status"] = "needs_work"
    elif job.get("status") in ("needs_work", "stuck") and best_score >= CONTENT_MIN_SCORE:
        fields["status"] = "pending_review"
        report["status"] = "pending_review"

    cp._update_job(job_id, **fields)
    report.update(after=round(best_score, 1), confirmed=confirmed,
                  reached=bool(confirmed and best_score >= target), changed=changed,
                  title=title)
    return report


async def upgrade_batch(target: int = WORLDCLASS_TARGET,
                        site_id: Optional[str] = None,
                        limit: Optional[int] = None,
                        statuses: tuple = UPGRADABLE_STATUSES) -> Dict:
    """Draai de opschoonronde over alles wat de lat nog niet haalt.

    Idempotent en hervatbaar: de selectie kijkt naar de opgeslagen score, en
    elk geslaagd artikel valt bij een volgende run vanzelf buiten de selectie.
    Een afgebroken run (herstart, quota) hoeft dus niet opnieuw vanaf nul —
    wat af is blijft af.
    """
    todo: List[Dict] = []
    for status in statuses:
        for job in cp.list_jobs(site_id=site_id, status=status):
            if float(job.get("seo_score") or 0) < target:
                todo.append(job)
    # Hoogste score eerst: die zitten het dichtst bij de lat en leveren de
    # meeste gehaalde artikelen per bestede token op. Loopt de quota halverwege
    # leeg, dan is er zoveel mogelijk afgemaakt in plaats van half begonnen.
    todo.sort(key=lambda j: -float(j.get("seo_score") or 0))
    if limit:
        todo = todo[:limit]

    results, reached, failed = [], 0, 0
    for i, job in enumerate(todo, 1):
        from ...shared.outcomes import llm_budget_exceeded
        if llm_budget_exceeded():
            logger.warning("[upgrade] Quota/budget op na %s van %s artikelen.", i - 1, len(todo))
            break
        logger.info("[upgrade] %s/%s — '%s' (nu %s)", i, len(todo),
                    (job.get("title") or "")[:70], job.get("seo_score"))
        try:
            rep = await upgrade_job(job["id"], target=target)
        except Exception as e:
            logger.exception("[upgrade] Artikel %s mislukt", job.get("id"))
            failed += 1
            results.append({"job_id": job.get("id"), "title": job.get("title"),
                            "error": str(e)[:200]})
            continue
        results.append(rep)
        if rep.get("reached"):
            reached += 1

    summary = {"target": target, "considered": len(todo), "processed": len(results),
               "reached": reached, "failed": failed, "results": results}

    # Uitkomstkaart: activiteit is geen effect, dus de kaart meldt wat er
    # aantoonbaar gehaald is — niet hoeveel artikelen "behandeld" zijn.
    rest = len(results) - reached - failed
    detail = (f"Opschoonronde naar {target}: {reached} van {len(todo)} artikelen "
              f"bevestigd op {target}+ (twee metingen).")
    if rest:
        detail += f" {rest} bleven eronder."
    if failed:
        detail += f" {failed} liepen vast."
    from ...shared.outcomes import log_outcome
    log_outcome(
        "content", "content-opschoonronde", detail,
        artifact="/wachtrij",
        next_step=("Beoordeel de Wachtrij." if reached else
                   "Bekijk waarom de lat niet gehaald werd."),
        status="ok" if reached or not todo else "error",
    )
    return summary
