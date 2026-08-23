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
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ...shared.database import get_conn
from ...shared.mail_text import strip_quoted_history

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
# Hoe ver vooruit een uit vrije tekst geraapte datum nog geloofwaardig is.
# Een afspraak die per mail geregeld wordt ligt weken vooruit, geen seizoenen;
# alles daarbuiten is een misparse (zie de horizon-controle in _parse_datetime).
_MAX_HORIZON_DAGEN = 180

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


# Citaat-stripping (_QUOTE_MARKERS/_strip_quoted_history) verhuisde naar
# shared/mail_text.py — mail/classify.py heeft precies dezelfde knip nodig
# vóórdat het een mail classificeert, en twee losse implementaties van
# "waar begint het citaat" is hoe dit soort fout twee keer wordt gemaakt.
_strip_quoted_history = strip_quoted_history


def extract_appointment(subject: str, body: str, from_addr: str = "") -> Dict:
    """Haal afspraak-gegevens uit een mail. Regelgebaseerd, geen LLM.

    Returns dict met keys: has_time (bool), date (datetime|None),
    duration_min (int), location (str), is_remote (bool), priority (str),
    attendees (list), raw_hints (list).
    """
    text = f"{subject or ''}\n{_strip_quoted_history(body or '')}".lower()
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
    # \b vóór 'waar': anders matcht dat ook midden in 'meerwaarde' of 'waarom'
    # en levert de rest van dié zin als nep-locatie (gemeten 9 aug 2026: "of
    # PootGelukkig voor ons meerwaarde heeft" -> locatie "de heeft").
    loc_m = re.search(r"\b(locatie|adres|plek|waar)\b\s*[:=]?\s*([^\n,.;]{3,60})", text)
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
    # (?<![:.\d]) sluit de minuten van een kloktijd uit: "09:00 uur" matchte
    # zonder deze guard als "00 uur" -> 0 minuten duur (gemeten 9 aug 2026).
    dur_m = re.search(r"(?<![:.\d])(\d+)\s*(uur|uurb|min|minuten|kwartier)", text)
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

    # Horizon-controle. Deze parser leest vrije tekst en pakt élke dag-maand-
    # combinatie die hij tegenkomt — ook eentje die in een lopende zin staat.
    # Staat de genoemde maand vóór de huidige, dan telt de regel hierboven er
    # een jaar bij op; op een nieuwsbriefzin als "op 30 mei presenteerde het
    # bedrijf..." leverde dat in augustus 2026 een afspraakvoorstel voor
    # 30 mei 2027 op (1 aug 2026). Niemand plant tien maanden vooruit per mail.
    # Zó ver weg is het bewijs dat we een zin hebben gelezen die geen afspraak
    # beschrijft — dan liever géén tijd voorstellen (de agent stelt dan zelf
    # een vrij slot voor) dan een verzonnen datum.
    if target > now + timedelta(days=_MAX_HORIZON_DAGEN):
        return None
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
    (geen agenda gekoppeld), 'invalid_range' (begin ≥ eind — geen agendafout)
    of 'error' (check mislukt). Die vier mógen niet op één hoop: een mislukte
    check is géén bewijs van een vrij slot, en juist dat verschil houdt
    dubbele boekingen tegen.
    """
    if end <= start:
        # Google's freeBusy geeft hier een kale 400 Bad Request — dat lijkt op
        # een agenda-koppelingsfout maar is een kapotte tijdsduur (gemeten:
        # voorstel #19 had proposed_start == proposed_end). Nooit de API
        # bellen met een leeg of negatief venster.
        return "invalid_range", []
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
                    body: str, from_name: str = "") -> Optional[Dict]:
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

    # Nooit een 0-minuten-afspraak: Google's freeBusy-endpoint geeft een kale
    # 400 Bad Request zodra timeMin == timeMax, en dat werd tot nu toe gemeld
    # als "kan je agenda's niet controleren, koppel Google Agenda opnieuw" —
    # een op-het-verkeerde-been-zettende diagnose voor wat gewoon een kapotte
    # duur is (gemeten 9-10 aug 2026, voorstel #19: duration_min=0).
    duration = appt["duration_min"] or _DEFAULT_DURATION_MIN
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
        # Wereldklasse: elke afspraak-aanvraag van een onbekende afzender wordt
        # automatisch een prospect in de CRM + Obsidian-vault (geen handwerk).
        # Draait in een eigen try/except zodat een mislukking hier de agenda-rij
        # nooit in gevaar brengt — de afspraak blijft altijd leidend.
        try:
            from ..prospecting import service as prospecting
            svc = prospecting.LeadsService()
            svc.ensure_lead_for_contact(
                from_addr,
                from_name=from_name or "",
                context=(f"Afspraak-voorstel '{subject}' → "
                         f"{start.strftime('%a %d-%m %H:%M')}–{end.strftime('%H:%M')} "
                         f"({appt['location'] or 'locatie onbekend'})."),
                source="agenda-voorstel",
            )
        except Exception as e:
            log.warning("[agenda-agent] lead-capture voor %s mislukt (niet fataal): %s",
                        from_addr, e)
        return {"id": pid, "title": title, "start": start.isoformat(),
                "priority": appt["priority"], "conflict": bool(conflicts)}
    except Exception:
        log.exception("[agenda-agent] kon voorstel niet opslaan")
        return None


def _lokale_overlap(conn, proposal_id: int, start: datetime, end: datetime,
                    recur_weekday: Optional[int]) -> List[sqlite3.Row]:
    """Vergelijk tegen al GEBOEKTE voorstellen in onze eigen tabel — instant en
    consistent, in tegenstelling tot Google's freeBusy die na een schrijving
    even kan achterlopen (gemeten 10 aug 2026: twee voorstellen voor exact
    hetzelfde tijdslot, 8 seconden na elkaar goedgekeurd, allebei geboekt —
    de live-check bij de tweede vond de eerste kennelijk nog niet).

    Wekelijkse blokken bewaren maar één (proposed_start, proposed_end) — de
    eerste week — dus die kunnen niet op absolute datum vergeleken worden.
    Zodra één van de twee kanten terugkerend is, vergelijken we op weekdag +
    tijdstip-op-de-dag; anders op de absolute datum/tijd zelf."""
    rows = conn.execute(
        "SELECT id, title, proposed_start, proposed_end, recur_weekday "
        "FROM calendar_proposals WHERE status='booked' AND id != ?",
        (proposal_id,),
    ).fetchall()
    cand_wd = recur_weekday if recur_weekday is not None and recur_weekday >= 0 else None
    conflicts = []
    for row in rows:
        rs, re_ = _parse_iso(row["proposed_start"]), _parse_iso(row["proposed_end"])
        if not rs or not re_:
            continue
        try:
            row_wd = int(row["recur_weekday"]) if row["recur_weekday"] is not None else -1
        except (TypeError, ValueError):
            row_wd = -1
        row_wd = row_wd if row_wd >= 0 else None
        if cand_wd is not None or row_wd is not None:
            # Eén (of beide) kant is terugkerend: alleen weekdag + tijdstip
            # tellen, de kalenderdatum van de opgeslagen rij is toeval (week 1).
            eff_row_wd = row_wd if row_wd is not None else rs.weekday()
            eff_cand_wd = cand_wd if cand_wd is not None else start.weekday()
            if eff_row_wd != eff_cand_wd:
                continue
            if rs.time() < end.time() and start.time() < re_.time():
                conflicts.append(row)
        elif rs < end and start < re_:
            conflicts.append(row)
    return conflicts


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
        all_day = bool(r["all_day"]) if "all_day" in r.keys() else False
        if start and end:
            try:
                r_wd = int(r["recur_weekday"]) if "recur_weekday" in r.keys() and r["recur_weekday"] is not None else -1
            except (TypeError, ValueError):
                r_wd = -1
            lokale_conflicten = _lokale_overlap(conn, proposal_id, start, end, r_wd)
            if lokale_conflicten:
                namen = "; ".join(f"#{c['id']} '{(c['title'] or '')[:40]}'" for c in lokale_conflicten[:2])
                return {"ok": False, "code": "conflict_found", "blocked": True,
                        "error": (f"Dit slot overlapt met al geboekte voorstel(len) {namen}. "
                                  "Wijs dit af (of het andere) — anders staat hetzelfde moment dubbel.")}
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
            if fb_status == "invalid_range":
                # Geen agendaprobleem: begin ≥ eind (bv. een 0-minuten-parse uit
                # een oudere bug). Niets valt te "koppelen" — het voorstel zelf
                # is kapot en moet opnieuw gemaakt worden.
                return {"ok": False, "code": "invalid_range", "blocked": True,
                        "error": ("Voorgestelde tijd is ongeldig (begin ligt niet vóór "
                                  "eind). Wijs af en laat het voorstel opnieuw maken.")}
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
            # Terugkerend blok? recur_weekday >= 0 markeert een wekelijkse reeks.
            # recur_count telt hoeveel weken (bv. "de komende 6 weken op maandag");
            # -1 = open/eindeloos ("elke dinsdag", "blok alle dinsdagen" — géén
            # expliciet aantal genoemd). We boeken elke week apart in plaats van
            # via één RRULE: dan verschijnt elk blok als een eigen event dat los
            # verplaatst of verwijderd kan worden, en de titel/omschrijving blijft
            # per week identiek.
            #
            # Bug tot 11 aug 2026: recur_weekday wordt door de parser ALLEEN gezet
            # als er expliciet om herhaling is gevraagd (elke/alle/wekelijks, of
            # 'komende N weken') — een losse afspraak krijgt nooit een
            # recur_weekday. Toch behandelde deze functie recur_count=-1 als "dus
            # één losse afspraak", in tegenspraak met nl_command.py's eigen
            # docstring ("None = open/eindeloos"). Gevolg: "blok alle dinsdagen
            # tussen 09.00 en 10.00" boekte precies één dinsdag, niet alle
            # dinsdagen — het tegenovergestelde van de opdracht, zonder foutmelding.
            # Open/eindeloos krijgt nu dezelfde grens als een expliciet genoemd
            # aantal (_MAX_RECUR_COUNT weken, ~een half jaar) in plaats van 1.
            from .nl_command import _MAX_RECUR_COUNT
            recur_wd = r["recur_weekday"] if "recur_weekday" in r.keys() else -1
            recur_n = r["recur_count"] if "recur_count" in r.keys() else -1
            try:
                recur_n = int(recur_n)
            except (TypeError, ValueError):
                recur_n = -1
            if recur_wd is not None and int(recur_wd) >= 0 and recur_n <= 0:
                recur_n = _MAX_RECUR_COUNT
            if recur_wd is not None and int(recur_wd) >= 0 and recur_n >= 1:
                links, event_ids, errors = [], [], []
                for i in range(recur_n):
                    s_i = start + timedelta(days=7 * i)
                    e_i = end + timedelta(days=7 * i)
                    try:
                        res_i = _run_async(cal.block_time(
                            title=r["title"], start=s_i, end=e_i,
                            description=r["rationale"], all_day=all_day,
                        ))
                        if res_i.get("event_id"):
                            event_ids.append(res_i.get("event_id"))
                        if res_i.get("html_link"):
                            links.append(res_i.get("html_link"))
                    except Exception as ex:  # noqa: BLE001
                        log.warning("[agenda-agent] week %s van reeks mislukt: %s", i + 1, ex)
                        errors.append(str(ex))
                if not event_ids:
                    return {"ok": False, "code": "booking_error",
                            "error": f"Geen enkel blok geboekt: {errors[0] if errors else 'onbekende fout'}"}
                conn.execute(
                    "UPDATE calendar_proposals SET status='booked', booked_event_id=?, "
                    "booked_link=?, decided_at=datetime('now') WHERE id=?",
                    (",".join(event_ids), links[0] if links else None, proposal_id),
                )
                msg = f"{len(event_ids)} wekelijkse blokken geboekt"
                if errors:
                    msg += f" ({len(errors)} mislukt)"
                return {"ok": True, "event_id": event_ids[0], "count": len(event_ids),
                        "link": links[0] if links else None, "message": msg}
            # Enkele afspraak.
            result = _run_async(cal.block_time(
                title=r["title"],
                start=start, end=end,
                description=r["rationale"], all_day=all_day,
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


_WD_NAMEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
_MAAND_NAMEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
                "augustus", "september", "oktober", "november", "december"]


def _nl_datum_tijd(dt: datetime) -> str:
    """NL datum+tijd zonder locale-afhankelijkheid (strftime geeft hier Engels)."""
    return f"{_WD_NAMEN[dt.weekday()]} {dt.day} {_MAAND_NAMEN[dt.month - 1]} om {dt.strftime('%H:%M')}"


# ── Bevestiging naar de klant (23 aug 2026) ─────────────────────────────────
# Alleen voorstellen die via klant-Iris op WhatsApp zijn gedaan dragen een
# customer_wa_id (bridge/actions.py:_cmd_calendar_add) — voor mail-voorstellen
# of Vincents eigen agenda-opdrachten is dit veld leeg en doet deze functie
# bewust niets: er is dan geen klant om te melden. Wordt aangeroepen ná zowel
# approve_proposal als reject_proposal, vanuit elk van hun (twee) aanroepers
# (router.py en bridge/actions.py) — best-effort, mag de goedkeur/afwijs-actie
# zelf nooit blokkeren of laten falen.
async def notify_customer_outcome(proposal_id: int, outcome: str) -> None:
    """outcome: 'booked' of 'rejected'."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT customer_wa_id, proposed_start FROM calendar_proposals WHERE id=?",
            (proposal_id,),
        ).fetchone()
    if not row or not row["customer_wa_id"]:
        return
    start = _parse_iso(row["proposed_start"])
    wanneer = _nl_datum_tijd(start) if start else "het voorgestelde moment"
    if outcome == "booked":
        tekst = (f"Je afspraak staat vanaf nu vast: {wanneer}. Wil je ook een "
                 "agenda-uitnodiging? Stuur gerust je e-mailadres, dan zet Vincent 'm erbij.")
    else:
        tekst = (f"Het voorgestelde moment ({wanneer}) past helaas niet bij Vincent. "
                 "Laat gerust weten of een ander moment beter uitkomt.")
    try:
        from ..bridge import service as bridge_service
        ok = await bridge_service.send_whatsapp_to_customer(row["customer_wa_id"], tekst)
        if not ok:
            log.warning("[agenda-agent] bevestiging naar klant %s mislukt (voorstel #%s)",
                        row["customer_wa_id"], proposal_id)
    except Exception:
        log.exception("[agenda-agent] notify_customer_outcome mislukt voor voorstel #%s", proposal_id)
