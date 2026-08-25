"""
LSP-workshop "Bouw je AI-assistent" (AI Leadership Lab, 27 aug 2026).

Zelfde vorm als impact_leads.py hiernaast: de inzending bestaat al volledig
(WhatsApp heeft het rapport al naar het team gestuurd, zie
remote/api/whatsapp.js:handleWorkshopMessage — dit domein voert zelf niets
uit), dit haalt de rij alleen op zodat Vincent er ook een Actiecentrum-kaart
van ziet. Draait mee in bridge/service.sync_once, geen eigen scheduler-job.
"""
import logging
from typing import Any, Dict

import httpx

from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.outcomes import log_outcome

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


def _base() -> str:
    return BRIDGE_REMOTE_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {BRIDGE_TOKEN}"}


def _log_one(row: Dict[str, Any]) -> None:
    team = row.get("team_label") or f"Inzending van {row.get('sender', 'onbekend')}"
    if row.get("status") == "fout" or row.get("error"):
        log_outcome(
            "LSP Workshop", "inzending_niet_geanalyseerd",
            f"De inzending van {team} ({row.get('source')}) kon niet volledig "
            f"geanalyseerd worden: {row.get('error') or 'onbekende fout'}.",
            next_step="Bekijk de foto en toelichting handmatig en stuur zo nodig zelf een reactie.",
            status="error",
        )
        return
    agent_type = row.get("agent_type")
    detail = row.get("dashboard_summary") or row.get("participant_report") or "Geen samenvatting."
    if agent_type:
        detail = f"Voorgestelde agent: {agent_type}. {detail}"
    log_outcome(
        "LSP Workshop", team, detail,
        artifact=row.get("participant_report") or "",
        next_step="Bekijk het volledige rapport dat het team al ontvangen heeft.",
    )


async def process_pending() -> Dict[str, Any]:
    """Ophalen bij de bridge, een kaart loggen per inzending, ack'en. Eigen
    try/except zodat een mislukking hier de rest van de bridge-sync (die net
    geslaagd is) niet alsnog als 'failed' laat boeken."""
    summary: Dict[str, Any] = {"pulled": 0, "logged": 0, "failed": 0}
    if not (BRIDGE_REMOTE_URL and BRIDGE_TOKEN):
        return summary
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/bridge?op=lsp-submissions")
            r.raise_for_status()
            rows = r.json().get("submissions", [])
            summary["pulled"] = len(rows)
            if not rows:
                return summary

            ids = []
            for row in rows:
                try:
                    _log_one(row)
                    summary["logged"] += 1
                except Exception:  # noqa: BLE001
                    logger.exception("LSP-workshop: kaart loggen mislukt voor id %s", row.get("id"))
                    summary["failed"] += 1
                ids.append(row["id"])

            r = await client.post(f"{_base()}/api/bridge?op=lsp-submissions-ack",
                                  json={"ids": ids})
            r.raise_for_status()
    except Exception:
        logger.exception("LSP-workshop-inzendingen ophalen/verwerken mislukt")
    return summary
