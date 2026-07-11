"""
Google Agenda-integratie voor Agent OS (Fase 1: lezen + blokkeren).

Auth: hergebruikt het bestaande Google-serviceaccount (GSC/GA4). Voor een
persoonlijke/Workspace-agenda zet je CALENDAR_SERVICE_ACCOUNT_PATH op een
EINAAR-account dat lees/schrijfrechten heeft op de agenda (of via Domain-Wide
Delegation met CALENDAR_SUB=impersonatie-adres). Zonder credentials is
is_configured() == False en blijven alle calls veilig (geen side-effects).

Scope: https://www.googleapis.com/auth/calendar (volledig, zodat blokkeren
en het ochtendrapport-inkijkje allebei werken).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from ...shared.config import (
    CALENDAR_CALENDAR_ID,
    CALENDAR_CLIENT_EMAIL,
    CALENDAR_PRIVATE_KEY,
    CALENDAR_SERVICE_ACCOUNT_PATH,
    CALENDAR_SUB,
    GSC_SERVICE_ACCOUNT_PATH,
)
from ...shared.database import get_conn

log = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def is_configured() -> bool:
    """True als er credentials geleverd zijn (inline OF json-bestand)."""
    if CALENDAR_CLIENT_EMAIL and CALENDAR_PRIVATE_KEY:
        return True
    return bool(CALENDAR_SERVICE_ACCOUNT_PATH or GSC_SERVICE_ACCOUNT_PATH)


def _creds():
    """Build google.oauth2 service-account credentials (lazy import).

    Ondersteunt twee leveringen:
      A) Inline: CALENDAR_CLIENT_EMAIL + CALENDAR_PRIVATE_KEY
      B) JSON-bestand: CALENDAR_SERVICE_ACCOUNT_PATH (default GSC/GA4-JSON)
    Bij Domain-Wide Delegation (CALENDAR_SUB) handelen we namens de eigenaar.
    """
    from google.oauth2 import service_account

    # A) Inline credentials (kopie uit WeAreImpact .env.local)
    if CALENDAR_CLIENT_EMAIL and CALENDAR_PRIVATE_KEY:
        info = {
            "type": "service_account",
            "project_id": "weareimpact-482912",
            "private_key": CALENDAR_PRIVATE_KEY,
            "client_email": CALENDAR_CLIENT_EMAIL,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    else:
        # B) JSON-bestand
        path = CALENDAR_SERVICE_ACCOUNT_PATH or GSC_SERVICE_ACCOUNT_PATH
        if not path:
            raise RuntimeError("Geen Google Calendar-serviceaccount geconfigureerd")
        creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)

    if CALENDAR_SUB:
        # Domain-Wide Delegation: handel namens de eigenaar (persoonlijke agenda).
        creds = creds.with_subject(CALENDAR_SUB)
    return creds


def _token() -> str:
    """Haal een OAuth2-access-token op uit de serviceaccount-credentials."""
    creds = _creds()
    if not creds.valid:
        from google.auth.transport.requests import Request

        creds.refresh(Request())
    return creds.token


def _cal_id() -> str:
    return CALENDAR_CALENDAR_ID or "primary"


def _client_email() -> str:
    """Het service-account-adres — nodig in foutmeldingen ('deel je agenda met X')."""
    if CALENDAR_CLIENT_EMAIL:
        return CALENDAR_CLIENT_EMAIL
    path = CALENDAR_SERVICE_ACCOUNT_PATH or GSC_SERVICE_ACCOUNT_PATH
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("client_email", "")
        except Exception:
            pass
    return ""


def explain_error(exc: Exception) -> str:
    """Vertaal een kale Google Calendar-API-fout naar een uitvoerbare melding.

    '404 Not Found' of 'invalid_grant' vertelt Vincent niet wat hij moet doen;
    deze vertaling wél. De scheduler-job gooit hem door, zodat hij leesbaar in
    scheduler_runs en dus het Actiecentrum belandt.
    """
    text = str(exc)
    email = _client_email() or "het service-account"
    cal = _cal_id()
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return (
                f"Agenda '{cal}' is niet zichtbaar voor {email}. Deel de agenda in "
                f"Google Agenda: Instellingen van de agenda → 'Delen met specifieke "
                f"personen' → voeg {email} toe met 'Wijzigingen aanbrengen in afspraken'."
            )
        if code == 403:
            return (
                f"Geen rechten op agenda '{cal}' voor {email} (403). Zet het account in "
                f"de deel-instellingen op 'Wijzigingen aanbrengen in afspraken'."
            )
    if "invalid_grant" in text:
        return (
            "Google weigert de impersonatie (invalid_grant). CALENDAR_SUB vereist "
            "Domain-Wide Delegation in de Google Workspace-admin; zonder DWD: laat "
            f"CALENDAR_SUB weg uit .env en deel agenda '{cal}' direct met {email}."
        )
    return text


async def _api(method: str, path: str, **kwargs) -> dict:
    token = _token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if method in ("POST", "PATCH", "PUT"):
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, f"{CALENDAR_API}{path}", headers=headers, **kwargs
        )
        resp.raise_for_status()
        if resp.status_code in (204, 202) or not resp.content.strip():
            return {}
        return resp.json()


# ── Lezen ──────────────────────────────────────────────────────────────────

async def get_week_events(week_start: Optional[str] = None) -> List[dict]:
    """Haal events voor de week die begint op `week_start` (YYYY-MM-DD, default
    deze week maandag). Gecachte kopie wordt bijgewerkt in calendar_events."""
    now = datetime.now(timezone.utc)
    if week_start:
        start = datetime.fromisoformat(week_start).replace(
            hour=0, minute=0, second=0, tzinfo=timezone.utc
        )
    else:
        # Maandag van deze week
        monday = now - timedelta(days=now.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    data = await _api(
        "GET",
        f"/calendars/{_cal_id()}/events",
        params={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 100,
        },
    )
    items = data.get("items", [])

    events = []
    for ev in items:
        start_info = ev.get("start", {})
        end_info = ev.get("end", {})
        events.append(
            {
                "id": ev.get("id"),
                "summary": ev.get("summary") or "(geen titel)",
                "start": start_info.get("dateTime") or start_info.get("date"),
                "end": end_info.get("dateTime") or end_info.get("date"),
                "all_day": "date" in start_info and "dateTime" not in start_info,
                "location": ev.get("location"),
                "hangout_link": (ev.get("hangoutLink")),
                "html_link": ev.get("htmlLink"),
            }
        )

    _cache_events(events)
    return events


def _cache_events(events: List[dict]) -> None:
    """Schrijf de vers gehaalde events naar de lokale cache (idempotent)."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            for ev in events:
                conn.execute(
                    """
                    INSERT INTO calendar_events
                        (event_id, summary, start_at, end_at, all_day, location,
                         hangout_link, html_link, synced_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        summary    = excluded.summary,
                        start_at   = excluded.start_at,
                        end_at     = excluded.end_at,
                        all_day    = excluded.all_day,
                        location   = excluded.location,
                        hangout_link = excluded.hangout_link,
                        html_link  = excluded.html_link,
                        synced_at  = excluded.synced_at
                    """,
                    (
                        ev["id"],
                        ev["summary"],
                        ev["start"],
                        ev["end"],
                        1 if ev["all_day"] else 0,
                        ev.get("location") or "",
                        ev.get("hangout_link") or "",
                        ev.get("html_link") or "",
                        now,
                    ),
                )
    except Exception as e:
        log.warning(f"Calendar-cache bijwerken mislukt: {e}")


async def get_busy_times(start: datetime, end: datetime) -> List[dict]:
    """Free/busy-query — handig voor conflict-detectie met geplande jobs."""
    data = await _api(
        "POST",
        "/freeBusy",
        json={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": _cal_id()}],
        },
    )
    busy = data.get("calendars", {}).get(_cal_id(), {}).get("busy", [])
    return [
        {"start": b["start"], "end": b["end"]}
        for b in busy
    ]


# ── Schrijven ──────────────────────────────────────────────────────────────

async def block_time(
    title: str,
    start: datetime,
    end: datetime,
    description: str = "",
    calendar_id: Optional[str] = None,
) -> dict:
    """Blokkeer een tijdslot in de agenda (transparantie = busy)."""
    cid = calendar_id or _cal_id()
    body = {
        "summary": title,
        "description": description or "Geblokkeerd via Agent OS",
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "Europe/Amsterdam",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "Europe/Amsterdam",
        },
        "transparency": "opaque",
    }
    result = await _api("POST", f"/calendars/{cid}/events", json=body)
    return {
        "success": True,
        "event_id": result.get("id"),
        "html_link": result.get("htmlLink"),
    }


async def get_today_summary() -> str:
    """Korte Nederlandse samenvatting van vandaag — voor Iris' briefing."""
    today = datetime.now(timezone.utc).date()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    events = await get_week_events(week_start=None)
    todays = [
        e
        for e in events
        if e["start"] and e["start"][:10] == today.isoformat()
    ]
    if not todays:
        return "Je agenda is vandaag leeg — mooi, tijd voor dieptewerk of acquisitie."
    lines = [f"Je hebt vandaag {len(todays)} afspraak(en):"]
    for e in sorted(todays, key=lambda x: x["start"] or ""):
        t = e["start"][11:16] if e["start"] and "T" in e["start"] else "hele dag"
        lines.append(f"- {t}: {e['summary']}")
    return "\n".join(lines)
