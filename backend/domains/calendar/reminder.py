"""Dagelijkse agenda-herinnering per mail (1 dag van tevoren).

Voor elke geboekte afspraak (calendar_proposals.status='booked') waarvan de
start precies morgen valt, én voor wekelijkse blokken (recur_weekday) waarvan
de volgende occurrence morgen is, stuurt Iris een korte herinneringsmail.

Verzending: primair via de gekoppelde Outlook/Office365
(v.munster@weareimpact.nl, Graph /me/sendMail) — de herinnering komt uit je
eigen mailbox. Valt terug op SMTP zodra Graph niet beschikbaar is. Elke
herinnering wordt één keer verstuurd (reminder_sent-vlag), zodat een
terugkerend blok per week één mail krijgt.

Draait via de scheduler (calendar_reminder-job, elke ochtend). Stil als geen
van beide verzendkanalen is geconfigureerd (geen side-effects, geen crash).
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
    """Stuur herinneringen voor morgen. Retourneert aantal verstuurde mails.

    Primair via de gekoppelde Outlook/Office365 (v.munster@weareimpact.nl) —
    de herinnering komt dan uit je eigen mailbox. Valt terug op SMTP zodra
    Graph niet beschikbaar is. Stil als geen van beide is geconfigureerd.
    """
    due = collect_due()
    if not due:
        return 0
    sent = 0
    with get_conn() as conn:
        for item in due:
            try:
                body = build_message(item)
                ok = _send_reminder(item["title"], body)
                if ok:
                    conn.execute(
                        "UPDATE calendar_proposals SET reminder_sent=1 WHERE id=?",
                        (item["id"],))
                    sent += 1
                    log.info("[agenda-reminder] herinnering verstuurd voor %s", item["title"])
            except Exception as e:
                log.warning("[agenda-reminder] kon herinnering niet sturen: %s", e)
    return sent


def _run_async(coro):
    """Draai een coroutine vanuit sync-code (zelfde patroon als calendar/agent)."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _own_address() -> str:
    """Eigen Outlook-adres (v.munster@weareimpact.nl) uit de token-cache."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT email FROM outlook_tokens ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return row["email"] if row and row["email"] else ""
    except Exception:
        return ""


def _send_reminder(title: str, body_md: str) -> bool:
    """Verstuur de herinnering: Outlook Graph eerst, anders SMTP."""
    subject = f"Herinnering: {title}"
    # Outlook Graph (eigen mailbox) — herkbaar, geen aparte SMTP-server.
    try:
        from ...domains.outlook import service as outlook
        if outlook.is_configured():
            to = _own_address() or (outlook._OWN_EMAIL if hasattr(outlook, "_OWN_EMAIL") else "")
            if to:
                res = _run_async(
                    outlook.send_new_email(to, subject, _md_to_html(body_md)))
                if res.get("success"):
                    return True
                log.warning("[agenda-reminder] Graph zond niet: %s", res.get("error"))
    except Exception as e:
        log.warning("[agenda-reminder] Outlook Graph niet beschikbaar: %s", e)
    # Fallback: SMTP (email_service).
    try:
        from ...shared import email_service
        if email_service.is_configured():
            return bool(email_service.send_report(subject, body_md))
    except Exception as e:
        log.warning("[agenda-reminder] SMTP fallback mislukt: %s", e)
    return False


def _md_to_html(md: str) -> str:
    """Minimale markdown->HTML voor de Outlook-body (alleen **vet** + \n)."""
    html = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = html.replace("\n", "<br>")
    import re
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    return f"<p style='font-family:system-ui,sans-serif;font-size:14px'>{html}</p>"
