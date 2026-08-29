"""Signaal voor de proactieve WhatsApp-check, opgehaald bij mijn-ondernemers-os
in plaats van lokaal herberekend.

Vincents rituelen (ochtend/avond, energie-attributie) leven sinds de multi-
tenant-migratie van mijn-ondernemers-os écht dáár, in de Neon-database — niet
meer in ImpactOS' eigen lokale tabellen. `coach/service.py`'s
`detect_proactive_signal()` was een losse Python-herimplementatie van dezelfde
logica tegen ImpactOS' eigen (mogelijk verouderde) rituelen-tabellen. Deze
functie vervangt die aanroep door de bridge naar de bron van waarheid: zelfde
patroon als `coach_router`'s `/reflection` en `/lessons` in router.py.

Faalt stil (None) bij elke fout — een onbereikbare mijn-ondernemers-os of een
niet-geconfigureerde bridge mag de tweewekelijkse scheduler-job niet laten
crashen, alleen geen signaal opleveren (zie coach/service.py:
check_and_send_whatsapp, dat al met `if not result["signal"]: return False`
omgaat met "geen signaal").
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .router import _call_mijn_ondernemers_os, _bridge_configured

logger = logging.getLogger(__name__)


async def fetch_remote_signal() -> Optional[Dict[str, Any]]:
    """Haalt het proactieve signaal op bij mijn-ondernemers-os' /api/coach/signal
    (geen /bridge/-prefix, ondanks dat die wel hetzelfde bridge-token gebruikt —
    zie src/app/api/coach/signal/route.ts in mijn-ondernemers-os).

    Retourneert None als de bridge niet geconfigureerd is of onbereikbaar —
    de aanroeper (coach/service.py) behandelt dat hetzelfde als "geen signaal".
    Anders het lokale dict-formaat van detect_proactive_signal (snake_case),
    zodat de bestaande dedupe (_whatsapp_already_sent/_whatsapp_mark_sent,
    die op pattern_key sleutelt) ongewijzigd blijft werken.
    """
    if not _bridge_configured():
        return None
    try:
        body = await _call_mijn_ondernemers_os("GET", "/api/coach/signal")
    except Exception as e:  # noqa: BLE001 — HTTPException hier ook, geen browser-context
        logger.warning("[coach-whatsapp] signaal ophalen bij mijn-ondernemers-os mislukt: %s", e)
        return None

    return {
        "signal": bool(body.get("signal")),
        "pattern_key": body.get("patternKey", ""),
        "message": body.get("message", ""),
    }
