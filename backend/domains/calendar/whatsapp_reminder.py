"""Agenda-herinnering 1 uur van tevoren, per WhatsApp.

Ander doel dan `reminder.py` (dat is 1 dag vooruit, per mail, en alleen voor
afspraken die Iris zelf via `calendar_proposals` heeft geboekt). Dit vangt
élke afspraak in de agenda — ook handmatig ingevoerde ("Tandarts", "Kapper")
— want Vincent wil precies zien met wie hij zo een meeting heeft en waar hij
op moet letten, niet alleen voor wat de agent zelf plande.

Bron is `calendar_service.get_events_range`, niet de 15-min `calendar_events`-
cache van `calendar_sync_job`: alleen `get_events_range` levert deelnemers
mee (zie service_google.py — "de telefoon kan zo tonen wíe er in de afspraak
zit"), en dat is precies wat hier nodig is.

Verzenden loopt via de bridge naar Iris Remote: het WhatsApp-token is
Vincents eigen Meta-app-credential en leeft alleen in Vercel (CLAUDE.md
14e-b), dus deze machine kan niet zelf versturen. Bridge niet geconfigureerd
of tenant heeft geen WhatsApp gekoppeld → stil 0 verstuurd, geen crash
(zelfde regel als overal in dit domein: een niet-geconfigureerd kanaal is
geen storing).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from ...shared.database import get_conn

log = logging.getLogger(__name__)
_TZ = ZoneInfo("Europe/Amsterdam")

# De job draait elke 10 min (zie scheduler.py); het venster is ruimer dan dat
# interval zodat een trage of overgeslagen ronde geen event mist. Dubbel
# versturen wordt voorkomen door `calendar_hourly_reminders`, niet door een
# smal venster — een smal venster + een gemiste ronde is precies hoe een
# afspraak zonder herinnering blijft.
_WINDOW_START = timedelta(minutes=50)
_WINDOW_END = timedelta(minutes=70)


def _parse_iso(s: str):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        return dt
    except Exception:
        return None


def _already_sent(event_key: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM calendar_hourly_reminders WHERE event_key=?", (event_key,)
        ).fetchone()
    return row is not None


def _mark_sent(event_key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO calendar_hourly_reminders (event_key, sent_at) VALUES (?, ?)",
            (event_key, datetime.now(timezone.utc).isoformat()),
        )


def _watch_out_for(ev: Dict) -> str:
    """Deterministische 'let op'-regel, bewust zonder LLM — zelfde afweging
    als `radar/quality.py` en de kansen-gate: dit hoort ook te werken als de
    gateway plat ligt, en het is een vormvraag (online/fysiek/onbekend) geen
    inhoudsvraag."""
    if ev.get("hangout_link"):
        return f"Online: {ev['hangout_link']}"
    loc = (ev.get("location") or "").strip()
    if loc:
        return f"Op locatie: {loc} — reken reistijd erbij."
    return "Geen locatie ingevuld in de agenda."


def _describe_attendees(ev: Dict) -> str:
    names = [a.get("name") for a in (ev.get("attendees") or [])
             if a.get("name") and a.get("name") != "?"]
    return "Met: " + ", ".join(names) if names else ""


def build_message(ev: Dict) -> str:
    start = _parse_iso(ev.get("start"))
    t0 = start.strftime("%H:%M") if start else "?"
    lines = [f"Over een uur ({t0}): {ev.get('summary') or '(geen titel)'}"]
    who = _describe_attendees(ev)
    if who:
        lines.append(who)
    lines.append(_watch_out_for(ev))
    return "\n".join(lines)


async def collect_due() -> List[Dict]:
    from . import service as calendar_service
    if not calendar_service.is_configured():
        return []
    now = datetime.now(timezone.utc)
    data = await calendar_service.get_events_range(now + _WINDOW_START, now + _WINDOW_END)
    out: List[Dict] = []
    for ev in data.get("events", []):
        if ev.get("declined") or ev.get("all_day"):
            continue
        if not _parse_iso(ev.get("start")):
            continue
        key = f"{ev.get('id') or ev.get('summary')}:{ev.get('start')}"
        if _already_sent(key):
            continue
        ev = dict(ev)
        ev["_key"] = key
        out.append(ev)
    return out


async def run() -> int:
    """Stuur WhatsApp-herinneringen voor afspraken die over ~1 uur beginnen.
    Retourneert het aantal verstuurde berichten."""
    from ..bridge import service as bridge_service
    if not bridge_service.enabled():
        return 0
    due = await collect_due()
    sent = 0
    for ev in due:
        ok = await bridge_service.send_whatsapp_reminder(build_message(ev))
        if ok:
            _mark_sent(ev["_key"])
            sent += 1
            log.info("[agenda-whatsapp] herinnering verstuurd: %s", ev.get("summary"))
        else:
            log.warning("[agenda-whatsapp] versturen mislukt, blijft openstaan: %s", ev.get("summary"))
    return sent
