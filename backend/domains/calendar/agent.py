"""Agenda-agent: mail → afspraak-voorstel, met conflict- en reistijd-logica.

Wereldklasse-gedachte (Vincent): Iris plant op basis van binnenkomende mail
automatisch afspraken, incalculeert reistijd, stelt prioriteiten, en voorkomt
dubbele boekingen — zónder dat Vincent er handwerk aan heeft. Maar: net als de
mail-helpdesk en content-pipeline hanteert Agent OS een menselijke review-gate.
We schrijven NIET direct in de agenda; we leggen een `calendar_proposals`-rij
neer (status=pending_review). Vincent keurt goed → pas dán gaat hij naar
Google Calendar. Nooit auto-boeken.

Stroom per g detective mail (classify=='appointment'):
  1. extract_appointment() — haal datum/tijd, duur, locatie, prioriteit, deelnemers
     uit de mail (regelgebaseerd; geen LLM-noodzaak voor de kern).
  2. resolve_slot() — als de mail een expliciete wens heeft, check dan Google
     free/busy (get_busy_times) op conflict; zo niet, stel een vrij slot voor
     rond de gewenste dag.
  3. travel_buffer() — als de locatie ≠ Vincent's thuisbasis, voeg 30 min
     reistijd toe vóór én na (of alleen na, configureerbaar).
  4. create_proposal() — schrijf de rij + log naar activity_log.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ...shared.database import get_conn

log = logging.getLogger(__name__)

# Echte zone, geen vaste UTC+2: die laatste klopt alleen in de zomer en zet
# vanaf de laatste zondag van oktober élke afspraak een uur verkeerd.
_TZ = ZoneInfo("Europe/Amsterdam")

# Vincents thuisbasis (geen reistijd nodig). Alles wat hier niet naar wijst,
# telt als "onderweg" en krijgt een reisbuffer.
_HOME_BASE_TOKENS = ("thuis", "kantoor", "weareimpact", "impactbox", "philia",
                     "online", "teams", "zoom", "meet", "bellen", "telefoon",
                     "videocall", "videogesprek", "remote", "digitaal")
# Reistijd-buffer in minuten (heen + terug wordt apart toegevoegd).
_TRAVEL_BUFFER_MIN = 30
# Standaard-afspraakduur als de mail geen noemt (minuten).
_DEFAULT_DURATION_MIN = 30
# Ver onder de drukke uren: vrije-slot-voorstel werkt binnen kantooruren NL.
_WORK_START_H, _WORK_END_H = 9, 17

_NL_DAYS = {
    "maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3,
    "vrijdag": 4, "zaterdag": 5, "zondag": 6,
}
_MONTHS = {
    "jan": 1, "januari": 1, "feb": 2, "februari": 2, "mrt": 3, "maart": 3,
    "apr": 4, "april": 4, "mei": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "augustus": 8, "sep": 9, "september": 9, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _amsterdam_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(_TZ)


def extract_appointment(subject: str, body: str, from_addr: str = "") -> Dict:
    """Haal afspraak-gegevens uit een mail. Regelgebaseerd, geen LLM.

    Returns dict met keys: has_time (bool), date (datetime|None),
    duration_min (int), location (str), is_remote (bool), priority (str),
    attendees (list), raw_hints (list).
    """
    text = f"{subject or ''}\n{body or ''}".lower()
    out: Dict = {
        "has_time": False,
        "date": None,
        "duration_min": _DEFAULT_DURATION_MIN,
        "location": "",
        "is_remote": False,
        "priority": "normal",
        "attendees": [from_addr] if from_addr else [],
        "raw_hints": [],
    }

    # ── Locatie ──
    loc_m = re.search(r"(locatie|adres|plek|waar)\s*[:=]?\s*([^\n,.;]{3,60})", text)
    if loc_m:
        out["location"] = loc_m.group(2).strip()
    # remote-signalen: alleen expliciete online-platformen tellen als remote.
    # 'bellen'/'telefonisch' is ambigu (kan ook 'ik bel je op kantoor' zijn) en
    # telt alleen als er géén fysieke locatie genoemd wordt.
    explicit_remote = any(t in text for t in ("online", "teams", "zoom", "meet",
                                "videocall", "videogesprek", "per zoom",
                                "google meet", "remote", "digitaal", "skype"))
    phone_words = any(t in text for t in ("bellen", "telefonisch", "telefoon"))
    if explicit_remote:
        out["is_remote"] = True
        out["location"] = out["location"] or "Online (videocall)"
    elif phone_words and not out["location"]:
        out["is_remote"] = True
        out["location"] = "Telefonisch"

    # ── Duur ──
    dur_m = re.search(r"(\d+)\s*(uur|uurb|min|minuten|kwartier)", text)
    if dur_m:
        n = int(dur_m.group(1))
        unit = dur_m.group(2)
        out["duration_min"] = (n * 60 if "uur" in unit else
                               15 if "kwartier" in unit else n)
        out["raw_hints"].append(f"duur {out['duration_min']}min")

    # ── Prioriteit ──
    if any(t in text for t in ("spoed", "urgent", "asap", "zo snel", "direct",
                                "beltje", "terugbellen", "belangrijk", "prioriteit")):
        out["priority"] = "high"
        out["raw_hints"].append("priority=high")
    elif any(t in text for t in ("informeel", "even", "kort", "kennismaking",
                                  "vrijblijvend", "eens kijken")):
        out["priority"] = "low"
        out["raw_hints"].append("priority=low")

    # ── Datum/tijd ──
    out["date"] = _parse_datetime(text)
    out["has_time"] = out["date"] is not None
    if out["date"]:
        out["raw_hints"].append("datetime=" + out["date"].strftime("%a %d-%m %H:%M"))
    return out


def _parse_datetime(text: str) -> Optional[datetime]:
    """Beste-inspanning parse van NL datum/tijd in vrije tekst.

    Ondersteunt: weekdagen ('dinsdag 14:00'), 'morgen'/'overmorgen',
    'volgende week', dag-maand ('23 juli'), en ISO-achtige fragmenten.
    Geen NLP — alleen veelvoorkomende patronen. Mislukt → None (dan stelt de
    agent een vrij slot voor in plaats van een expliciete tijd).
    """
    now = _amsterdam_now()

    # 1) expliciete kloktijd — kies de eerste match die ook een geldige klok
    # oplevert. Het [:.]-patroon vangt óók decimalen op ("€ 12,75" schrijft men
    # niet, maar "1.99 euro" of een versienummer "2.60" wel); zonder bereik-check
    # belandt minute=75 in datetime.replace() → "minute must be in 0..59" en de
    # hele mailbox-poll klapt eruit. We slaan zulke schijn-tijden gewoon over.
    hour, minute = None, 0
    for tm in re.finditer(r"(\d{1,2})[:.](\d{2})", text):
        h, m = int(tm.group(1)), int(tm.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            hour, minute = h, m
            break

    # 2) dag-bepaling
    day_offset = None
    target_weekday = None
    target = None
    # weekdag los detecteren (ook als 'volgende week' erbij staat)
    for dname, dnum in _NL_DAYS.items():
        if re.search(rf"\b{dname}\b", text):
            target_weekday = dnum
            break
    if "overmorgen" in text:  # vóór 'morgen': dat is er een substring van
        day_offset = 2
    elif "morgen" in text:
        day_offset = 1
    elif "volgende week" in text or "komende week" in text:
        day_offset = 7
    # dag-maand (23 juli / 23-7)
    dm = re.search(r"(\d{1,2})[\s-]+(jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec|"
                   r"januari|februari|maart|april|mei|juni|juli|augustus|september|"
                   r"oktober|november|december)", text)
    if dm:
        d, mname = int(dm.group(1)), dm.group(2)
        mnum = _MONTHS.get(mname[:3], 0)
        if mnum:
            yr = now.year + (1 if (mnum < now.month) else 0)
            try:
                target = datetime(yr, mnum, d, tzinfo=_TZ)
            except ValueError:
                target = None

    if target is None:
        if target_weekday is not None:
            # een weekdag genoemd (bv. 'dinsdag'); als ook 'volgende week' erbij
            # staat, verschuif naar dezelfde weekdag in de volgende week.
            base = now + timedelta(days=(target_weekday - now.weekday()) % 7)
            if day_offset == 7 or "volgende week" in text or "komende week" in text:
                base = base + timedelta(days=7)
            target = base.replace(hour=0, minute=0, second=0, microsecond=0)
        elif day_offset is not None:
            target = (now + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0)

    if target is None:
        return None

    if hour is not None:
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        # geen tijd genoemd → stel een werkbare default voor (10:00)
        target = target.replace(hour=10, minute=0, second=0, microsecond=0)
    # nooit in het verleden. Bij een genoemde weekdag moet de uitwijk een week
    # zijn (anders wordt 'dinsdag 09:00' op dinsdagmiddag stilletjes woensdag).
    if target < now:
        target = target + timedelta(days=7 if target_weekday is not None else 1)
    return target


def _needs_travel(location: str, is_remote: bool) -> bool:
    if is_remote:
        return False
    if not location:
        return False  # onbekend → geen risico nemen, geen buffer
    loc = location.lower()
    return not any(t in loc for t in _HOME_BASE_TOKENS)


def _free_busy_conflict(start: datetime, end: datetime) -> tuple:
    """Vraag Google Calendar free/busy (als gekoppeld).

    Returns (status, overlaps) met status 'ok' (gecontroleerd), 'unavailable'
    (geen agenda gekoppeld) of 'error' (check mislukt). Die drie mógen niet op
    één hoop: een mislukte check is géén bewijs van een vrij slot, en juist dat
    verschil houdt dubbele boekingen tegen.
    """
    try:
        from ...domains.calendar import service as cal
        if not cal.is_configured():
            return "unavailable", []
        # calendar.get_busy_times is async; we draaien hem via een helper
        busy = _run_async(cal.get_busy_times(start, end))
        overlaps = []
        for b in busy:
            bs = _parse_iso(b.get("start"))
            be = _parse_iso(b.get("end"))
            if bs and be and bs < end and be > start:
                overlaps.append({"start": b.get("start"), "end": b.get("end")})
        return "ok", overlaps
    except Exception as e:
        log.warning("[agenda-agent] free/busy check mislukt: %s", e)
        return "error", []


def _run_async(coro):
    """Draai een coroutine vanuit sync-code.

    Nooit opnieuw proberen met dezelfde coroutine: die is na één await
    verbruikt, dus een retry faalt altijd met 'cannot reuse already awaited
    coroutine' en maskeert de échte fout.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # geen loop actief — het normale pad
    # We zitten al in een draaiende loop (bv. vanuit een async route):
    # asyncio.run() mag daar niet, dus een eigen loop in een aparte thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def create_proposal(mailbox_id: str, inbox_id: int, subject: str, from_addr: str,
                    body: str) -> Optional[Dict]:
    """Bouw een afspraak-voorstel op basis van de mail en schrijf het als
    pending_review. Geen directe agenda-schrijf. Retourneert de rij of None."""
    appt = extract_appointment(subject, body, from_addr)
    now = _amsterdam_now()

    # Bepaal start: expliciete wens, anders een vrij slot binnenkort.
    if appt["date"]:
        start = appt["date"]
    else:
        # geen tijd in mail → stel morgen 10:00 voor (mens beslist definitief)
        start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

    duration = appt["duration_min"]
    end = start + timedelta(minutes=duration)

    # Reistijd-buffer als onderweg.
    travel = _needs_travel(appt["location"], appt["is_remote"])
    travel_buffer = _TRAVEL_BUFFER_MIN if travel else 0
    if travel:
        start = start - timedelta(minutes=travel_buffer)
        end = end + timedelta(minutes=travel_buffer)

    # Conflict-check tegen Google Calendar.
    fb_status, conflicts = _free_busy_conflict(start, end)
    conflict_note = ""
    if conflicts:
        conflict_note = ("LET OP: overlap met bestaande afspraak " +
                         "; ".join(f"{c['start']}–{c['end']}" for c in conflicts[:2]) +
                         ". Verplaats of kies een ander slot.")
        appt["priority"] = "high"  # conflict moet opvallen in de review
    elif fb_status == "unavailable":
        conflict_note = ("Niet op dubbele boeking gecontroleerd: geen Google Agenda "
                         "gekoppeld. Goedkeuren is geblokkeerd tot dat klopt.")
    elif fb_status == "error":
        conflict_note = ("Niet op dubbele boeking gecontroleerd: de agenda-check "
                         "mislukte. Goedkeuren is geblokkeerd tot dat klopt.")

    # Samenvatting voor de mens.
    title = f"Afspraak: {subject[:60]}" if subject else f"Afspraak met {from_addr}"
    rationale = (
        f"Mail van {from_addr}: afspraak-verzoek gedetecteerd. "
        f"Voorgesteld: {start.strftime('%a %d-%m %H:%M')}–{end.strftime('%H:%M')} "
        f"({duration} min{' + '+str(travel_buffer)+' min reistijd' if travel else ''}). "
        f"Locatie: {appt['location'] or 'niet genoemd'}. "
        f"Prioriteit: {appt['priority']}. " + conflict_note
    )

    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO calendar_proposals
                   (mailbox_id, inbox_id, from_addr, subject, title,
                    proposed_start, proposed_end, location, is_remote,
                    duration_min, travel_buffer_min, priority, conflict_note,
                    conflict_checked, rationale, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending_review', datetime('now'))""",
                (mailbox_id, inbox_id, from_addr, subject, title,
                 start.isoformat(), end.isoformat(), appt["location"],
                 1 if appt["is_remote"] else 0, duration, travel_buffer,
                 appt["priority"], conflict_note, fb_status, rationale),
            )
            pid = cur.lastrowid
        log.info("[agenda-agent] voorstel %s aangemaakt voor %s", pid, from_addr)
        return {"id": pid, "title": title, "start": start.isoformat(),
                "priority": appt["priority"], "conflict": bool(conflicts)}
    except Exception:
        log.exception("[agenda-agent] kon voorstel niet opslaan")
        return None


def approve_proposal(proposal_id: int) -> Dict:
    """Mens keurt goed → schrijf naar Google Calendar via block_time."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM calendar_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if not r:
            return {"ok": False, "code": "not_found", "error": "voorstel niet gevonden"}
        if r["status"] != "pending_review":
            return {"ok": False, "code": "wrong_status",
                    "error": f"status is '{r['status']}', niet pending"}
        start = _parse_iso(r["proposed_start"])
        end = _parse_iso(r["proposed_end"])
        # Nooit boeken op een slot dat we niet tegen de agenda's konden toetsen:
        # dan is "vrij" een aanname, geen feit. Maar de opgeslagen uitslag kan
        # verouderd zijn — een agenda die tijdens het maken onbereikbaar was, kan
        # nu wél gekoppeld/gedeeld zijn. Toets daarom bij goedkeuren opnieuw en
        # live in plaats van blind op de oude 'error'/'unavailable' te vertrouwen;
        # zo wordt een eerder geblokkeerd voorstel vanzelf goed te keuren zodra de
        # agenda klopt, zonder het opnieuw te hoeven genereren.
        if (r["conflict_checked"] or "ok") != "ok" and start and end:
            fb_status, conflicts = _free_busy_conflict(start, end)
            if conflicts:
                overlap = "; ".join(f"{c['start']}–{c['end']}" for c in conflicts[:2])
                return {"ok": False, "code": "conflict_found", "blocked": True,
                        "error": ("Dit slot overlapt nu met een bestaande afspraak "
                                  f"({overlap}). Verplaats het of kies een ander slot.")}
            if fb_status != "ok":
                from . import service as cal
                access = _run_async(cal.verify_access())
                return {"ok": False, "code": "conflict_unchecked",
                        "blocked": True, "error": (
                    "Dit slot is nog steeds niet tegen je agenda's te controleren, "
                    "dus goedkeuren kan een dubbele boeking opleveren. " +
                    (access["error"] or "Koppel Google Agenda en laat het voorstel opnieuw maken.")
                )}
            # De check slaagt nu en er is geen overlap → leg dat vast en boek door.
            conn.execute(
                "UPDATE calendar_proposals SET conflict_checked='ok' WHERE id=?",
                (proposal_id,))
        elif (r["conflict_checked"] or "ok") != "ok":
            # Kon de opgeslagen tijd niet parsen; nooit blind boeken.
            return {"ok": False, "code": "conflict_unchecked", "blocked": True,
                    "error": "Voorgestelde tijd is onleesbaar; laat het voorstel opnieuw maken."}
        try:
            from ...domains.calendar import service as cal
            if not cal.is_configured():
                return {"ok": False, "error": "Google Agenda niet geconfigureerd"}
            result = _run_async(cal.block_time(
                title=r["title"],
                start=start, end=end,
                description=r["rationale"],
            ))
            conn.execute(
                "UPDATE calendar_proposals SET status='booked', booked_event_id=?, "
                "booked_link=?, decided_at=datetime('now') WHERE id=?",
                (result.get("event_id"), result.get("html_link"), proposal_id),
            )
            return {"ok": True, "event_id": result.get("event_id"),
                    "link": result.get("html_link")}
        except Exception as e:
            log.exception("[agenda-agent] boeken mislukt")
            return {"ok": False, "code": "booking_error", "error": str(e)}


def reject_proposal(proposal_id: int) -> bool:
    with get_conn() as conn:
        conn.execute(
            "UPDATE calendar_proposals SET status='rejected', decided_at=datetime('now') "
            "WHERE id=?", (proposal_id,))
        return True


def pending_proposals() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM calendar_proposals WHERE status='pending_review' "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
