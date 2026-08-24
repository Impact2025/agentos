"""Iris Pulse — het antwoord op "werkt het, en waar moet ik kijken?"

Aanleiding (19 aug 2026): Vincent mist op de Control Room een samenvattend
verhaal. Elke tab is er (Postvak, Agenda, Kansen, Leads, Wachtrij, Iris) en
elk mechanisme werkt — maar nergens staat in één oogopslag "wat deed Iris
deze week, en brengt het me dichter bij meer traffic/omzet/rust". Dat is
dezelfde fout als de per-project Dashboard: activiteit-vormig (wat wacht er)
in plaats van doel-vormig (wat is er gebeurd, en werkt het).

Dit bouwt geen nieuw mechanisme — het vat vijf bestaande bronnen samen:
  mail       backlog + reply-rate, uit dezelfde teller als het Postvak
  agenda     vandaag + openstaande voorstellen
  content    gepubliceerd deze week / Wachtrij / vastgelopen
  leads      acquisitieformule (funnel + input), zelfde functie als het
             ochtendrapport
  seo/ga     traffic-trend (GA4 7v7) + zoekverkeer-trend (GSC week-op-week)

De mail/agenda/analytics/seo-secties zijn letterlijk hergebruikt uit
`bridge/context.py` — dezelfde bouwers en dezelfde TTL-cache die Iris Remote
al voedt (elke 3 minuten ververst), dus dit kost geen extra Graph-/GA4-call
bovenop wat de bridge toch al ophaalt. `build_pulse()` (ook daaruit) levert
het deterministische goed/aandacht-oordeel — bewust zonder LLM, want een
oordeel dat wegvalt zodra de gateway hapert is precies het probleem dat dit
bestand oplost.

Wat NIET in dit bestand zit: een verzonnen "X uur bespaard"-metriek. Dat is
aantrekkelijk maar niet gemeten — zie CLAUDE.md-regel "activiteit is geen
effect". In plaats daarvan telt `_activity_stats()` wat Iris deze week
daadwerkelijk afrondde (`activity_log`, status='ok') — een harde telling,
geen schatting.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ...shared.database import get_conn
from ...shared.projects import squash_project

logger = logging.getLogger(__name__)


def _week_ago_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


def _site_ids_for_project(conn, project: str) -> list:
    doel = squash_project(project)
    rows = conn.execute("SELECT id, name FROM sites").fetchall()
    return [r["id"] for r in rows if squash_project(r["name"]) == doel]


def _content_stats(project: Optional[str] = None) -> Dict[str, Any]:
    """Wat de contentmotor deze week echt opleverde, en waar het vastzit.

    `reviewed_at` is niet "sinds wanneer live" maar "wanneer voor het laatst
    aangeraakt door review/publicatie" (zie CLAUDE.md 7e) — voor "gepubliceerd
    deze week" is dat precies het juiste veld.
    """
    week_ago = _week_ago_iso()
    try:
        with get_conn() as conn:
            site_filter = ""
            params: tuple = ()
            if project:
                site_ids = _site_ids_for_project(conn, project)
                if not site_ids:
                    return {"status": "ok", "published_7d": 0, "in_wachtrij": 0, "needs_work": 0, "stuck": 0}
                placeholders = ",".join("?" * len(site_ids))
                site_filter = f" AND site_id IN ({placeholders})"
                params = tuple(site_ids)
            published_7d = conn.execute(
                "SELECT COUNT(*) c FROM content_jobs WHERE status='published' AND reviewed_at >= ?" + site_filter,
                (week_ago,) + params,
            ).fetchone()["c"]
            wachtrij = conn.execute(
                "SELECT COUNT(*) c FROM content_jobs WHERE status='pending_review'" + site_filter,
                params,
            ).fetchone()["c"]
            needs_work = conn.execute(
                "SELECT COUNT(*) c FROM content_jobs WHERE status='needs_work'" + site_filter,
                params,
            ).fetchone()["c"]
            stuck = conn.execute(
                "SELECT COUNT(*) c FROM content_jobs WHERE status='stuck'" + site_filter,
                params,
            ).fetchone()["c"]
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: content-cijfers mislukt: %s", e)
        return {"status": "error", "error": str(e)[:200]}
    return {
        "status": "ok",
        "published_7d": published_7d,
        "in_wachtrij": wachtrij,
        "needs_work": needs_work,
        "stuck": stuck,
    }


def _leads_stats() -> Dict[str, Any]:
    """Zelfde functies als het ochtendrapport (`digest.py`) — één bron voor
    de acquisitieformule, geen tweede telling die kan gaan afwijken.

    De leadfunnel is niet per project op te splitsen (`leads` heeft geen
    projectkolom) — het IS de acquisitie voor WeAreImpact zelf, dus hoort
    alleen op dát project-dashboard thuis, ongefilterd."""
    try:
        from ..prospecting import funnel
        return {
            "status": "ok",
            "funnel": funnel.funnel_stats(),
            "input_7d": funnel.input_stats(7),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: leads-cijfers mislukt: %s", e)
        return {"status": "error", "error": str(e)[:200]}


def _activity_stats(project: Optional[str] = None) -> Dict[str, Any]:
    """Wat Iris deze week daadwerkelijk afrondde — een telling, geen schatting."""
    week_ago = _week_ago_iso()
    try:
        with get_conn() as conn:
            proj_filter = ""
            params: tuple = (week_ago,)
            if project:
                proj_filter = " AND project = ?"
                params = (week_ago, project)
            done = conn.execute(
                "SELECT COUNT(*) c FROM activity_log WHERE status='ok' AND created_at >= ?" + proj_filter,
                params,
            ).fetchone()["c"]
            errors = conn.execute(
                "SELECT COUNT(*) c FROM activity_log WHERE status='error' AND created_at >= ?" + proj_filter,
                params,
            ).fetchone()["c"]
            top = conn.execute(
                "SELECT action, COUNT(*) c FROM activity_log "
                "WHERE status='ok' AND created_at >= ?" + proj_filter + " GROUP BY action ORDER BY c DESC LIMIT 6",
                params,
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: activiteit-cijfers mislukt: %s", e)
        return {"status": "error", "error": str(e)[:200]}
    return {
        "status": "ok",
        "done_7d": done,
        "errors_7d": errors,
        "top_actions": [{"action": r["action"], "count": r["c"]} for r in top],
    }


def _hero_stats(project: Optional[str] = None) -> Dict[str, Any]:
    """Dezelfde drie tellingen als de Control-Room-hero (`iris/metrics.global_metrics`),
    hier optioneel gefilterd op één project — activity_log draagt zijn eigen
    `project`-kolom (ongewassen, zoals `goals.project`), content_jobs alleen
    via zijn site_id."""
    with get_conn() as conn:
        proj_filter = ""
        err_params: tuple = ()
        if project:
            proj_filter = " AND project = ?"
            err_params = (project,)
        errors_24h = conn.execute(
            "SELECT COUNT(*) c FROM activity_log WHERE status='error' "
            "AND created_at > datetime('now', '-1 day')" + proj_filter,
            err_params,
        ).fetchone()["c"]
        delivered_24h = conn.execute(
            "SELECT COUNT(*) c FROM activity_log WHERE status='ok' "
            "AND action IN ('task_done','goal_done','live','publicatie','wachtrij_staged') "
            "AND created_at > datetime('now', '-1 day')" + proj_filter,
            err_params,
        ).fetchone()["c"]
        if project:
            site_ids = _site_ids_for_project(conn, project)
            if site_ids:
                placeholders = ",".join("?" * len(site_ids))
                pending_review_total = conn.execute(
                    f"SELECT COUNT(*) c FROM content_jobs WHERE status='pending_review' AND site_id IN ({placeholders})",
                    tuple(site_ids),
                ).fetchone()["c"]
            else:
                pending_review_total = 0
            running_goals = conn.execute(
                "SELECT COUNT(*) c FROM goals WHERE status IN ('running','ready')" + proj_filter,
                err_params,
            ).fetchone()["c"]
        else:
            pending_review_total = conn.execute(
                "SELECT COUNT(*) c FROM content_jobs WHERE status='pending_review'"
            ).fetchone()["c"]
            running_goals = conn.execute(
                "SELECT COUNT(*) c FROM goals WHERE status IN ('running','ready')"
            ).fetchone()["c"]
    quick_wins = 0
    try:
        from ..analytics import insights
        if project:
            doel = squash_project(project)
            for p in insights.summary().get("projects", []):
                if squash_project(p.get("project") or "") == doel:
                    quick_wins = p.get("quick_wins") or 0
                    break
        else:
            quick_wins = sum((p.get("quick_wins") or 0) for p in insights.summary().get("projects", []))
    except Exception:  # noqa: BLE001
        logger.warning("Pulse: quick-wins tellen mislukt", exc_info=True)
    return {
        "errors_24h": errors_24h,
        "delivered_24h": delivered_24h,
        "pending_review_total": pending_review_total,
        "quick_wins": quick_wins,
        "running_goals": running_goals,
    }


async def build_home_pulse(project: Optional[str] = None) -> Dict[str, Any]:
    """Eén call, vijf bronnen, één oordeel — voor de hero bovenaan de Control Room.

    Met `project` gefilterd tot dat project: content/activiteit/hero-tellingen
    filteren op de eigen site/goals, seo filtert tot de bijpassende site.
    Mail en agenda blijven bewust ongefilterd — dat is Vincent's eigen mailbox
    en agenda, geen projectgebonden gegeven (zie CLAUDE.md 13/14d)."""
    from ..bridge import context as bridge_context

    sections: Dict[str, Any] = {}
    try:
        sections["mail"] = await bridge_context._section(  # noqa: SLF001 — zelfde cache als de bridge
            "mail", bridge_context.TTL_MAIL, bridge_context.build_mail)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: mail-sectie mislukt: %s", e)
        sections["mail"] = {"status": "error", "error": str(e)[:200]}
    try:
        sections["agenda"] = await bridge_context._section(  # noqa: SLF001
            "agenda", bridge_context.TTL_AGENDA, bridge_context.build_agenda)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: agenda-sectie mislukt: %s", e)
        sections["agenda"] = {"status": "error", "error": str(e)[:200]}
    try:
        sections["analytics"] = await bridge_context._section(  # noqa: SLF001
            "analytics", bridge_context.TTL_ANALYTICS, bridge_context.build_analytics)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: analytics-sectie mislukt: %s", e)
        sections["analytics"] = {"status": "error", "error": str(e)[:200]}
    try:
        sections["seo"] = await bridge_context._section(  # noqa: SLF001
            "seo", bridge_context.TTL_SEO, bridge_context.build_seo)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: seo-sectie mislukt: %s", e)
        sections["seo"] = {"status": "error", "error": str(e)[:200]}

    if project and sections["seo"].get("sites"):
        doel = squash_project(project)
        sections["seo"] = {"sites": [s for s in sections["seo"]["sites"] if squash_project(s.get("name") or "") == doel]}

    try:
        oordeel = bridge_context.build_pulse(sections)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pulse: oordeel bouwen mislukt: %s", e)
        oordeel = {"good": [], "bad": [], "unavailable": list(sections.keys())}

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mail": sections["mail"],
        "agenda": sections["agenda"],
        "analytics": sections["analytics"],
        "seo": sections["seo"],
        "content": _content_stats(project),
        "leads": _leads_stats(),
        "activity": _activity_stats(project),
        "hero": _hero_stats(project),
        "good": oordeel.get("good", []),
        "bad": oordeel.get("bad", []),
        "unavailable": oordeel.get("unavailable", []),
    }
    if project:
        out["project"] = project
        # Leads is Vincents eigen acquisitie — alleen zinvol op zíjn eigen
        # project-dashboard, anders belooft de tegel iets dat niet bij het
        # gekozen project hoort.
        if squash_project(project) != squash_project("WeAreImpact"):
            out["leads"] = {"status": "off", "reason": "leadfunnel is WeAreImpact-acquisitie, niet per project"}
    return out
