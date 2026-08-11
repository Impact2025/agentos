"""Dagelijkse agenda-herinnering per mail (1 dag van tevoren).

Voor elke geboekte afspraak (calendar_proposals.status='booked') waarvan de
start precies morgen valt, én voor wekelijkse blokken (recur_weekday) waarvan
de volgende occurrence morgen is, stuurt Iris een korte herinneringsmail via
de geconfigureerde SMTP (email_service). Elke herinnering wordt één keer
gestuurd (reminder_sent-vlag), zodat een terugkerend blok per week één mail
krijgt.

Draait via de scheduler (calendar_reminder-job, elke ochtend). Stil als SMTP
niet is geconfigureerd (geen side-effects, geen crash).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

from zoneinfo import ZoneInfo

from ...shared.database import get_conn

log = logging.getLogger(__name__)
_TZ = ZoneInfo("Europe/Amsterdam")

_WD_NL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
          "zaterdag", "zondag"]


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _due_tomorrow(start: datetime, now: datetime) -> bool:
    """Start valt morgen (dezelfde kalenderdag als now+1, in Amsterdam)."""
    tomorrow = (now + timedelta(days=1)).date()
    return start.date() == tomorrow


def collect_due() -> List[Dict]:
    """Geef alle afspraken die morgen moeten en nog geen herinnering kregen."""
    now = datetime.now(timezone.utc).astimezone(_TZ)
    out: List[Dict] = []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, proposed_start, proposed_end, location, "
            "is_remote, recur_weekday, status, reminder_sent "
            "FROM calendar_proposals "
            "WHERE status='booked' AND (reminder_sent IS NULL OR reminder_sent=0)"
        ).fetchall()
    for r in rows:
        start = _parse_iso(r["proposed_start"])
        if not start:
            continue
        # Eenmalige afspraak: start morgen?
        if r["recur_weekday"] is None or r["recur_weekday"] < 0:
            if _due_tomorrow(start, now):
                out.append(dict(r))
            continue
        # Wekelijks blok: ligt de volgende occurrence morgen?
        wd = int(r["recur_weekday"])
        if now.weekday() == (wd - 1) % 7:  # morgen is de geblokkeerde weekdag
            out.append(dict(r))
    return out


def build_message(item: Dict) -> str:
    start = _parse_iso(item["proposed_start"])
    end = _parse_iso(item["proposed_end"])
    t0 = start.strftime("%H:%M") if start else "?"
    t1 = end.strftime("%H:%M") if end else ""
    when = (start.strftime("%A %d %B") if start else "?")
    loc = "Online" if item["is_remote"] else (item["location"] or "locatie onbekend")
    recur = ""
    if item["recur_weekday"] is not None and item["recur_weekday"] >= 0:
        recur = f" (elke {_WD_NL[item['recur_weekday']]})"
    lines = [
        f"# 📅 Herinnering: {item['title']}",
        "",
        f"**Wanneer:** {when} · {t0}–{t1}{recur}",
        f"**Waar:** {loc}",
        "",
        "_Dit is een automatische herinnering van Iris, 1 dag van tevoren._",
    ]
    return "\n".join(lines)


def run_reminders() -> int:
    """Stuur herinneringen voor morgen. Retourneert aantal verstuurde mails."""
    due = collect_due()
    if not due:
        return 0
    sent = 0
    try:
        from ...shared import email_service
        if not email_service.is_configured():
            log.info("[agenda-reminder] SMTP niet geconfigureerd — geen mail verstuurd")
            return 0
    except Exception as e:
        log.warning("[agenda-reminder] email_service niet beschikbaar: %s", e)
        return 0

    with get_conn() as conn:
        for item in due:
            try:
                body = build_message(item)
                ok = email_service.send_report(
                    f"Herinnering: {item['title']}", body)
                if ok:
                    conn.execute(
                        "UPDATE calendar_proposals SET reminder_sent=1 WHERE id=?",
                        (item["id"],))
                    sent += 1
                    log.info("[agenda-reminder] herinnering verstuurd voor %s", item["title"])
            except Exception as e:
                log.warning("[agenda-reminder] kon herinnering niet sturen: %s", e)
    return sent
