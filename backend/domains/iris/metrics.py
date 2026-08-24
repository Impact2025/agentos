"""Iris-metrics — de harde cijfers waarop de manager-agent stuurt.

Alles hier is deterministisch (geen LLM): per project een rapportcijfer
opgebouwd uit vier pijlers, plus de globale funnel- en systeemcijfers.
De LLM-laag (service.py) krijgt deze cijfers als input en mag er een
oordeel over vellen, maar de cijfers zelf zijn altijd reproduceerbaar.

Pijlers per project (samen 100):
- content    (25): draait de contentmotor — live-artikelen laatste 30 dagen
                   t.o.v. het batch-doel, en blijft de Wachtrij niet liggen.
- seo        (35): meetbare vindbaarheid — GSC-clicks/positie/CTR van de
                   gepubliceerde pagina's. Geen GSC-data = laag, met reden.
- uitvoering (20): doelen afgerond vs. mislukt in de laatste 30 dagen.
- hygiene    (20): fouten in de uitkomst-feed (7 dagen) en needs_work-jobs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...shared.database import get_conn


def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def _site_projects(conn) -> List[Dict[str, Any]]:
    # Testsites tellen niet mee: anders wordt "til TestSite omhoog" het
    # topadvies terwijl het geen echt project is.
    return [dict(r) for r in conn.execute(
        "SELECT id, name, base_url, gsc_property, auto_content_enabled, "
        "content_batch_size FROM sites WHERE COALESCE(is_test, 0) = 0"
    ).fetchall()]


def _content_pillar(conn, site_id: str, batch_size: int) -> Dict[str, Any]:
    live_30d = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'published' "
        "AND created_at > datetime('now', '-30 days')", (site_id,)
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'pending_review'",
        (site_id,)
    ).fetchone()[0]
    stale = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'pending_review' "
        "AND created_at < datetime('now', '-3 days')", (site_id,)
    ).fetchone()[0]
    needs_work = conn.execute(
        "SELECT COUNT(*) FROM content_jobs WHERE site_id = ? AND status = 'needs_work'",
        (site_id,)
    ).fetchone()[0]
    # Doel: 2 runs/week × batch_size ≈ 8×batch per maand; 100% = alles gehaald.
    target_30d = max(1, (batch_size or 1) * 8)
    score = _clamp(live_30d / target_30d * 25, 0, 25)
    # Wachtrij die blijft liggen kost punten: de motor draait dan voor niets.
    score = _clamp(score - stale * 2, 0, 25)
    return {
        "score": round(score, 1),
        "live_30d": live_30d,
        "target_30d": target_30d,
        "pending_review": pending,
        "stale_review": stale,
        "needs_work": needs_work,
    }


def _weak_page_count(conn, site_id: str) -> int:
    """Aantal pagina's op een onvindbare positie (gem. positie > 20) in de
    nieuwste GSC-pagina-snapshot. Voorkomt dat een paar toppers een zee van
    slecht geplaatste pagina's verbloemt in de SEO-pijler."""
    day = conn.execute(
        "SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page'",
        (site_id,),
    ).fetchone()[0]
    if not day:
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM gsc_history "
        "WHERE site_id = ? AND scope = 'page' AND date = ? AND position > 20",
        (site_id, day),
    ).fetchone()[0]


def _seo_pillar(conn, site_id: str, gsc_configured: bool) -> Dict[str, Any]:
    # Meetbron: de nieuwste per-pagina GSC-snapshot uit gsc_history. Sites die
    # buiten Impact OS om gehost worden krijgen nooit rijen in published_pages
    # (dat vullen alleen Netlify-publicaties), dus wie dáárop meet ziet overal
    # "0 pagina's" terwijl GSC gewoon clicks rapporteert — en dan is 35 van de
    # 100 punten dood gewicht. published_pages blijft de terugval voor sites
    # zonder GSC-historie.
    row = conn.execute(
        "SELECT COUNT(*) AS pages, "
        "SUM(clicks) AS clicks, SUM(impressions) AS impressions, "
        "SUM(CASE WHEN clicks > 0 THEN 1 ELSE 0 END) AS pages_with_clicks, "
        "AVG(CASE WHEN position > 0 THEN position END) AS avg_position "
        "FROM gsc_history WHERE site_id = ? AND scope = 'page' AND date = "
        "(SELECT MAX(date) FROM gsc_history WHERE site_id = ? AND scope = 'page')",
        (site_id, site_id)
    ).fetchone()
    if not (row["pages"] or 0):
        row = conn.execute(
            "SELECT COUNT(*) AS pages, "
            "SUM(gsc_clicks) AS clicks, SUM(gsc_impressions) AS impressions, "
            "SUM(CASE WHEN gsc_clicks > 0 THEN 1 ELSE 0 END) AS pages_with_clicks, "
            "AVG(CASE WHEN gsc_position > 0 THEN gsc_position END) AS avg_position "
            "FROM published_pages WHERE site_id = ?", (site_id,)
        ).fetchone()
    pages = row["pages"] or 0
    clicks = row["clicks"] or 0
    impressions = row["impressions"] or 0
    pages_with_clicks = row["pages_with_clicks"] or 0
    avg_position = round(row["avg_position"], 1) if row["avg_position"] else None

    if not gsc_configured:
        # Zonder meetdata kan SEO nooit 'wereldklasse' heten. We geven géén
        # nep-score: de pijler is 0 en de melding maakt expliciet dat dit een
        # hiaat is, geen prestatie. (Vóór 12 aug 2026 kreeg zo'n site
        # stilzwijgend max 10/35 — waardoor 'niet gemeten' eruitzag als
        # 'redelijk vindbaar'.)
        score, note = 0.0, "geen GSC-koppeling — vindbaarheid is niet meetbaar"
    elif pages == 0:
        score, note = 0.0, "nog geen gepubliceerde pagina's"
    else:
        # 15 punten: aandeel pagina's dat daadwerkelijk clicks krijgt.
        score = pages_with_clicks / pages * 15
        # 10 punten: absolute clicks (100+/30d = vol).
        score += _clamp(clicks / 100 * 10, 0, 10)
        # 10 punten: gemiddelde positie, maar mét een 'zwakke-pagina'-correctie.
        # Een handvol toppagina's mag niet een zee van pagina's op pos >20
        # verbloemen: als een groot deel van de pagina's slecht staat, trekt
        # dat de positie-subscore eerlijk omlaag (zodat TeambuildingMetImpact
        # niet '6.6 groen' scoort terwijl 10/16 pagina's op pos >20 staan).
        weak = _weak_page_count(conn, site_id)
        if avg_position:
            pos_score = _clamp((30 - avg_position) / 29 * 10, 0, 10)
            if pages:
                weak_share = weak / pages
                pos_score *= (1 - 0.5 * weak_share)  # max -50% bij 100% zwak
            score += pos_score
        note = ""
        score = _clamp(score, 0, 35)

    ctr = round(clicks / impressions * 100, 2) if impressions else None
    open_suggestions = conn.execute(
        "SELECT COUNT(*) FROM seo_suggestions WHERE site_id = ? AND status = 'new'",
        (site_id,)
    ).fetchone()[0]
    # Backlinks als context (geen scorepunten: de pijler blijft puur GSC-meting;
    # links zijn de hefboom, clicks/positie zijn het bewijs). live = door de
    # link-monitor waargenomen, dofollow = zonder nofollow/sponsored-rel.
    bl = conn.execute(
        "SELECT COUNT(*) AS live, "
        "SUM(CASE WHEN rel = '' THEN 1 ELSE 0 END) AS dofollow "
        "FROM link_placements WHERE site_id = ? AND status = 'live'", (site_id,)
    ).fetchone()
    return {
        "score": round(score, 1),
        "pages": pages,
        "clicks": clicks,
        "impressions": impressions,
        "pages_with_clicks": pages_with_clicks,
        "avg_position": avg_position,
        "ctr_pct": ctr,
        "open_suggestions": open_suggestions,
        "backlinks_live": bl["live"] or 0,
        "backlinks_dofollow": bl["dofollow"] or 0,
        "note": note,
    }


def _execution_pillar(conn, project_names: List[str]) -> Dict[str, Any]:
    """Doelen-pijler: hoeveel werk komt daadwerkelijk rond.

    Belangrijke correctie (2026-08-12): de goals-tabel kent geen 'failed'-
    status — alleen completed / partial / paused. De oude code deed
    `finished = completed + failed`, maar `failed` is altijd 0, dus
    `finished == completed` en `completed/finished == 100%` zodra er ook maar
    één doel 'completed' is. Gevolg: de pijler was binair (20 of 5) en
    'werk loopt maar komt niet af' (partial) was onzichtbaar. Nu wegen we op
    alle actieve statussen en straffen we stilstand (>30d niets geüpdatet).
    """
    from datetime import datetime as _dt
    ph = ",".join("?" for _ in project_names) or "''"
    # Actuele venster (laatste 30d) — voor de 'recent afgerond'-signaal.
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM goals "
        f"WHERE lower(project) IN ({ph}) AND updated_at > datetime('now', '-30 days') "
        f"GROUP BY status",
        [p.lower() for p in project_names],
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in rows}
    completed = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    running = by_status.get("running", 0)
    partial = by_status.get("partial", 0)

    # Volledige teller over alle tijden — een doel dat jaren geleden klaar is
    # mag niet eeuwig een 20 opleveren als er nu niets meer gebeurt.
    all_rows = conn.execute(
        f"SELECT status, COUNT(*) AS n, MAX(updated_at) AS last_up "
        f"FROM goals WHERE lower(project) IN ({ph}) GROUP BY status",
        [p.lower() for p in project_names],
    ).fetchall()
    all_by = {r["status"]: r["n"] for r in all_rows}
    last_up = max([r["last_up"] for r in all_rows if r["last_up"]] or [None])

    active_total = (all_by.get("completed", 0) + all_by.get("partial", 0)
                    + all_by.get("paused", 0) + all_by.get("running", 0))
    if active_total == 0:
        # Écht geen doelen: geen halve punten, dit is een leeg project.
        score = 0.0
    else:
        # Afgerond ten opzichte van alles wat ooit actief was (incl. partial:
        # werk dat begonnen is telt mee, maar voltooiing telt zwaarder).
        base = (all_by.get("completed", 0) / active_total) * 20
        # Stilstand-straf: het meest recente doel >30d geleden geüpdatet =
        # de motor draait niet. Lineair weg vanaf 30 dagen tot 0 op 60 dagen.
        if last_up:
            try:
                lu = _dt.strptime(last_up[:19], "%Y-%m-%d %H:%M:%S")
                age = (_dt.now() - lu).days
                if age > 30:
                    penalty = _clamp((age - 30) / 30, 0, 1)  # 0 op 30d → 1 op 60d
                    base *= (1 - penalty)
            except Exception:
                pass
        score = _clamp(base, 0, 20)
    return {
        "score": round(score, 1),
        "completed_30d": completed,
        "failed_30d": failed,
        "partial_30d": partial,
        "running": running,
        "by_status": by_status,
        "active_total": active_total,
        "last_updated": last_up,
    }


import re as _re


def _error_resolved(conn, row: Dict[str, Any]) -> bool:
    """Bepaal of een fout uit activity_log inmiddels is opgelost.

    Regels (bewust conservatief — bij twijfel telt de fout gewoon mee):
    1. 'iris_actie'-fouten zijn diagnoses/meta over een onderliggende fout,
       geen nieuwe fout: nooit meetellen (de onderliggende fout telt al).
    2. Fouten die een artikeltitel citeren ('...' in detail) zijn opgelost
       zodra diezelfde job in content_jobs op status 'published' staat.
    3. Fouten zijn opgelost als er in hetzelfde project een LATERE ok-regel
       staat met dezelfde geciteerde titel (bv. publicatie_mislukt om 04:30,
       'LIVE op ...' om 05:09).
    4. 'llm-budget-op' van vóór vandaag: budget reset om middernacht.
    """
    action = (row.get("action") or "")
    detail = row.get("detail") or ""
    created = row.get("created_at") or ""
    if action == "iris_actie":
        return True
    if action == "llm-budget-op":
        today = conn.execute("SELECT date('now')").fetchone()[0]
        return created[:10] < today
    # Linkbuilding: een storing in de zoeklaag (Tavily-quota + DDG-rate-limit)
    # gooide per-site 'search failed'-kaarten op. Zodra er ná zo'n fout een
    # geslaagde prospectie of weekrun is gelogd, is de oorzaak weg en mogen de
    # oude kaarten verdwijnen — anders blijven ze eeuwig in het Actiecentrum
    # staan terwijl de zoekmachine allang weer werkt.
    if action in ("linkbuilding_prospectie", "linkbuilding_weekrun"):
        hit = conn.execute(
            "SELECT 1 FROM activity_log WHERE status='ok' AND action IN "
            "('linkbuilding_prospectie','linkbuilding_weekrun') AND created_at > ? "
            "LIMIT 1",
            (created,),
        ).fetchone()
        return bool(hit)
    # Stilstand: de kaart is opgelost zodra de taak weer geslaagd is. De
    # levensloop staat in `scheduler_gaps` respectievelijk `scheduler_runs`,
    # niet in de kaarttekst — dus vraag het daar. Zonder deze regel blijft een
    # gemiste run Iris' hygiëne-pijler drukken nadat het werk allang is
    # ingehaald, en dat is precies de ruis die stilstand-melden moest oplossen.
    if action == "gemiste_runs":
        # Deze kaarten worden niet meer gemaakt: het Actiecentrum rendert de
        # stilstand rechtstreeks uit `scheduler_gaps`, inclusief de inhaalknop
        # (zie shared/downtime.py, 2 aug 2026). De rijen die er nog liggen zijn
        # dus per definitie een dubbeling van een kaart die er al staat — niet
        # "onopgelost", maar overbodig. Ze blijven in het logboek als historie
        # en verdwijnen uit de inbox en uit de hygiëne-pijler.
        return True
    if action == "job_nooit_geslaagd":
        job_id = detail.split("|")[0].strip()
        if not job_id:
            return False
        rij = conn.execute(
            "SELECT last_run_at, last_ok_at FROM scheduler_runs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not rij:
            return False
        # Twee manieren waarop deze kaart klaar is. De eerste is de bedoelde: de
        # taak is inmiddels geslaagd. De tweede is dat de bewering zelf niet
        # klopt — de taak heeft nog nóóit uitgevoerd, en dan is "is nog nooit
        # geslaagd" geen defect maar een taak die nog niet aan de beurt is
        # geweest. Tot 2 aug 2026 zette een misfire `last_run_at`, waardoor elke
        # taak die tijdens een uitgezette machine overging als defect werd
        # gemeld; die kaarten horen te sluiten, niet te blijven staan naast de
        # stilstand-kaart die het wél goed vertelt.
        return bool(rij["last_ok_at"]) or not rij["last_run_at"]

    # 'gauntlet_zonder_project' — de auto-queue-route kon een run niet aan een
    # site koppelen (oude runs vóór 19 aug 2026 droegen de projectnaam niet in
    # de vereiste "project 'X'"-vorm in de benchmark). Maar dezelfde run werd
    # wél via de Orchestrator-route gepubliceerd naar een bestaande content_job
    # (die haalt het project uit de objective). De kaart is dus een
    # false-positive zodra de genoemde run een published_job_id heeft dat naar
    # een bestaande content_jobs-rij wijst — het werk is niet weg, het stond
    # alleen in een andere queue. Anders blijft de kaart eeuwig staan terwijl
    # de content gewoon in de Wachtrij ligt.
    if action == "gauntlet_zonder_project":
        m_run = _re.search(r"Gauntlet-run\s+([^\s]+)\s+haalde", detail)
        if m_run:
            run_id = m_run.group(1)
            r = conn.execute(
                "SELECT published_job_id FROM gauntlet_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if r and r["published_job_id"]:
                job = conn.execute(
                    "SELECT 1 FROM content_jobs WHERE id = ?",
                    (r["published_job_id"],),
                ).fetchone()
                if job:
                    return True
        return False

    # 'remote_decision_failed' door een geweigerde dubbele calendar_add — de
    # bridge-guard blokkeerde terecht een tweede voorstel voor hetzelfde moment
    # (het origineel wachtte nog op goedkeuring). De kaart is opgelost zodra
    # het geciteerde proposal niet meer in 'pending_review' staat: Vincent heeft
    # het inmiddels geboekt of afgewezen, dus de "dubbele boeking" kan niet meer
    # gebeuren. Anders blijft een achterhaalde melding over een al genomen
    # beslissing op het scherm staan.
    if action == "remote_decision_failed" and "bestaat al" in detail:
        m_prop = _re.search(r"#(\d+)\s+'([^']+)'", detail)
        if m_prop:
            prop_id = m_prop.group(1)
            p = conn.execute(
                "SELECT status FROM calendar_proposals WHERE id = ?",
                (prop_id,),
            ).fetchone()
            if p and p["status"] != "pending_review":
                return True
        return False

    m = _re.search(r"'([^']{8,})'", detail)
    if not m:
        return False
    title = m.group(1)
    # 2) job inmiddels gepubliceerd?
    hit = conn.execute(
        "SELECT 1 FROM content_jobs WHERE status = 'published' AND title = ? LIMIT 1",
        (title,),
    ).fetchone()
    if hit:
        return True
    # 3) latere ok-activiteit met dezelfde titel in hetzelfde project?
    hit = conn.execute(
        "SELECT 1 FROM activity_log WHERE status = 'ok' AND lower(project) = lower(?) "
        "AND created_at > ? AND detail LIKE ? LIMIT 1",
        (row.get("project") or "", created, f"%'{title}'%"),
    ).fetchone()
    return bool(hit)


def unresolved_errors(conn, project_names: Optional[List[str]] = None,
                      days: int = 7) -> List[Dict[str, Any]]:
    """Fouten uit activity_log (laatste `days` dagen) die nog NIET zijn
    opgelost. Dit is de enige fouten-lijst die scores en briefings mogen
    gebruiken: alleen meldingen waar nog echt iets voor moet gebeuren."""
    if project_names is not None:
        ph = ",".join("?" for _ in project_names) or "''"
        rows = conn.execute(
            f"SELECT project, action, detail, status, created_at FROM activity_log "
            f"WHERE status = 'error' AND lower(project) IN ({ph}) "
            f"AND created_at > datetime('now', ?) ORDER BY created_at DESC",
            [p.lower() for p in project_names] + [f"-{days} days"],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT project, action, detail, status, created_at FROM activity_log "
            "WHERE status = 'error' AND created_at > datetime('now', ?) "
            "ORDER BY created_at DESC",
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows if not _error_resolved(conn, dict(r))]


def _hygiene_pillar(conn, project_names: List[str], needs_work: int) -> Dict[str, Any]:
    errors_7d = len(unresolved_errors(conn, project_names))
    score = _clamp(20 - errors_7d * 3 - needs_work * 2, 0, 20)
    return {"score": round(score, 1), "errors_7d": errors_7d, "needs_work": needs_work}


def _trend_block(site_id: str) -> Optional[Dict[str, Any]]:
    """Week-over-week-delta's uit de GSC-historie; None zolang er geen reeks is."""
    from ..seo import history as history_service
    trend = history_service.site_trend(site_id)
    if trend is None:
        return None
    movers = history_service.page_movers(site_id, limit=3)
    return {
        "site": trend,
        "risers": [{"url": m["page_url"], "query": m["top_query"],
                    "delta_clicks": m["delta_clicks"], "delta_position": m["delta_position"]}
                   for m in movers.get("risers", [])],
        "fallers": [{"url": m["page_url"], "query": m["top_query"],
                     "delta_clicks": m["delta_clicks"], "delta_position": m["delta_position"]}
                    for m in movers.get("fallers", [])],
    }


def _geo_pillar(site_id: str) -> Dict[str, Any]:
    """Generative Engine Optimization-pijler voor Iris' briefing.

    Leest de laatste GEO-scan (gedraaid via /api/geo/scan/{site_id}). Geeft een
    compact blok terug met score + de 5 sub-pijlers, plus een 0-gauge als er nog
    niet gescand is (zodat Iris weet dat het een hiaat is, geen prestatie).
    """
    try:
        from ..geo import service as geo_service
        scan = geo_service.get_latest_scan(site_id)
    except Exception:
        return {"score": None, "scanned": False, "pillars": {}, "recommendations": []}
    if not scan:
        return {"score": None, "scanned": False, "pillars": {}, "recommendations": []}
    try:
        pillars = json.loads(scan.get("pillars") or "{}")
    except Exception:
        pillars = {}
    try:
        recs = json.loads(scan.get("recommendations") or "[]")
    except Exception:
        recs = []
    return {
        "score": scan.get("score"),
        "scanned": True,
        "scanned_at": scan.get("scanned_at"),
        "pillars": pillars,
        "recommendations": recs,
    }


def project_scores() -> List[Dict[str, Any]]:
    """Rapportcijfer per project (site), opgebouwd uit de vier pijlers."""
    out: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for site in _site_projects(conn):
            names = [site["id"], site["name"]]
            content = _content_pillar(conn, site["id"], site["content_batch_size"] or 1)
            seo = _seo_pillar(conn, site["id"], bool(site["gsc_property"]))
            execution = _execution_pillar(conn, names)
            hygiene = _hygiene_pillar(conn, names, content["needs_work"])
            total = round(content["score"] + seo["score"] + execution["score"] + hygiene["score"], 1)
            # GEO-pijler (Generative Engine Optimization) — niet meegeteld in de
            # bestaande 0-100 totaalscore (die blijft content/seo/uitvoering/
            # hygiene), maar WEL beschikbaar als 5e inzicht voor Iris' briefing
            # en de GEO-tab. Voorkomt regressie in bestaande rapportcijfers.
            geo = _geo_pillar(site["id"])
            out.append({
                "project": site["name"],
                "site_id": site["id"],
                "score": total,
                "grade": round(total / 10, 1),  # rapportcijfer 0-10
                "auto_content": bool(site["auto_content_enabled"]),
                "pillars": {
                    "content": content,
                    "seo": seo,
                    "uitvoering": execution,
                    "hygiene": hygiene,
                    "geo": geo,
                },
            })
    # Trend-delta's per site apart ophalen (eigen read-connecties, buiten de
    # bovenstaande lus zodat we niet twee schrijf-connecties genest aanhouden).
    for p in out:
        p["trend"] = _trend_block(p["site_id"])
    out.sort(key=lambda p: p["score"])
    return out


def global_metrics() -> Dict[str, Any]:
    """Project-overstijgende cijfers: funnel, fouten, scheduler, wachtrij."""
    from ..prospecting import funnel as funnel_mod
    with get_conn() as conn:
        errors_24h = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE status = 'error' "
            "AND created_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        delivered_24h = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE status = 'ok' "
            "AND action IN ('task_done','goal_done','live','publicatie','wachtrij_staged') "
            "AND created_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        pending_review_total = conn.execute(
            "SELECT COUNT(*) FROM content_jobs WHERE status = 'pending_review'"
        ).fetchone()[0]

    scheduler_failures: List[Dict[str, Any]] = []
    try:
        from ...scheduler import get_scheduler_status
        for job in get_scheduler_status().get("jobs", []):
            last = job.get("last_run") or {}
            if last.get("status") == "error":
                scheduler_failures.append({"job": job["label"], "error": last.get("error", "")})
    except Exception:
        pass

    try:
        funnel = funnel_mod.funnel_stats()
        inputs = funnel_mod.input_stats(days=7)
    except Exception:
        funnel, inputs = {}, {}

    # 'Voorraad' moet betekenen: leads waar de outreach-batch vandaag een concept
    # voor kan schrijven. Op `new + enriched` tellen las 47 waar er 7 mailbaar
    # waren — generieke info@-adressen worden door de outreach-zeef geweigerd —
    # en dat verschil bepaalt welke knop eronder komt (zoeken vs. klaarzetten).
    # Dezelfde functie als de batch zelf gebruikt: twee antwoorden op dezelfde
    # vraag is precies hoe de funnel weken droog kon staan bij een volle voorraad.
    if funnel:
        try:
            from ..prospecting.outreach import count_mailable_leads
            funnel["mailable"] = count_mailable_leads()
        except Exception:  # noqa: BLE001 — een kapotte zeef velt de briefing niet
            logger.exception("[iris] kon mailbare voorraad niet tellen")

    try:
        from ..linkbuilding import service as lb_service
        linkbuilding = lb_service.funnel_stats()
    except Exception:
        linkbuilding = {}

    # Stilstand: geplande runs die overgingen terwijl de machine uit stond.
    # Dit hoort in het cijferbeeld omdat het de vérklaring is onder andere
    # cijfers: een droge funnel na vier dagen zonder outreach-batch is geen
    # acquisitieprobleem maar een uptime-probleem, en dan is "zet meer
    # concepten klaar" het behandelen van een symptoom.
    try:
        from ...shared import downtime
        gaps = downtime.summary()
    except Exception:
        gaps = []

    # Het trage zoekbeeld: 28 dagen tegen de 28 daarvóór, per project, uit het
    # weekrapport. De projectcijfers hierboven dragen het snelle beeld (7 vs. 7
    # uit `gsc_history`); pas met beide horizonnen naast elkaar is te zien of een
    # daling ruis is of een lijn. Vóór 4 aug 2026 bestond dit rapport alleen als
    # mail — Iris stuurde dus wekelijks op de ruwste van de twee horizonnen.
    try:
        from ..analytics import insights
        weekrapport = insights.summary()
    except Exception:
        logger.exception("[iris] weekrapport-samenvatting mislukt")
        weekrapport = {"state": "geen", "projects": [], "structureel_dalend": []}

    return {
        "errors_24h": errors_24h,
        "delivered_24h": delivered_24h,
        "pending_review_total": pending_review_total,
        "scheduler_failures": scheduler_failures,
        "downtime_gaps": gaps,
        "weekrapport": weekrapport,
        "funnel": funnel,
        "inputs_7d": inputs,
        "linkbuilding": linkbuilding,
    }


def bottlenecks(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministische knelpunt-detectie, gerangschikt op bedrijfsimpact.

    Dit is de manager-logica die ook zonder LLM moet werken: het laagste
    rapportcijfer is zelden het echte probleem — een droge funnel of een
    onmeetbaar project wel. Elk knelpunt heeft een advies voor Vincent en,
    waar een agent het kan oppakken, een kant-en-klaar actie-voorstel
    (zelfde vorm als de LLM's `actie_voorstellen`, dus direct bruikbaar
    voor de "Wil je dat ik dit fix?"-knoppen).
    """
    projects = snap.get("projects") or []
    glob = snap.get("global") or {}
    funnel = glob.get("funnel") or {}
    inputs = glob.get("inputs_7d") or {}
    out: List[Dict[str, Any]] = []
    prio = 1

    # 0. Stilstand gaat vóór alles. Een agent die niet gedraaid heeft is geen
    # slecht presterende agent — hij heeft niet bestaan die dag. Zolang dat
    # niet bovenaan staat, verklaart Iris de gevolgen (droge funnel, lege
    # Wachtrij) als inhoudelijke problemen en stuurt ze agents bij die niets
    # verkeerd deden. Vier werkdagen zonder outreach-batch, 28-31 jul 2026.
    gaps = glob.get("downtime_gaps") or []
    inhaalbaar = [g for g in gaps if g.get("recoverable")]
    if inhaalbaar:
        ernstigste = max(inhaalbaar, key=lambda g: g.get("missed", 0))
        out.append({
            "prio": prio,
            "issue": "stilstand",
            "actie": ("Haal gemiste geplande taken in — "
                      + "; ".join(f"{g['label']} ({g['missed']}×)" for g in inhaalbaar[:3])),
            "waarom": (ernstigste.get("detail") or "")
                      + " — dit werk gebeurt niet vanzelf alsnog",
            "suggestion": {
                "type": "run_job", "scope": "all", "target": ernstigste["job_id"],
                "title": f"Draai '{ernstigste['label']}' alsnog",
                "detail": ernstigste.get("detail") or "",
                "priority": prio, "payload": {"job_id": ernstigste["job_id"]},
            },
        })
        prio += 1

    # 1. Acquisitie-funnel droog: input is de enige knop waar sales op draait.
    target = inputs.get("outreach_target") or 0
    sent = inputs.get("outreach_sent") or 0
    ready = inputs.get("outreach_drafts_ready") or 0
    by_status = funnel.get("by_status") or {}
    ruwe_voorraad = (by_status.get("new") or 0) + (by_status.get("enriched") or 0)
    # `mailable` telt alleen de leads waar de outreach-batch écht een concept voor
    # kan schrijven (zie global_metrics). Ontbreekt het veld — oudere snapshot,
    # zeef onbereikbaar — dan is de ruwe voorraad de eerlijkste schatting die er is.
    stock = funnel.get("mailable")
    if stock is None:
        stock = ruwe_voorraad
    if target and (sent + ready) < target * 0.5:
        # Draaide de batch überhaupt? Zo niet, dan is de funnel niet droog maar
        # ongevuld, en dat is een ander probleem met een andere oplossing.
        batch_gap = next((g for g in gaps if g["job_id"] == "daily_outreach_batch"), None)
        item: Dict[str, Any] = {
            "prio": prio,
            "issue": "funnel_droog",
            "actie": f"Vul de acquisitie-funnel — {sent} verstuurd + {ready} klaar "
                     f"tegen een weekdoel van {target}",
            "waarom": f"outreach 7d: {sent} verstuurd, {ready} concepten klaar, "
                      f"{stock} mailbare lead(s) op voorraad"
                      + (f" (van {ruwe_voorraad} in totaal — de rest heeft geen "
                         "bruikbaar adres)" if ruwe_voorraad > stock else "")
                      + (f" — LET OP: de outreach-batch draaide {batch_gap['missed']}× niet, "
                         "dus dit is stilstand en geen acquisitieprobleem"
                         if batch_gap else ""),
        }
        if stock:
            item["suggestion"] = {
                "type": "outreach_run", "scope": "all", "target": "all",
                "title": f"Zet {min(stock, 10)} outreach-concepten klaar",
                "detail": f"Weekdoel {target}, pas {sent} verstuurd; {stock} lead(s) "
                          "op voorraad. Concepten landen als outreach_review — "
                          "versturen blijft jouw klik.",
                "priority": prio, "payload": {"aantal": min(stock, 10)},
            }
        else:
            item["actie"] = ("Vul de funnel-voorraad — 0 bruikbare leads, outreach "
                             "heeft niets om mee te werken")
            item["suggestion"] = {
                "type": "lead_search_run", "scope": "all", "target": "all",
                "title": "Draai een lead-zoekactie (5 zoekopdrachten)",
                "detail": f"Funnel-voorraad is 0 bij een weekdoel van {target}. De "
                          "agent zoekt, verrijkt en bewaart nieuwe leads (status "
                          "new) — mailen blijft achter de review-gate.",
                "priority": prio, "payload": {"template": "weareimpact_ai"},
            }
        out.append(item)
        prio += 1

    # 1b. Linkkansen die blijven liggen: gekwalificeerde prospects zonder
    # concept zijn onbenutte SEO-hefboom (backlinks → positie → clicks).
    lb = glob.get("linkbuilding") or {}
    lb_status = lb.get("by_status") or {}
    lb_qualified = lb_status.get("qualified") or 0
    lb_review = lb_status.get("outreach_review") or 0
    if lb_qualified >= 3 and not lb_review:
        out.append({
            "prio": prio,
            "issue": "linkkansen_liggen",
            "actie": f"Zet link-outreach klaar — {lb_qualified} gekwalificeerde "
                     "linkkans(en) wachten zonder concept",
            "waarom": "backlinks zijn de goedkoopste positie-hefboom; kansen die "
                      "liggen verjaren",
            "suggestion": {
                "type": "linkbuilding_run", "scope": "all", "target": "all",
                "title": f"Zet {min(lb_qualified, 10)} link-outreach-concepten klaar",
                "detail": f"{lb_qualified} gekwalificeerde linkkans(en) op voorraad. "
                          "Concepten landen als outreach_review — versturen blijft "
                          "jouw klik.",
                "priority": prio, "payload": {"aantal": min(lb_qualified, 10)},
            },
        })
        prio += 1

    # 1c. Structurele daling uit het weekrapport (28 dagen vs. de 28 daarvóór).
    # Dit staat bewust náást de 7-vs-7-trend per project en niet in plaats
    # daarvan: één week omlaag is ruis, vier weken omlaag is een lijn. Zolang
    # alleen de snelle horizon meetelde, reageerde Iris op ruis en zag ze de
    # lijn niet — het weekrapport dat dit al berekende ging alleen naar de mail.
    week = glob.get("weekrapport") or {}
    dalend = week.get("structureel_dalend") or []
    if dalend:
        details = {p["project"]: p for p in (week.get("projects") or [])}
        eerste = details.get(dalend[0], {})
        item = {
            "prio": prio, "issue": "structurele_daling",
            "actie": f"Herstel de wegzakkende zichtbaarheid van {', '.join(dalend[:3])}",
            "waarom": (f"weekrapport {week.get('week')}: over 28 dagen zowel minder "
                       f"klikken als een slechtere positie — dit is geen weekruis "
                       f"maar een lijn"
                       + (f" ({eerste.get('clicks_pct')}% klikken, positie "
                          f"{eerste.get('position_delta')})" if eerste else "")),
        }
        # Meer produceren bij een verstopte Wachtrij maakt het probleem groter;
        # dan blijft de diagnose staan zonder knop (zie het doorvoer-knelpunt).
        if eerste.get("site_id") and (glob.get("pending_review_total") or 0) < 20:
            item["suggestion"] = {
                "type": "seo_refresh", "scope": eerste.get("project") or "all",
                "target": eerste["site_id"],
                "title": f"Ververs de wegzakkende pagina's van {eerste.get('project')}",
                "detail": ("Structurele daling over 28 dagen. De agent actualiseert "
                           "de sterkste dalers; het resultaat landt in de Wachtrij."),
                "priority": prio, "payload": {"aantal": 2},
            }
        out.append(item)
        prio += 1

    # 2. Wachtrij die ligt te wachten: gemaakte waarde die niet live gaat.
    # Boven een bepaalde stapel is dit geen achterstand meer maar een
    # doorvoerprobleem, en dan is nóg meer schrijven schadelijk: het verstopt
    # precies de plek waar de opbrengst vandaan moet komen. Iris moet dat
    # verschil kunnen zien, anders blijft ze content_run voorstellen bij een
    # Wachtrij van 53 (WeAreImpact, 2 aug 2026).
    pending = glob.get("pending_review_total") or 0
    if pending >= 20:
        out.append({
            "prio": prio, "issue": "doorvoer",
            "actie": f"Doorvoer zit vast — {pending} concepten wachten op goedkeuring",
            "waarom": ("dit is geen productieprobleem maar een doorvoerprobleem: "
                       "meer schrijven maakt de stapel groter en levert geen klik op. "
                       "Stel géén content_run voor zolang dit staat"),
        })
        prio += 1
    elif pending:
        out.append({
            "prio": prio, "issue": "wachtrij",
            "actie": f"Keur de Wachtrij goed — {pending} stuk(s) wachten op jouw klik",
            "waarom": "content die blijft liggen levert niets op",
        })
        prio += 1

    # 3. Onmeetbare projecten: zonder GSC is elk SEO-oordeel giswerk.
    unmeasurable = [p for p in projects
                    if "GSC" in (p["pillars"]["seo"].get("note") or "")
                    or (p["pillars"]["seo"]["pages"] == 0
                        and not (p.get("trend") or {}).get("site"))]
    if unmeasurable:
        first = unmeasurable[0]
        out.append({
            "prio": prio, "issue": "onmeetbaar",
            "actie": f"Koppel Search Console voor {', '.join(p['project'] for p in unmeasurable[:4])}",
            "waarom": "zonder meetdata is hun SEO-cijfer een ondergrens en elk advies giswerk",
            "suggestion": {
                "type": "gsc_connect", "scope": first["project"],
                "target": first["project"],
                "title": f"Leg uit hoe je GSC koppelt voor {first['project']}",
                "detail": "Geen bruikbare Search Console-data — Iris zet de "
                          "koppel-instructie als kaart in het Actiecentrum.",
                "priority": prio, "payload": {},
            },
        })
        prio += 1

    # 4. Scheduler-fouten: kapotte automatisering ondermijnt alles hierboven.
    failures = glob.get("scheduler_failures") or []
    if failures:
        out.append({
            "prio": prio, "issue": "scheduler",
            "actie": f"Fix de scheduler — {len(failures)} job(s) faalden: "
                     + "; ".join(f["job"] for f in failures[:3]),
            "waarom": "een stille scheduler betekent dat agents ongemerkt stilstaan",
        })
        prio += 1

    # 4b. Vastgelopen doelen: alle taken zijn al terminaal (mislukt of deels
    # voltooid), maar niemand heeft op "Oplossen" geklikt. Dit blijft bewust
    # mensenwerk — een AI-retry kost LLM-budget en herstart taken die eerder al
    # faalden, dus geen `suggestion` (geen zelfstandige knop, zoals bij
    # `scheduler` hierboven). Zonder deze regel zag Iris alleen een laag
    # uitvoeringscijfer en kon ze "geen doelen" niet onderscheiden van "doelen
    # die vastzitten en wachten op de Oplossen-knop in het Actiecentrum".
    def _n(p):
        u = p["pillars"].get("uitvoering") or {}
        return u.get("failed_30d", 0) + u.get("partial_30d", 0)
    stalled = [p for p in projects if _n(p) > 0]
    if stalled:
        stalled.sort(key=_n, reverse=True)
        totaal = sum(_n(p) for p in stalled)
        namen = ", ".join(f"{p['project']} ({_n(p)})" for p in stalled[:3])
        out.append({
            "prio": prio, "issue": "doelen_vastgelopen",
            "actie": f"Los {totaal} vastgelopen doel(en) op — {namen}",
            "waarom": ("mislukt of deels voltooid in de laatste 30 dagen; dit werk "
                       "komt niet vanzelf verder. De Oplossen-knop in het "
                       "Actiecentrum herstart ze met AI — dat kost LLM-budget, dus "
                       "geen automatische actie van mij"),
        })
        prio += 1

    # 5. Zwakste échte project: pas als de systemische knelpunten benoemd zijn.
    if projects:
        weakest = projects[0]
        c = weakest["pillars"]["content"]
        item = {
            "prio": prio, "issue": "zwakste_project",
            "actie": f"Til {weakest['project']} omhoog (cijfer {weakest['grade']})",
            "waarom": f"zwakste project van dit moment",
        }
        if c.get("live_30d", 0) < c.get("target_30d", 1) and not c.get("pending_review"):
            item["suggestion"] = {
                "type": "content_run", "scope": weakest["project"],
                "target": weakest["site_id"],
                "title": f"Schrijf 1 artikel voor {weakest['project']}",
                "detail": f"Content {c.get('live_30d', 0)}/{c.get('target_30d', 0)} van het "
                          "maanddoel. Het artikel landt in de Wachtrij — niets gaat "
                          "live zonder jouw goedkeuring.",
                "priority": prio, "payload": {"aantal": 1},
            }
        out.append(item)

    # 5b. GEO-bottleneck: projecten met een lage AI-zichtbaarheid (GEO-score)
    # krijgen de GEO Specialist-agent voorgesteld. Dit is de 5e inzicht-pijler
    # naast content/seo/uitvoering/hygiene — de agent die de 5 GEO-hefbomen
    # (Bing, structured data, direct answer, entity/negations, UGC) oppakt.
    geo_weak = [p for p in projects
                if (p["pillars"].get("geo") or {}).get("scanned")
                and (p["pillars"]["geo"].get("score") or 100) < 85]
    if geo_weak:
        g = geo_weak[0]
        gscore = g["pillars"]["geo"].get("score")
        recs = (g["pillars"]["geo"].get("recommendations") or [])[:1]
        out.append({
            "prio": prio, "issue": "geo_zwak",
            "actie": f"Verhoog AI-zichtbaarheid van {g['project']} (GEO {gscore}/100)",
            "waarom": "ChatGPT/Perplexity citeren dit merk niet als bron — de "
                      "belangrijkste nieuwe verkeersbron wordt gemist. "
                      + (recs[0] if recs else ""),
            "suggestion": {
                "type": "geo_fix", "scope": g["project"],
                "target": g["site_id"],
                "title": f"Zet GEO Specialist op {g['project']}",
                "detail": f"GEO-score {gscore}/100. De GEO Specialist past de 5 "
                          "GEO-hefbomen toe (Bing-ranking, structured data, "
                          "direct-answer, entity/negations, UGC) zodat AI dit merk "
                          "citeert. Resultaat landt ter review — niets live zonder "
                          "jouw goedkeuring.",
                "priority": prio, "payload": {"site_id": g["site_id"], "agent_id": 15},
            },
        })
        prio += 1

    return out


def snapshot() -> Dict[str, Any]:
    """Het volledige cijferbeeld dat Iris elke ochtend analyseert."""
    snap = {"projects": project_scores(), "global": global_metrics()}
    snap["bottlenecks"] = bottlenecks(snap)
    return snap
