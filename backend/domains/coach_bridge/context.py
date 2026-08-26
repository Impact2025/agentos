"""Holding-brede context voor De Sparringpartner.

Vincents persoonlijke coach in mijn-ondernemers-os gaat nooit over één
project maar over de hele WeAreImpact-holding — daarom leest deze module
dezelfde bronnen die de Control Room gebruikt (project_scores, de
waarheidsaudit, gemiste runs, Iris' laatste briefing, de agenda) maar
aggregeert ze tot iets dat een coachgesprek kan gebruiken: "hoeveel projecten
staan stil", niet "welke SEO-taak faalt op welke site". Puur lezen, nooit
schrijven — de coach gaat over Vincent, niet over de bedrijfsuitvoering
(zie CLAUDE.md §9a: naast Iris, niet erboven).

Elke sectie draagt een eigen `status` (`ok`/`off`/`error`), zelfde regel als
`bridge/context.py`: "niet geconfigureerd" mag nooit als "alles rustig"
overkomen bij een coach die daarop reflecteert.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Amsterdam")

# Hergebruikt de bestaande cache-tabel/patroon uit bridge/context.py i.p.v. een
# eigen laag te verzinnen — zelfde afweging als daar: dit voorkomt dat elke
# poll vanuit mijn-ondernemers-os een volle SQLite-scan over alle projecten
# plus een agenda-call doet.
from ..bridge.context import _cache_read, _cache_write, build_agenda  # noqa: E402

_CACHE_KEY = "coach:holding"
_TTL = 15 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build() -> Dict[str, Any]:
    from ..iris import integrity, metrics
    from ..iris import service as iris_service
    from ...shared import downtime

    try:
        scores = metrics.project_scores()
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context: project_scores mislukt: %s", e)
        scores = []

    projecten = [
        {"project": s["project"], "score": s["score"], "grade": s["grade"]}
        for s in scores
    ]
    stilstaand = sorted(
        (p for p in projecten if p["score"] < 40),
        key=lambda p: p["score"],
    )

    try:
        audit = integrity.audit_summary()
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context: audit_summary mislukt: %s", e)
        audit = {"open_totaal": 0, "blokkerend": 0}

    try:
        gaps = downtime.summary()
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context: downtime.summary mislukt: %s", e)
        gaps = []

    try:
        report = iris_service.latest_report()
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context: latest_report mislukt: %s", e)
        report = None

    iris_advice = []
    iris_report_date = None
    if report:
        iris_report_date = report.get("report_date")
        advice = report.get("advice") or []
        if isinstance(advice, list):
            iris_advice = advice[:5]

    return {
        "status": "ok",
        "generated_at": _now_iso(),
        "projecten": {
            "totaal": len(projecten),
            "stilstaand": stilstaand,  # score < 40 — de holding-brede "wat staat stil"
            "gemiddelde_score": round(sum(p["score"] for p in projecten) / len(projecten), 1) if projecten else None,
        },
        "waarheidsaudit": {
            "open_totaal": audit.get("open_totaal", 0),
            "blokkerend": audit.get("blokkerend", 0),
        },
        "gemiste_runs": {
            "aantal_jobs": len(gaps),
            "jobs": [{"label": g["label"], "missed": g["missed"]} for g in gaps[:5]],
        },
        "iris": {
            "report_date": iris_report_date,
            "top_advies": iris_advice,
        },
    }


async def build_holding_context() -> Dict[str, Any]:
    cached, fresh = _cache_read(_CACHE_KEY, _TTL)
    if fresh:
        return cached
    try:
        result = await asyncio.to_thread(_build)
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context bouwen mislukt: %s", e)
        if cached:
            return {**cached, "stale": True, "error": str(e)[:200]}
        return {"status": "error", "error": str(e)[:200]}

    try:
        agenda = await build_agenda()
    except Exception as e:  # noqa: BLE001
        logger.warning("Coach-context: agenda ophalen mislukt: %s", e)
        agenda = {"status": "error"}

    # build_agenda() zet alleen op de faalpaden een "status"-sleutel (de
    # succesvolle return heeft die niet — hij wordt normaliter via _section()
    # aangevuld, wat we hier bewust overslaan om niet dubbel te cachen).
    if agenda.get("status") in ("off", "error"):
        result["agenda"] = {"status": agenda.get("status", "off")}
    else:
        result["agenda"] = {
            "status": "ok",
            "vandaag_afspraken": len(agenda.get("today", [])),
            "vrije_blokken_vandaag": agenda.get("free_today", []),
        }

    _cache_write(_CACHE_KEY, result)
    return result
