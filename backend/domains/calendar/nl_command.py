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
    for tm in re.finditer(r"(\d{1,2})[:.](\d{2})", text):
        h, m = int(tm.group(1)), int(tm.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    m2 = re.search(r"(\d{1,2})\s*uur", text)
    if m2:
        h = int(m2.group(1))
        if 0 <= h <= 23:
            return h, 0
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
               r"online meeting|bel|belafspraak|blok|blokkeer|blokker|"
               r"reserveer|schedule|book)\s+", "", t, flags=re.IGNORECASE).strip()
    # 'naar de tandarts' -> 'tandarts'
    m = re.search(r"naar (de |het )?([A-Za-z0-9 ]{2,40})", t, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip().capitalize()
    # 'met Thijs Lenting' -> behoud originele casing
    m = re.search(r"(?:met|en)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", t)
    if m:
        return text.strip()[:80]
    # specifieke termen
    for tok in ("tandarts", "kapper", "huisarts", "fysio", "dentist", "coach", "trainer"):
        if tok in t.lower():
            return tok.capitalize()
    if attendees:
        return f"Afspraak met {attendees[0]}"
    # fallback: eerste hoofdwoord, eerste letter hoofdletter
    words = [w for w in re.findall(r"[A-Za-z]{3,}", t)
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


def _duration_from_text(text: str) -> int:
    dur_m = re.search(r"(\d+)\s*(uur|uurb|min|minuten|kwartier)", text.lower())
    if dur_m:
        n = int(dur_m.group(1))
        unit = dur_m.group(2)
        return (n * 60 if "uur" in unit else 15 if "kwartier" in unit else n)
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
    duration = _duration_from_text(raw)

    # ── Terugkerend blok? ──
    is_recur = any(tok in low for tok in _RECUR_TOKENS) and _weekday_from_text(low) is not None
    if is_recur:
        wd = _weekday_from_text(low)
        # tijdsvenster: "tussen 09.00 en 10.00" of "09.00-10.00" of "09:00 10:00"
        times = re.findall(r"(\d{1,2})[:.](\d{2})", low)
        if len(times) >= 2:
            sh, sm = int(times[0][0]), int(times[0][1])
            eh, em = int(times[1][0]), int(times[1][1])
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                return ParsedCommand(kind="error", title="", raw=raw,
                                     error="Ongeldige tijd in het blok")
            start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
            if end <= start:
                return ParsedCommand(kind="error", title="", raw=raw,
                                     error="Eindtijd ligt vóór begintijd")
            delta_min = int((end - start).total_seconds() // 60)
            title = _infer_title(raw, attendees) or "Geblokkeerd"
            if title.lower() in ("afspraak",):
                title = f"Blok ({_NL_DAYS_inv(wd)})"
            return ParsedCommand(
                kind="recurring", title=f"{title} (wekelijks)",
                start=start, end=end, recur_weekday=wd,
                duration_min=delta_min, is_remote=is_remote,
                attendees=attendees, raw=raw,
            )

    # ── Enkele afspraak ──
    # Datum bepalen: expliciete dag-maand, of weekdag, of 'morgen'/'overmorgen'.
    target_date = _parse_day_month(low, now)
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
