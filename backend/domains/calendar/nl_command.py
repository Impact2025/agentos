"""Vrije-tekst / spraak-command parser voor agenda-acties (NL).

Vertaalt een zin als "dinsdag 18 augustus om 12.15 naar de tandarts" of
"Online meeting met Thijs Lenting op 19 augustus 10.00" of "blok alle
dinsdagen tussen 09.00 en 10.00" naar een gestructureerde agenda-actie, met
conflict-controle via de gekoppelde agenda (free/busy).

Twee actietypen:
  * single  — één afspraak op een expliciete datum/tijd.
  * recurring — een wekelijks terugkerend blok (bv. elke dinsdag 09:00-10:00).

Output is een `ParsedCommand` met `kind`, `title`, `start`, `end`,
`location`, `is_remote`, `attendees`, `recur_weekday` (bij recurring), en
`conflict` (na een optionele free/busy-check). De caller (bridge-commando)
schrijft dit naar `calendar_proposals` (review-gate) — NIET direct boeken.

De datum/tijd-parser is een adapter op de bestaande
`agent.extract_appointment` / `_parse_datetime` uit calendar/agent.py, met
extra patronen die typisch zijn voor gesproken commando's (="om 12.15",
"op 19 augustus 10.00", "tussen 09.00 en 10.00").
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/Amsterdam")

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
# Stopwoorden die aangeven dat we met een terugkerend blok te maken hebben.
_RECUR_TOKENS = ("elke", "alle", "ieder", "iedere", "wekelijks", "om de week",
                 "elk", "ieder")
# "de komende 6 weken op maandag" / "de volgende 4 weken op vrijdag" is óók een
# terugkerend blok, maar mét een einde. Zonder dit patroon las de parser alleen
# "op maandag" en maakte er één losse afspraak van (gemeten 11 aug 2026 op de
# zin "Ik wil de komende 6 weken op maandag van 08.30 t/m 10.00 blokken voor
# Focustijd": resultaat was één afspraak van 30 minuten met de titel "Komende").
_RECUR_COUNT_RE = re.compile(
    r"\b(?:de\s+)?(?:komende|volgende|eerstvolgende|aankomende)\s+(\d{1,2})\s*weken\b",
    re.IGNORECASE)
# Hoeveel herhalingen we maximaal in één keer klaarzetten. Een tikfout ("60
# weken") mag geen jaar aan agenda volschrijven.
_MAX_RECUR_COUNT = 26
_DEFAULT_DURATION_MIN = 30
_TRAVEL_BUFFER_MIN = 30
_HOME_BASE_TOKENS = ("thuis", "kantoor", "weareimpact", "impactbox", "philia",
                     "online", "teams", "zoom", "meet", "bellen", "telefoon",
                     "videocall", "videogesprek", "remote", "digitaal")


@dataclass
class ParsedCommand:
    kind: str                       # 'single' | 'recurring'
    title: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    location: str = ""
    is_remote: bool = False
    attendees: List[str] = field(default_factory=list)
    recur_weekday: Optional[int] = None   # 0=ma .. 6=zo (alleen recurring)
    recur_count: Optional[int] = None     # aantal herhalingen (bijv. 6 weken); None = open/eindeloos
    duration_min: int = _DEFAULT_DURATION_MIN
    raw: str = ""
    error: Optional[str] = None
    conflict: Optional[dict] = None       # {'status','overlaps'} na check


def _amsterdam_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(_TZ)


def _parse_time(text: str) -> Optional[tuple]:
    """Eerste geldige kloktijd in de tekst -> (uur, minuut) of None.

    Accepteert '12.15', '12:15', '10 uur', '10.00 uur'.
    """
    gevonden = _parse_time_span(text)
    return (gevonden[0], gevonden[1]) if gevonden else None


def _parse_time_span(text: str) -> Optional[tuple]:
    """Als `_parse_time`, maar geeft ook terug wélk stuk tekst de tijd was:
    (uur, minuut, start_index, eind_index).

    Die positie is niet cosmetisch. `_duration_from_text` zocht op `(\\d+)\\s*uur`
    en las in "morgen om 10 uur tandarts" de kloktijd als duur — de afspraak
    werd 10:00–20:00 (gemeten, 11 aug 2026). De duur mag dus nooit hetzelfde
    stuk tekst gebruiken dat al als tijdstip is opgevat.
    """
    for tm in re.finditer(r"(\d{1,2})[:.](\d{2})", text):
        h, m = int(tm.group(1)), int(tm.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m, tm.start(), tm.end()
    m2 = re.search(r"(\d{1,2})\s*uur", text)
    if m2:
        h = int(m2.group(1))
        if 0 <= h <= 23:
            return h, 0, m2.start(), m2.end()
    return None


def _parse_day_month(text: str, now: datetime) -> Optional[datetime]:
    """'18 augustus', '19 aug', '23-7' -> datum (zonder tijd)."""
    dm = re.search(
        r"(\d{1,2})[\s-]+(jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec|"
        r"januari|februari|maart|april|mei|juni|juli|augustus|september|"
        r"oktober|november|december)", text)
    if dm:
        d, mname = int(dm.group(1)), dm.group(2)
        mnum = _MONTHS.get(mname[:3], 0)
        if mnum:
            yr = now.year + (1 if mnum < now.month else 0)
            try:
                return datetime(yr, mnum, d, tzinfo=_TZ)
            except ValueError:
                return None
    return None


def _weekday_from_text(text: str) -> Optional[int]:
    for dname, dnum in _NL_DAYS.items():
        # match 'dinsdag' én meervoud/afgeleiden ('dinsdagen', 'op dinsdag')
        if re.search(rf"\b{dname}(s|en|se)?\b", text):
            return dnum
    return None


def _next_weekday(target: int, base: Optional[datetime] = None) -> datetime:
    base = base or _amsterdam_now()
    return base + timedelta(days=(target - base.weekday()) % 7)


def _zonder_tijdsinfo(text: str) -> str:
    """Haal datum-, dag-, tijd- en duuraanduidingen uit een zin.

    Wat overblijft is het onderwerp. De titel van een afspraak hoort te zeggen
    wát het is; wannéér staat al in het tijdvak, en twee keer opschrijven maakt
    de agenda op de dag zelf onleesbaar.
    """
    t = text
    t = re.sub(r"\b\d{1,2}[:.]\d{2}\b", " ", t)                      # 12.15
    t = re.sub(r"\b\d{1,2}\s*uur\b", " ", t, flags=re.IGNORECASE)     # 10 uur
    t = re.sub(r"\b\d*\s*(min(uten)?|kwartier)\b", " ", t, flags=re.IGNORECASE)
    maanden = "|".join(sorted(_MONTHS, key=len, reverse=True))
    t = re.sub(rf"\b\d{{1,2}}\s*(?:{maanden})\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\b(?:{maanden})\b", " ", t, flags=re.IGNORECASE)
    dagen = "|".join(_NL_DAYS)
    t = re.sub(rf"\b(?:{dagen})(s|en|se)?\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(morgen|overmorgen|vandaag|om|op|rond|vanaf|tussen|tot)\b", " ",
               t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip(" ,.-")


def _infer_title(text: str, attendees: List[str]) -> str:
    """Bouw een leesbare titel uit de zin.

    'naar de tandarts' -> 'Tandarts'
    'meeting met Thijs Lenting' -> 'Meeting met Thijs Lenting'
    'afspraak met de tandarts' -> 'Afspraak: Tandarts'

    We titel-casen alleen de eerste letter van de gevonden woorden en behouden
    de originele casing (zo blijft 'Thijs Lenting' correct, en wordt niet
    'Thijs Lenting Op 19 Augustus').
    """
    t = text
    # Verwijder commando-woorden vooraan (case-insensitief).
    t = re.sub(r"^(ik wil|ik wil graag|graag|plan|maak|zet|zet een|voeg toe|"
               r"herinner|noteer|spreek|afspraak|afspraak met|meeting|call|"
               r"online meeting|bel|belafspraak|blok|blokkeer|blokker|blokken|"
               r"reserveer|schedule|book|de komende|de volgende|de eerstvolgende|"
               r"aankomende|weken|de)\s+", "", t, flags=re.IGNORECASE).strip()
    # "voor Focustijd" → het onderwerp ná 'voor' is de afspraaktitel (blijft
    # Title-Case, dus "Focustijd" blijft "Focustijd"). Zonder dit werd op de zin
    # "blok ... voor Focustijd" de titel "Komende" (gemeten 11 aug 2026).
    m = re.search(r"\bvoor\s+([A-Za-z0-9 ]{2,40})$", t, flags=re.IGNORECASE)
    if m:
        subj = m.group(1).strip()
        subj = subj[:1].upper() + subj[1:] if subj else subj
        return subj
    # 'naar de tandarts' -> 'tandarts'
    m = re.search(r"naar (de |het )?([A-Za-z0-9 ]{2,40})", t, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip().capitalize()
    # 'met Thijs Lenting' -> behoud originele casing
    m = re.search(r"(?:met|en)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", t)
    if m:
        # Wél de hele zin als titel (die draagt de context), maar zónder de
        # datum/tijd: die staat al in het tijdvak van de afspraak, en
        # "donderdag om 9 uur bellen met Marieke" als titel in je agenda is
        # onleesbaar op de dag zelf.
        kaal = ((_zonder_tijdsinfo(t) or t)).strip()[:80]
        # Alleen de eerste letter omhoog, niet `.capitalize()`: die maakt van
        # 'Thijs Lenting' een 'thijs lenting'.
        return kaal[:1].upper() + kaal[1:]
    # specifieke termen
    for tok in ("tandarts", "kapper", "huisarts", "fysio", "dentist", "coach", "trainer"):
        if tok in t.lower():
            return tok.capitalize()
    if attendees:
        return f"Afspraak met {attendees[0]}"
    # fallback: eerste hoofdwoord, eerste letter hoofdletter. Maand- en
    # dagnamen tellen niet mee als onderwerp — "5 augustus 14.00 evaluatie"
    # kreeg anders de titel 'Augustus' (gemeten, 11 aug 2026).
    words = [w for w in re.findall(r"[A-Za-z]{3,}", _zonder_tijdsinfo(t))
             if w.lower() not in ("een", "met", "voor", "naar", "op", "om", "de",
                                   "het", "en", "van", "in", "bij", "tussen", "alle")]
    if words:
        return words[0].capitalize()
    return "Afspraak"


def _extract_attendees(text: str) -> List[str]:
    """'met Thijs Lenting', 'met Thijs Lenting en Marie' -> ['Thijs Lenting', ...]"""
    out = []
    for m in re.finditer(r"\bmet\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text):
        out.append(m.group(1).strip())
    # ook "meeting Thijs Lenting" zonder 'met'
    if not out:
        m = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", text)
        if m and m.group(1).lower() not in ("Online meeting", "Teams meeting"):
            out.append(m.group(1).strip())
    return out


def _is_remote(text: str) -> bool:
    return any(t in text.lower() for t in (
        "online", "teams", "zoom", "meet", "videocall", "videogesprek",
        "remote", "digitaal", "skype", "belcall", "telefonisch", "bellen"))


# Woorden die vóór een getal aangeven dat het een tijdstip is, geen duur.
# "om 10 uur" is een moment; "10 uur lang" een duur. Zonder dit onderscheid
# wordt de meest gewone Nederlandse formulering een afspraak van tien uur.
_TIJDSTIP_VOORZETSELS = ("om", "rond", "vanaf", "tegen", "tussen", "tot", "na", "voor")


def _duration_from_text(text: str, tijd_span: Optional[tuple] = None) -> int:
    """Hoe lang de afspraak duurt. `tijd_span` is het stuk tekst dat al als
    kloktijd is gelezen — dat mag nooit óók de duur zijn (zie _parse_time_span).

    'kwartier' en 'min(uten)' zijn ondubbelzinnig duur. 'uur' is dat niet: dat
    telt alleen als duur wanneer er geen tijdstip-voorzetsel vóór staat en het
    getal niet al het tijdstip ís.
    """
    low = text.lower()
    for dur_m in re.finditer(r"(\d+)\s*(uur|min|minuten|kwartier)\b", low):
        n, unit = int(dur_m.group(1)), dur_m.group(2)
        if tijd_span and dur_m.start() < tijd_span[1] and dur_m.end() > tijd_span[0]:
            continue  # dit getal is het tijdstip zelf
        if unit == "uur":
            ervoor = low[max(0, dur_m.start() - 12):dur_m.start()]
            if any(re.search(rf"\b{w}\s*$", ervoor) for w in _TIJDSTIP_VOORZETSELS):
                continue  # "om 10 uur" — een moment, geen duur
        return n * 60 if unit == "uur" else 15 if unit == "kwartier" else n
    return _DEFAULT_DURATION_MIN


def parse_command(text: str) -> ParsedCommand:
    """Hoofd-entry: parseer een vrije NL-zin (of spraaktranscript) naar een
    agenda-actie. Geeft altijd een ParsedCommand terug; bij een parse-fout
    staat `error` gevuld en `kind='error'`."""
    raw = (text or "").strip()
    if not raw:
        return ParsedCommand(kind="error", title="", raw=raw,
                             error="Lege opdracht")
    low = raw.lower()
    now = _amsterdam_now()

    attendees = _extract_attendees(raw)
    is_remote = _is_remote(raw)
    tijd_span = _parse_time_span(low)
    duration = _duration_from_text(raw, tijd_span)

    # ── Terugkerend blok? ──
    # Twee vormen:
    #  • open herhaling: "elke maandag 09.00-10.00"
    #  • eindige reeks:  "de komende 6 weken op maandag 08.30 t/m 10.00"
    recur_count = None
    cm = _RECUR_COUNT_RE.search(low)
    has_weekday = _weekday_from_text(low) is not None
    is_recur = (any(tok in low for tok in _RECUR_TOKENS) and has_weekday) or \
               (cm is not None and has_weekday)
    if cm:
        n = int(cm.group(1))
        recur_count = min(n, _MAX_RECUR_COUNT)
    if is_recur:
        wd = _weekday_from_text(low)
        # Tijdsvenster. Drie schrijfwijzen moeten allemaal werken:
        #   "tussen 09.00 en 10.00", "van 08.30 t/m 10.00", "08.00-09.00".
        # 1) "van 08.30 t/m 10.00" / "tussen 09.00 en 10.00" — ≥2 kloktijden.
        # 2) één kloktijd + een aparte duur ("09.00 voor 90 minuten") — hier
        #    niet nodig, maar de generieke duration-haalder mag niet de tweede
        #    tijd van het venster opslokken.
        times = re.findall(r"(\d{1,2})[:.](\d{2})", low)
        if len(times) >= 2:
            sh, sm = int(times[0][0]), int(times[0][1])
            eh, em = int(times[1][0]), int(times[1][1])
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                return ParsedCommand(kind="error", title="", raw=raw,
                                     error="Ongeldige tijd in het blok")
            # Het eerste vóórkomen van de gevraagde weekdag, niet vandaag.
            # `now.replace(...)` zette een blok voor "alle vrijdagen" op de dag
            # van invoeren (gemeten: dinsdag), waardoor óók de conflictcontrole
            # de verkeerde dag bekeek — een blok dat botst leest dan als vrij.
            dag = _next_weekday(wd, now).replace(hour=0, minute=0, second=0, microsecond=0)
            start = dag.replace(hour=sh, minute=sm)
            end = dag.replace(hour=eh, minute=em)
            if end <= now:
                start += timedelta(days=7)
                end += timedelta(days=7)
            if end <= start:
                return ParsedCommand(kind="error", title="", raw=raw,
                                     error="Eindtijd ligt vóór begintijd")
            delta_min = int((end - start).total_seconds() // 60)
            title = _infer_title(raw, attendees) or "Geblokkeerd"
            if title.lower() in ("afspraak", "komende", "volgende"):
                title = f"Blok ({_NL_DAYS_inv(wd)})"
            return ParsedCommand(
                kind="recurring", title=f"{title} (wekelijks)",
                start=start, end=end, recur_weekday=wd,
                recur_count=recur_count,
                duration_min=delta_min, is_remote=is_remote,
                attendees=attendees, raw=raw,
            )

    # ── Enkele afspraak ──
    # Datum bepalen: expliciete dag-maand, of weekdag, of 'morgen'/'overmorgen'.
    target_date = _parse_day_month(low, now)
    expliciete_datum = target_date is not None
    if target_date is None:
        wd = _weekday_from_text(low)
        if wd is not None:
            target_date = _next_weekday(wd, now)
        elif "overmorgen" in low:
            target_date = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif "morgen" in low:
            target_date = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    t = _parse_time(low)
    if target_date is None:
        # geen datum en geen weekdag -> we kunnen niet plannen
        return ParsedCommand(
            kind="error", title="", raw=raw,
            error="Ik kon geen datum herkennen (bv. 'dinsdag 18 augustus' "
                  "of 'morgen 14.00').")

    base = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if t:
        base = base.replace(hour=t[0], minute=t[1])
    else:
        base = base.replace(hour=10, minute=0)  # default 10:00

    if base < now:
        if expliciete_datum:
            # Een genoemde datum verschuiven is stil de opdracht veranderen:
            # "5 augustus 14.00" werd op 11 augustus een voorstel voor de 12e
            # (+7 dagen), en dat las als een geslaagde invoer. De +7-regel is
            # bedoeld voor "dinsdag" — een weekdag zónder datum betekent
            # vanzelfsprekend de eerstvolgende. Een datum betekent díe datum.
            return ParsedCommand(
                kind="error", title="", raw=raw,
                error=(f"{base.day} {_maandnaam(base.month)} {base:%H:%M} is al geweest — "
                       f"noem een datum in de toekomst."),
            )
        # weekdag in het verleden -> schuif een week op
        base = base + timedelta(days=7)

    end = base + timedelta(minutes=duration)
    title = _infer_title(raw, attendees) or "Afspraak"
    title = title[:80]

    return ParsedCommand(
        kind="single", title=title, start=base, end=end,
        is_remote=is_remote, attendees=attendees,
        duration_min=duration, raw=raw,
    )


def _maandnaam(num: int) -> str:
    """Nederlandse maandnaam. Bewust niet `strftime('%B')`: dat volgt de locale
    van de machine en geeft op deze server 'August'."""
    for naam, n in _MONTHS.items():
        if n == num and len(naam) > 3:
            return naam
    return str(num)


def _NL_DAYS_inv(num: int) -> str:
    for k, v in _NL_DAYS.items():
        if v == num:
            return k
    return ""


def check_conflict(cmd: ParsedCommand) -> ParsedCommand:
    """Vraag free/busy op voor het venster en zet cmd.conflict."""
    if cmd.kind not in ("single", "recurring") or not cmd.start or not cmd.end:
        return cmd
    try:
        from ...domains.calendar import service as cal
        if not cal.is_configured():
            cmd.conflict = {"status": "unavailable", "overlaps": []}
            return cmd
        busy = _run_async(cal.get_busy_times(cmd.start, cmd.end))
        overlaps = []
        for b in busy:
            bs = _parse_iso(b.get("start"))
            be = _parse_iso(b.get("end"))
            if bs and be and bs < cmd.end and be > cmd.start:
                overlaps.append({"start": b.get("start"), "end": b.get("end")})
        cmd.conflict = {"status": "ok", "overlaps": overlaps}
    except Exception as e:  # noqa: BLE001
        log.warning("[calendar-nl] conflict-check mislukt: %s", e)
        cmd.conflict = {"status": "error", "overlaps": []}
    return cmd


def _run_async(coro):
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
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
