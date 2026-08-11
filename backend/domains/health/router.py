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
    if items["calendar"].get("configured") and not items["calendar"].get("live"):
        return "degraded"
    if items["llm"]["today"]["errors"] or items["llm"]["quota_backoff_active"]:
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
    active = _run(_active_work)

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
        "llm": llm,
        "active_work": active,
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
    if items["llm"]["quota_backoff_active"]:
        return "quota-rem actief"
    if items["llm"]["today"]["errors"]:
        return f"{items['llm']['today']['errors']} LLM-fouten vandaag"
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
