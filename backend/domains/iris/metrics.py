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


def _seo_pillar(conn, site_id: str, gsc_configured: bool) -> Dict[str, Any]:
    # Meetbron: de nieuwste per-pagina GSC-snapshot uit gsc_history. Sites die
    # buiten Agent OS om gehost worden krijgen nooit rijen in published_pages
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
        # Zonder meetdata kan SEO nooit 'wereldklasse' heten — max 10/35.
        score = _clamp(min(pages, 10), 0, 10)
        note = "geen GSC-koppeling — vindbaarheid is niet meetbaar"
    elif pages == 0:
        score, note = 0.0, "nog geen gepubliceerde pagina's"
    else:
        # 15 punten: aandeel pagina's dat daadwerkelijk clicks krijgt.
        score = pages_with_clicks / pages * 15
        # 10 punten: absolute clicks (100+/30d = vol).
        score += _clamp(clicks / 100 * 10, 0, 10)
        # 10 punten: gemiddelde positie (pos 1 = 10, pos 30+ = 0).
        if avg_position:
            score += _clamp((30 - avg_position) / 29 * 10, 0, 10)
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
    ph = ",".join("?" for _ in project_names) or "''"
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
    finished = completed + failed
    if finished == 0:
        # Geen afgeronde doelen: half krediet als er tenminste iets loopt.
        score = 10.0 if running else 5.0
    else:
        score = _clamp(completed / finished * 20, 0, 20)
    return {
        "score": round(score, 1),
        "completed_30d": completed,
        "failed_30d": failed,
        "running": running,
        "by_status": by_status,
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

    try:
        from ..linkbuilding import service as lb_service
        linkbuilding = lb_service.funnel_stats()
    except Exception:
        linkbuilding = {}

    return {
        "errors_24h": errors_24h,
        "delivered_24h": delivered_24h,
        "pending_review_total": pending_review_total,
        "scheduler_failures": scheduler_failures,
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

    # 1. Acquisitie-funnel droog: input is de enige knop waar sales op draait.
    target = inputs.get("outreach_target") or 0
    sent = inputs.get("outreach_sent") or 0
    ready = inputs.get("outreach_drafts_ready") or 0
    by_status = funnel.get("by_status") or {}
    stock = (by_status.get("new") or 0) + (by_status.get("enriched") or 0)
    if target and (sent + ready) < target * 0.5:
        item: Dict[str, Any] = {
            "prio": prio,
            "issue": "funnel_droog",
            "actie": f"Vul de acquisitie-funnel — {sent} verstuurd + {ready} klaar "
                     f"tegen een weekdoel van {target}",
            "waarom": f"outreach 7d: {sent} verstuurd, {ready} concepten klaar, "
                      f"{stock} bruikbare lead(s) op voorraad",
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

    # 2. Wachtrij die ligt te wachten: gemaakte waarde die niet live gaat.
    pending = glob.get("pending_review_total") or 0
    if pending:
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

    return out


def snapshot() -> Dict[str, Any]:
    """Het volledige cijferbeeld dat Iris elke ochtend analyseert."""
    snap = {"projects": project_scores(), "global": global_metrics()}
    snap["bottlenecks"] = bottlenecks(snap)
    return snap
