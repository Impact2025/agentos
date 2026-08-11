"""
Google Agenda-integratie voor Agent OS (Fase 1: lezen + blokkeren).

Dit is de Google-backend achter `calendar/service.py` (CALENDAR_BACKEND=
'google', default). Zie service_outlook.py voor de Microsoft Graph-variant —
beide leveren dezelfde functiesignaturen.

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
    CALENDAR_BUSY_CALENDAR_IDS,
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


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    """Synthetische HTTP-fout, zodat explain_error() ook bruikbaar is voor
    fouten die Google in een 200-respons verstopt (freeBusy 'errors')."""
    req = httpx.Request("POST", "https://www.googleapis.com/calendar/v3/freeBusy")
    return httpx.HTTPStatusError(
        str(code), request=req, response=httpx.Response(code, request=req))


def explain_error(exc: Exception, cal_id: Optional[str] = None) -> str:
    """Vertaal een kale Google Calendar-API-fout naar een uitvoerbare melding.

    '404 Not Found' of 'invalid_grant' vertelt Vincent niet wat hij moet doen;
    deze vertaling wél. De scheduler-job gooit hem door, zodat hij leesbaar in
    scheduler_runs en dus het Actiecentrum belandt.

    `cal_id` benoemt de agenda die het probleem geeft — bij conflict-detectie is
    dat een lees-agenda, niet per se de schrijf-agenda.
    """
    text = str(exc)
    email = _client_email() or "het service-account"
    cal = cal_id or _cal_id()
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


async def get_events_range(start: datetime, end: datetime) -> dict:
    """Events mét titel over álle lees-agenda's (agent-agenda + de agenda's uit
    CALENDAR_BUSY_CALENDAR_IDS), voor een dagoverzicht onderweg.

    Bewust ánders dan `get_busy_times`: dáár maakt één onbereikbare agenda het
    hele antwoord ongeldig, want half controleren is precies hoe je dubbel
    boekt. Hier lezen we alleen om te tónen — een agenda die niet meewerkt
    mag de andere niet wegvagen. De onbereikbare agenda's komen daarom apart
    terug in `unreachable`, zodat de telefoon "dit is niet je volledige dag"
    kan zeggen in plaats van een lege dag te suggereren.

    Returns {events: [...], calendars: [...], unreachable: [{id, error}]}.
    """
    if not is_configured():
        return {"events": [], "calendars": [], "unreachable": [],
                "error": "geen Google-credentials ingesteld"}
    cids: List[str] = []
    for cid in [_cal_id()] + _busy_cal_ids():
        if cid and cid not in cids:
            cids.append(cid)

    events: List[dict] = []
    unreachable: List[dict] = []
    for cid in cids:
        try:
            data = await _api(
                "GET",
                f"/calendars/{cid}/events",
                params={
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 100,
                },
            )
        except Exception as e:  # noqa: BLE001
            unreachable.append({"id": cid, "error": explain_error(e, cal_id=cid)[:300]})
            continue
        for ev in data.get("items", []):
            s, en = ev.get("start", {}), ev.get("end", {})
            if ev.get("status") == "cancelled":
                continue
            events.append({
                "id": ev.get("id"),
                "calendar_id": cid,
                "summary": ev.get("summary") or "(geen titel)",
                "start": s.get("dateTime") or s.get("date"),
                "end": en.get("dateTime") or en.get("date"),
                "all_day": "date" in s and "dateTime" not in s,
                "location": ev.get("location") or "",
                "hangout_link": ev.get("hangoutLink") or "",
                "html_link": ev.get("htmlLink") or "",
                "description": (ev.get("description") or "")[:500],
                # Naam+e-mail i.p.v. alleen een telling: de telefoon kan zo
                # tonen wíe er in de afspraak zit. Jezelf (self=true) telt
                # niet mee — dat is geen "deelnemer" om op te letten.
                "attendees": [
                    {"name": a.get("displayName") or a.get("email") or "?",
                     "email": (a.get("email") or "").lower()}
                    for a in (ev.get("attendees") or []) if not a.get("self")
                ],
                # 'Ik heb afgezegd' is geen afspraak meer, maar Google levert
                # hem wél mee — de telefoon moet dat kunnen zien.
                "declined": any(
                    a.get("self") and a.get("responseStatus") == "declined"
                    for a in (ev.get("attendees") or [])),
            })
    events.sort(key=lambda e: str(e.get("start") or ""))
    return {"events": events, "calendars": cids, "unreachable": unreachable}


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


def _busy_cal_ids() -> List[str]:
    """Agenda's die meetellen voor conflict-detectie."""
    return CALENDAR_BUSY_CALENDAR_IDS or [_cal_id()]


async def get_busy_times(start: datetime, end: datetime) -> List[dict]:
    """Free/busy over álle lees-agenda's samen (zie CALENDAR_BUSY_CALENDAR_IDS).

    Eén onbereikbare agenda maakt het hele antwoord ongeldig: een half
    gecontroleerd slot is geen vrij slot, en stilletjes minder controleren is
    precies hoe een dubbele boeking ontstaat.
    """
    cids = _busy_cal_ids()
    data = await _api(
        "POST",
        "/freeBusy",
        json={
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": c} for c in cids],
        },
    )
    cals = data.get("calendars", {})
    busy: List[dict] = []
    for cid in cids:
        entry = cals.get(cid, {})
        # Google meldt geen HTTP-fout als een agenda onbereikbaar is (notFound /
        # geen toegang): je krijgt een lege 'busy' mét een 'errors'-veld. Dat mag
        # nooit als "vrij" gelezen worden.
        if entry.get("errors"):
            reasons = {e.get("reason", "?") for e in entry["errors"]}
            # notFound in een 200-respons betekent hetzelfde als een HTTP-404:
            # het serviceaccount mag deze agenda niet zien. Leen die uitleg,
            # anders staat er 'notFound' zonder dat iemand weet wat de fix is.
            if "notFound" in reasons:
                raise RuntimeError(explain_error(_http_status_error(404), cal_id=cid))
            raise RuntimeError(
                f"free/busy voor agenda '{cid}' mislukt: {', '.join(sorted(reasons))}")
        if cid not in cals:
            raise RuntimeError(
                f"free/busy gaf geen antwoord voor agenda '{cid}'")
        busy.extend({"start": b["start"], "end": b["end"]}
                    for b in entry.get("busy", []))
    return busy


async def verify_access() -> dict:
    """Kan het serviceaccount de agenda's écht lezen?

    `is_configured()` zegt alleen dat er credentials zijn — niet dat een agenda
    bereikbaar is. Zonder dit onderscheid meldt de agenda-agent elk slot als
    vrij terwijl hij in werkelijkheid niets kan zien, en dat is precies hoe je
    een dubbele boeking krijgt.
    Returns {reachable, calendar_id, busy_calendar_ids, error}.
    """
    cid = _cal_id()
    if not is_configured():
        return {"reachable": False, "calendar_id": None,
                "busy_calendar_ids": [], "error": "geen Google-credentials ingesteld"}
    now = datetime.now(timezone.utc)
    try:
        await get_busy_times(now, now + timedelta(days=1))
        return {"reachable": True, "calendar_id": cid,
                "busy_calendar_ids": _busy_cal_ids(), "error": None}
    except Exception as e:
        return {"reachable": False, "calendar_id": cid,
                "busy_calendar_ids": _busy_cal_ids(), "error": str(e)}


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
