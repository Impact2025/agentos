"""Geconsolideerde healthcheck voor Agent OS.

Eén endpoint (/api/healthcheck) dat in één oogopslag laat zien:
  • welke LLM-backend actief is en of OpenModel/Ollama écht live zijn
  • of de Google Agenda-sync groen is
  • tokenverbruik vandaag vs. budget + grootverbruikers (per route)
  • wat de agent NÚ aan het doen is (doelen, delegaties, loops, tasks)
  • status van de achtergrond-scheduler en conveyor

Geen side-effects: alleen lezen. HTTP-timeouts kort zodat het endpoint
altijd snel antwoordt, ook als een provider hangt.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from fastapi import APIRouter

from ...shared.config import (
    DAILY_TOKEN_BUDGET, LLM_QUOTA_BACKOFF_MINUTES,
    HERMES_LOCAL_URL, OLLAMA_BASE_URL, OLLAMA_MODEL, OPENMODEL_API_KEY,
    OPENMODEL_BASE_URL, OPENMODEL_MODEL, hermes_backend,
)
from ...shared.database import get_conn
from ...shared.outcomes import (
    llm_quota_backoff_active, llm_usage_summary,
)
from ...scheduler import get_scheduler_status

# Lokale Omniroute-LLM-gateway (:8899) — de centrale router waar Hermes/AgentOS
# (en de claude-CLI) doorheen praten. Staat los van de cloud-OpenModel-check:
# openmodel kan bereikbaar zijn maar de gateway zélf down (zoals 13 aug 2026,
# toen de supervisor crashte en :8899 platlag terwijl openmodel live was).
LOCAL_GATEWAY_URL = "http://127.0.0.1:8899"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/healthcheck", tags=["health"])


# ── Liveness probes (parallel, kort time-out) ──────────────────────────────
def _probe_openmodel() -> dict:
    if not OPENMODEL_API_KEY:
        return {"configured": False, "live": None, "note": "geen OPENMODEL_API_KEY"}
    try:
        # HEAD op de basis-URL is genoeg om te zien of de gateway bereikbaar is.
        with httpx.Client(timeout=4.0) as c:
            r = c.get(OPENMODEL_BASE_URL.rstrip("/") + "/v1/messages",
                      headers={"Authorization": f"Bearer {OPENMODEL_API_KEY}"},
                      # verwacht 400/401 zonder body, niet 5xx → gateway leeft
                      )
        return {"configured": True, "live": r.status_code < 500,
                "http_status": r.status_code}
    except Exception as e:
        return {"configured": True, "live": False, "error": str(e)[:160]}


def _probe_local() -> dict:
    """De 'local' tier (HERMES_LOCAL_URL): meestal Ollama/LiteLLM op een
    lokale poort. Als die DOWN is, faalt de primaire backend — precies de
    bug van 2026-07-12 (dode LiteLLM :4000)."""
    if not HERMES_LOCAL_URL:
        return {"configured": False, "live": None,
                "note": "HERMES_LOCAL_URL niet gezet"}
    try:
        with httpx.Client(timeout=4.0) as c:
            r = c.get(HERMES_LOCAL_URL.rstrip("/") + "/models")
        live = r.status_code == 200
        models = []
        if live:
            try:
                models = [m["id"] for m in r.json().get("data", [])]
            except Exception:
                pass
        return {"configured": True, "live": live, "http_status": r.status_code,
                "url": HERMES_LOCAL_URL, "model": OLLAMA_MODEL,
                "model_available": (OLLAMA_MODEL in models) if models else None}
    except Exception as e:
        return {"configured": True, "live": False, "error": str(e)[:160],
                "url": HERMES_LOCAL_URL, "model": OLLAMA_MODEL}


def _probe_ollama() -> dict:
    if not OLLAMA_BASE_URL:
        return {"configured": False, "live": None,
                "note": "OLLAMA_BASE_URL niet gezet — lokale fallback uit",
                "model": OLLAMA_MODEL}
    try:
        with httpx.Client(timeout=4.0) as c:
            r = c.get(OLLAMA_BASE_URL.rstrip("/") + "/models")
        live = r.status_code == 200
        models = []
        if live:
            try:
                models = [m["id"] for m in r.json().get("data", [])]
            except Exception:
                pass
        return {"configured": True, "live": live,
                "http_status": r.status_code,
                "model": OLLAMA_MODEL,
                "model_available": (OLLAMA_MODEL in models) if models else None}
    except Exception as e:
        return {"configured": True, "live": False, "error": str(e)[:160],
                "model": OLLAMA_MODEL}


def _probe_calendar() -> dict:
    from ...domains.calendar import service as cal
    from ...shared.config import CALENDAR_CALENDAR_ID
    if not cal.is_configured():
        return {"configured": False, "live": None,
                "note": "Google Agenda niet geconfigureerd"}
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status, last_run_at, error FROM scheduler_runs "
                "WHERE job_id='calendar_sync'"
            ).fetchone()
        last = dict(row) if row else None
        # 'missed' is GEEN storing: APScheduler markeert zo een run die niet
        # vuurde omdat de machine sliep of de server heropstartte. De job
        # herstelt zichzelf bij de volgende fire. Dat als 'degraded' tonen
        # maakte het dashboard rood na elke slaapstand van de laptop
        # (04-08-2026) en leert de gebruiker de statusbadge te negeren.
        # Alleen een echte 'error' is een storing.
        status = (last or {}).get("status")
        return {
            "configured": True,
            # calendar_id rechtstreeks uit config (geen _cal_id()-call die
            # in de dispatcher-laag niet bestaat en de probe liet crashen).
            "calendar_id": CALENDAR_CALENDAR_ID or "primary",
            "last_sync": last,
            "live": status in ("ok", "missed"),
            "note": ("laatste run overgeslagen (machine sliep of server lag stil) "
                     "— draait vanzelf bij de volgende geplande run"
                     if status == "missed" else None),
        }
    except Exception as e:
        return {"configured": True, "live": None, "error": str(e)[:160]}


def _probe_gateway() -> dict:
    """Lokale Omniroute-LLM-gateway (:8899). Dit is de centrale router waar
    Hermes/AgentOS/claude doorheen praten. OpenModel kan live zijn maar de
    gateway zélf down — dat was de 13-aug-2026-storing (supervisor crashte)."""
    try:
        with httpx.Client(timeout=4.0) as c:
            r = c.get(LOCAL_GATEWAY_URL.rstrip("/") + "/health")
        return {"configured": True, "live": r.status_code == 200,
                "http_status": r.status_code, "url": LOCAL_GATEWAY_URL}
    except Exception as e:
        return {"configured": True, "live": False, "error": str(e)[:160],
                "url": LOCAL_GATEWAY_URL}


def _probe_social() -> dict:
    """Social-publishing gezondheid — DB-only, géén live Graph-poll per refresh.

    Een groene 'Systeem gezond' terwijl Facebook dood is (zoals 18 aug 2026:
    code 190 'Application has been deleted' op de Liefde-voor-Iedereen-pagina)
    is vals vertrouwen. Deze probe leest de recente publish/social-fouten uit
    activity_log en de social_posts-wachtrij — exact dezelfde bronnen die het
    Actiecentrum voedt — en markeert de health als 'warning' zodra een gekoppeld
    kanaal recent faalde. Geen externe API-call, dus veilig bij elke 15-30s poll.
    """
    try:
        with get_conn() as conn:
            # 1. Recente social/publish-failures (laatste 7 dagen)
            errs = conn.execute(
                "SELECT project, action, detail, created_at FROM activity_log "
                "WHERE (action LIKE '%social%' OR action LIKE '%publish%' OR action LIKE '%fb%') "
                "AND status='error' AND created_at >= datetime('now','-7 days') "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
            # 2. Social posts die op 'posted' staan maar geen enkele echte
            #    kanaal-bevestiging hebben (fout-positief 'geplaatst').
            ghost = conn.execute(
                "SELECT COUNT(*) AS n FROM social_posts WHERE status='posted' "
                "AND (posted_result_json IS NULL OR posted_result_json='' "
                "OR posted_result_json='{}')"
            ).fetchone()["n"]
        failures = [
            {"project": (r["project"] or ""), "action": r["action"],
             "detail": (r["detail"] or "")[:160], "created_at": r["created_at"]}
            for r in errs
        ]
        return {
            "configured": True,
            "live": len(failures) == 0 and ghost == 0,
            "recent_failures": failures,
            "ghost_posted": ghost,
            "note": (f"{len(failures)} recente publish/social-fout(en)"
                     + (f", {ghost} 'geplaatst' zonder bevestiging" if ghost else ""))
                    if (failures or ghost) else "geen recente fouten",
        }
    except Exception as e:
        return {"configured": True, "live": None, "error": str(e)[:160]}


def _collect_bugs() -> dict:
    """Verzamel recente fouten/bugs over de hele stack zodat de Health-tab ze
    in één oogopslag toont. Geen side-effects, alleen lezen.

    Drie bronnen:
      1. scheduler_runs met status='error' (achtergrond-jobs die echt faalden)
      2. goals met status 'failed'/'partial' (vastgelopen doelen) + hun
         mislukte taken
      3. iris_error_fixes die actief zijn met failures>0 (terugkerende bugs
         die de agent nog niet zelf heeft kunnen oplossen)
    """
    out = {"scheduler_errors": [], "stalled_goals": [], "recurring_bugs": []}
    try:
        with get_conn() as conn:
            # 1. scheduler errors (laatste 7 dagen, top 5 op tijd)
            try:
                rows = conn.execute(
                    "SELECT job_id, status, error, last_run_at FROM scheduler_runs "
                    "WHERE status='error' AND error IS NOT NULL AND error != '' "
                    "ORDER BY last_run_at DESC LIMIT 5"
                ).fetchall()
                for r in rows:
                    out["scheduler_errors"].append({
                        "job": r["job_id"], "status": r["status"],
                        "error": (r["error"] or "")[:240],
                        "last_run_at": r["last_run_at"],
                    })
            except Exception:
                pass

            # 2. vastgelopen doelen + hun failed taken
            try:
                goals = conn.execute(
                    "SELECT id, title, project, status, updated_at FROM goals "
                    "WHERE status IN ('failed','partial') ORDER BY updated_at DESC LIMIT 8"
                ).fetchall()
                for g in goals:
                    failed_tasks = conn.execute(
                        "SELECT title, error FROM goal_tasks "
                        "WHERE goal_id=? AND status='failed' ORDER BY ord LIMIT 3",
                        (g["id"],)
                    ).fetchall()
                    out["stalled_goals"].append({
                        "goal_id": g["id"], "title": g["title"],
                        "project": g["project"], "status": g["status"],
                        "failed_tasks": [{"title": t["title"],
                                          "error": (t["error"] or "")[:160]}
                                         for t in failed_tasks],
                    })
            except Exception:
                pass

            # 3. terugkerende bugs (iris auto-remedy learning)
            try:
                bugs = conn.execute(
                    "SELECT signature, project, diagnosis, failures, occurrences, "
                    "last_result FROM iris_error_fixes "
                    "WHERE active=1 AND failures>0 ORDER BY failures DESC LIMIT 5"
                ).fetchall()
                for b in bugs:
                    out["recurring_bugs"].append({
                        "signature": b["signature"],
                        "project": b["project"],
                        "diagnosis": (b["diagnosis"] or "")[:160],
                        "failures": b["failures"],
                        "occurrences": b["occurrences"],
                        "last_result": (b["last_result"] or "")[:80],
                    })
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


def _active_work() -> dict:
    """Wat de agent nu aan het doen is: openstaande runs in de DB."""
    out: dict = {}
    with get_conn() as conn:
        out["goals"] = [
            dict(r) for r in conn.execute(
                "SELECT id, title, project, status, current_phase, current_task, "
                "completed_tasks, task_count FROM goals "
                "WHERE status IN ('running','draft','paused') ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        ]
        out["delegations"] = [
            dict(r) for r in conn.execute(
                "SELECT id, objective, status, worker_count, created_at FROM delegations "
                "WHERE status IN ('running','partial') ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        ]
        out["loops"] = [
            dict(r) for r in conn.execute(
                "SELECT id, objective, status, best_score, iterations_run, max_iterations "
                "FROM loops WHERE status='running' ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        ]
        out["tasks_running"] = [
            dict(r) for r in conn.execute(
                "SELECT id, title, status, started_at FROM tasks "
                "WHERE status IN ('running','ready') ORDER BY started_at DESC LIMIT 15"
            ).fetchall()
        ]
        out["subagents_running"] = conn.execute(
            "SELECT COUNT(*) AS n FROM subagents WHERE status='running'"
        ).fetchone()["n"]
    return out


def _overall(items: dict) -> str:
    """Bepaalde een enkele statuskleur voor de hele check."""
    if not items["backend"]["local"]["live"] and not items["backend"]["ollama"]["live"] and \
       (items["llm"]["quota_backoff_active"] or not items["backend"]["openmodel"]["live"]):
        # geen enkele LLM-route live → kritiek
        return "degraded"
    if items["backend"]["active"] == "local" and not items["backend"]["local"]["live"]:
        # primaire backend (local) is geconfigureerd maar DOOD → dat was de
        # 2026-07-12 bug (dode LiteLLM :4000). Robin hood niet stil.
        return "degraded"
    if items["gateway"].get("configured") and not items["gateway"].get("live"):
        # lokale LLM-gateway (:8899) plat → agents kunnen niet routeren
        return "degraded"
    if items["calendar"].get("configured") and not items["calendar"].get("live"):
        return "degraded"
    bugs = items.get("bugs") or {}
    if bugs.get("scheduler_errors") or bugs.get("stalled_goals"):
        return "warning"
    # Social-publishing recent kapot (FB-token dood, post-failure) — de groene
    # 'Systeem gezond' mag niet groen blijven terwijl kanalen dood zijn.
    social = items.get("social") or {}
    if social.get("configured") and social.get("live") is False:
        return "warning"
    if items["llm"]["today"]["errors"] or items["llm"]["quota_backoff_active"]:
        return "warning"
    # Budget-overschrijding: de dagquota is op (>=100%). De provider-rem
    # (quota_backoff) grijpt pas in bij een echte 403, maar het DAILY_TOKEN_BUDGET
    # is hier al over — toon dat als waarschuwing zodat het niet groen lijkt.
    if items["llm"].get("budget") and items["llm"]["today"].get("budget_pct", 0) >= 100:
        return "warning"
    return "ok"


@router.get("")
def healthcheck():
    # Parallel proben zodat een hangende provider het endpoint niet blokkeert.
    # Harde overall-timeout (HEALTHCHECK_TIMEOUT): dit endpoint wordt bij elke
    # pagina-load én elke auto-refresh aangeroepen — als een externe provider
    # hangt, mag de hele Control Room niet 15s blijven laden. Na de timeout
    # krijgen niet-voltooide probes een nette "timeout"-status.
    HEALTHCHECK_TIMEOUT = 3.0
    quota_backoff = llm_quota_backoff_active()

    def _run(fn):
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(fn).result(timeout=HEALTHCHECK_TIMEOUT)
        except Exception as e:  # timeout of crash → niet-blokkerend
            return {"live": None, "error": f"timeout/{type(e).__name__}"}

    local = _run(_probe_local)
    # OpenModel-cloud-probe: alleen doen als er geen quota-backoff loopt én
    # de key gezet is. Bij backoff is de cloud toch niet bruikbaar en spaart
    # dit ~2s netwerklatentie per healthcheck-call.
    if quota_backoff or not OPENMODEL_API_KEY:
        openmodel = {"configured": bool(OPENMODEL_API_KEY), "live": None,
                     "note": "overgeslagen (quota-backoff actief)" if quota_backoff
                     else "geen OPENMODEL_API_KEY"}
    else:
        openmodel = _run(_probe_openmodel)
    ollama = _run(_probe_ollama)
    calendar = _run(_probe_calendar)
    gateway = _run(_probe_gateway)
    social = _run(_probe_social)
    active = _run(_active_work)
    bugs = _run(_collect_bugs)

    backend_now = hermes_backend()
    llm = llm_usage_summary(days=1)
    # markeer quota-markers expliciet in de summary
    llm["quota_backoff_active"] = quota_backoff
    llm["quota_backoff_minutes"] = LLM_QUOTA_BACKOFF_MINUTES

    scheduler = get_scheduler_status()
    # conveyor_running: app.state wordt hier niet geïmporteerd (dat herstart
    # impliciet de hele app en hangt ~8s). De conveyor is een aparte loop;
    # zijn status is niet kritiek voor de healthcheck en de scheduler dekt de
    # achtergrondverwerking al. Blijft None = "niet bepaald".
    conveyor_running = None

    items = {
        "backend": {
            "active": backend_now,
            "local": local,
            "openmodel": openmodel,
            "ollama": ollama,
            "openmodel_model": OPENMODEL_MODEL,
        },
        "calendar": calendar,
        "gateway": gateway,
        "social": social,
        "llm": llm,
        "active_work": active,
        "bugs": bugs,
        "scheduler": {
            "running": scheduler.get("running"),
            "catching_up": scheduler.get("catching_up"),
            "paused": scheduler.get("paused"),
            "jobs_total": len(scheduler.get("jobs", [])),
            "jobs_error": [
                j for j in scheduler.get("jobs", [])
                if j.get("last_run") and j["last_run"].get("status") == "error"
            ],
        },
        "conveyor_running": conveyor_running,
    }
    status = _overall(items)
    return {
        "status": status,
        "summary": _summary_line(items, status),
        "reden": _status_reden(items, status),
        **items,
    }


def _status_reden(items: dict, status: str) -> str:
    """De ene oorzaak die deze status verklaart, in drie woorden.

    4 aug 2026: het badge toonde 'Degraded · local·Ollama · 14% tokens'. Waaróm
    stond er nergens — alleen in de `title`-tooltip, die niemand opent. Een rode
    stip zonder reden leert de gebruiker precies één ding: de statusbadge
    negeren. De volgorde hieronder volgt `_overall()`, zodat de reden altijd de
    tak noemt die de status daadwerkelijk heeft gezet.
    """
    if status == "ok":
        return ""
    b = items["backend"]
    if not b["local"]["live"] and not b["ollama"]["live"] and (
            items["llm"]["quota_backoff_active"] or not b["openmodel"]["live"]):
        return "geen enkele LLM-route bereikbaar"
    if b["active"] == "local" and not b["local"]["live"]:
        return "primaire backend (local) is dood"
    cal = items["calendar"]
    if cal.get("configured") and not cal.get("live"):
        return "agenda-sync faalt"
    bugs = items.get("bugs") or {}
    if bugs.get("scheduler_errors"):
        first = bugs["scheduler_errors"][0]
        return f"scheduler-fout: {first.get('job')}"
    if bugs.get("stalled_goals"):
        return f"{len(bugs['stalled_goals'])} vastgelopen doel(en)"
    rb = bugs.get("recurring_bugs") or []
    if rb:
        # Operationele blokkades (geen code-bug) herkennen we aan de signature
        # zodat de banner zinnig blijft i.p.v. 'onbekende oorzaak'.
        sigs = " ".join(str(b.get("signature", "")).lower() for b in rb)
        if "application has been deleted" in sigs or "error validating application" in sigs:
            return "social-app (Instagram/Meta) verwijderd — token dood"
        if "publicatie geblokkeerd" in sigs or "taalcorruptie" in sigs:
            return f"{len(rb)} publish-gate blokkades (zwakke artikelen)"
        return f"{len(rb)} self-heal fout(en) — zie Actiecentrum"
    social = items.get("social") or {}
    if social.get("configured") and social.get("live") is False:
        if social.get("ghost_posted"):
            return f"{social.get('ghost_posted')} post(s) 'geplaatst' zonder bevestiging"
        n = len(social.get("recent_failures", []))
        return f"{n} social-publish fout(en) (laatste 7d)"
    if items["llm"]["quota_backoff_active"]:
        return "quota-rem actief"
    if items["llm"]["today"]["errors"]:
        return f"{items['llm']['today']['errors']} LLM-fouten vandaag"
    if items["llm"].get("budget") and items["llm"]["today"].get("budget_pct", 0) >= 100:
        return f"daglimiet tokens overschreden ({items['llm']['today']['budget_pct']}%)"
    return "onbekende oorzaak"


def _summary_line(items: dict, status: str) -> str:
    parts = []
    b = items["backend"]
    parts.append(f"LLM-backend: {b['active']}")
    if b["active"] == "local" and not b["local"]["live"]:
        parts.append("PRIMAIR local-tier DOOD (zie HERMES_LOCAL_URL)")
    if not b["ollama"]["live"]:
        parts.append("Ollama fallback NIET live")
    if items["llm"]["quota_backoff_active"]:
        parts.append("quota-backoff ACTIEF (autonoom gepauzeerd)")
    cal = items["calendar"]
    if cal.get("configured"):
        cal_status = (cal.get("last_sync") or {}).get("status")
        if cal_status == "missed":
            parts.append("agenda-sync run overgeslagen (herstelt zichzelf)")
        else:
            parts.append("agenda-sync " + ("ok" if cal.get("live") else "FOUT"))
    t = items["llm"]["today"]
    if items["llm"]["budget"]:
        parts.append(f"{t['total_tokens']:,}/{items['llm']['budget']:,} tokens ({t['budget_pct']}%)")
    aw = items["active_work"]
    n_active = len(aw["goals"]) + len(aw["delegations"]) + len(aw["loops"]) + len(aw["tasks_running"])
    parts.append(f"{n_active} lopende acties")
    return " · ".join(parts)
