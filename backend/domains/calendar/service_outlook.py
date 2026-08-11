"""
Microsoft Outlook / Graph Agenda-integratie voor Agent OS.

Dit is de Outlook-backend achter `calendar/service.py` (CALENDAR_BACKEND=
'outlook') — voor klanten die zelf Outlook gebruiken (bv. Nicole @ WE SHAPE
THE FUTURE) in plaats van het Google-serviceaccount-model van de
hoofdinstallatie (zie service_google.py). Beide leveren dezelfde
functiesignaturen, dus de agenda-agent en alle aanroepers (bridge/context.py,
scheduler.py, iris/service.py, health/router.py) blijven ongewijzigd.

Auth: hergebruikt de delegated OAuth-login van de mail-koppeling
(outlook/service.py, MSAL device-code-flow, geen client secret) — de
Azure-app-registratie draagt daarom óók Calendars.ReadWrite naast de
Mail-scopes. Wie al bij Mail is ingelogd hoeft niet apart in te loggen voor
de agenda; het is dezelfde token-cache. Een bestaand token dat vóór deze
uitbreiding is aangevraagd dekt de agenda-scope niet met terugwerkende
kracht — opnieuw device-code inloggen via Instellingen → Outlook is dan nodig.

Waarom niet het Google-model (gedeelde agenda + service-account)? Dat vergt
dat de klant haar agenda deelt met een service-account-adres dat ze niet
kent, en werkt sowieso niet voor een Outlook-agenda. Hier logt ze zelf in bij
haar eigen Microsoft-account — haar eigen omgeving, geen gedeeld systeem.

Verschil met het Google-model: daar tellen meerdere lees-agenda's mee
(CALENDAR_BUSY_CALENDAR_IDS) omdat het service-account over meerdere
agenda's kan meekijken. Hier is er precies één agenda — die van de
ingelogde gebruiker — dus busy_calendar_ids is altijd één item (haar e-mail).

Tijdzone: elke Graph-call vraagt expliciet UTC op via de Prefer-header
('outlook.timezone="UTC"'). Zonder die header levert Graph tijden terug in
de tijdzone van de mailbox-instellingen, wat vergelijken met andere
(al-UTC) tijdstempels in dit systeem stilletjes fout zou laten lopen zodra
die instelling ooit afwijkt van Europe/Amsterdam.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from ..outlook import service as outlook_service
from .service_google import _cache_events

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_PREFER_UTC = {"Prefer": 'outlook.timezone="UTC"'}


def is_configured() -> bool:
    """OUTLOOK_CLIENT_ID gezet — of er ook al ingelogd is, zegt verify_access()."""
    return outlook_service.is_configured()


def client_email() -> str:
    """Geen service-account bij deze backend — de agenda is de eigen OAuth-
    login (dezelfde koppeling als mail), er is niets om mee te delen."""
    return ""


def explain_error(exc: Exception, cal_id: Optional[str] = None) -> str:
    """Vertaal een kale Graph-fout naar een uitvoerbare melding, zelfde rol
    als service_google.explain_error() — de scheduler-job en het Actiecentrum
    verwachten van elke backend een leesbare uitleg, geen kale statuscode."""
    text = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return (
                "Outlook-sessie verlopen of nog niet ingelogd voor de agenda. "
                "Log opnieuw in via Instellingen → Outlook (device-code-login) — "
                "dezelfde login geeft ook toegang tot de agenda."
            )
        if code == 403:
            return (
                "Geen agenda-rechten (403). De Azure-app-registratie mist de "
                "Calendars.ReadWrite-scope, of een bestaand token is vóór het "
                "toevoegen daarvan aangevraagd. Voeg de scope toe in Azure en "
                "log opnieuw in via Instellingen → Outlook."
            )
    if "Niet geauthenticeerd" in text:
        return (
            "Nog niet ingelogd bij Outlook. Log in via Instellingen → Outlook; "
            "dezelfde login dekt ook de agenda."
        )
    return text


async def _graph(method: str, path: str, token: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **_PREFER_UTC,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, f"{GRAPH_BASE}{path}", headers=headers, **kwargs)
        resp.raise_for_status()
        if resp.status_code in (204, 202) or not resp.content.strip():
            return {}
        return resp.json()


def _require_token() -> str:
    token = outlook_service.get_valid_token()
    if not token:
        raise RuntimeError("Niet geauthenticeerd bij Microsoft (agenda)")
    return token


async def _list_events(start: datetime, end: datetime) -> List[dict]:
    """Ruwe Graph-events in [start, end), inclusief geannuleerde/afwijzende
    (die worden door de aanroepers gefilterd — get_busy_times sluit ze uit,
    get_events_range toont 'declined' juist expliciet)."""
    token = _require_token()
    events: List[dict] = []
    url = "/me/calendarView"
    params: Optional[dict] = {
        "startDateTime": start.isoformat(),
        "endDateTime": end.isoformat(),
        "$orderby": "start/dateTime",
        "$top": "100",
        "$select": (
            "id,subject,start,end,isAllDay,location,onlineMeeting,"
            "onlineMeetingUrl,webLink,bodyPreview,attendees,isCancelled,"
            "showAs,responseStatus"
        ),
    }
    while url:
        data = await _graph("GET", url, token, params=params)
        events.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        # Vanaf de tweede pagina zit de querystring al in next_link zelf —
        # params opnieuw meesturen zou '$top'/'startDateTime' dubbel opgeven.
        url = next_link.replace(GRAPH_BASE, "") if next_link else ""
        params = None
    return events


# ── Lezen ──────────────────────────────────────────────────────────────────

async def get_week_events(week_start: Optional[str] = None) -> List[dict]:
    now = datetime.now(timezone.utc)
    if week_start:
        start = datetime.fromisoformat(week_start).replace(
            hour=0, minute=0, second=0, tzinfo=timezone.utc)
    else:
        monday = now - timedelta(days=now.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    raw = await _list_events(start, end)
    events = []
    for ev in raw:
        if ev.get("isCancelled"):
            continue
        s, e = ev.get("start", {}), ev.get("end", {})
        events.append({
            "id": ev.get("id"),
            "summary": ev.get("subject") or "(geen titel)",
            "start": s.get("dateTime"),
            "end": e.get("dateTime"),
            "all_day": bool(ev.get("isAllDay")),
            "location": (ev.get("location") or {}).get("displayName") or "",
            "hangout_link": ev.get("onlineMeetingUrl") or "",
            "html_link": ev.get("webLink") or "",
        })
    _cache_events(events)
    return events


async def get_events_range(start: datetime, end: datetime) -> dict:
    """Zelfde vorm als service_google.get_events_range — één agenda hier, dus
    `unreachable` is leeg of bevat precies één entry (de eigen agenda)."""
    if not is_configured():
        return {"events": [], "calendars": [], "unreachable": [],
                "error": "OUTLOOK_CLIENT_ID niet ingesteld"}
    info = outlook_service.get_account_info()
    cid = (info or {}).get("email") or "me"
    try:
        raw = await _list_events(start, end)
    except Exception as e:  # noqa: BLE001
        return {"events": [], "calendars": [cid],
                "unreachable": [{"id": cid, "error": explain_error(e)[:300]}]}

    events: List[dict] = []
    for ev in raw:
        if ev.get("isCancelled"):
            continue
        s, e_ = ev.get("start", {}), ev.get("end", {})
        events.append({
            "id": ev.get("id"),
            "calendar_id": cid,
            "summary": ev.get("subject") or "(geen titel)",
            "start": s.get("dateTime"),
            "end": e_.get("dateTime"),
            "all_day": bool(ev.get("isAllDay")),
            "location": (ev.get("location") or {}).get("displayName") or "",
            "hangout_link": ev.get("onlineMeetingUrl") or "",
            "html_link": ev.get("webLink") or "",
            "description": (ev.get("bodyPreview") or "")[:500],
            "attendees": [
                {"name": (a.get("emailAddress") or {}).get("name") or (a.get("emailAddress") or {}).get("address") or "?",
                 "email": ((a.get("emailAddress") or {}).get("address") or "").lower()}
                for a in (ev.get("attendees") or [])
                if ((a.get("emailAddress") or {}).get("address") or "").lower() != (info or {}).get("email", "").lower()
            ],
            "declined": (ev.get("responseStatus") or {}).get("response") == "declined",
        })
    events.sort(key=lambda ev: str(ev.get("start") or ""))
    return {"events": events, "calendars": [cid], "unreachable": []}


async def get_busy_times(start: datetime, end: datetime) -> List[dict]:
    """Busy-blokken uit de eigen agenda (showAs busy/tentative/oof, niet
    geannuleerd) — het Outlook-equivalent van Google's freeBusy-endpoint.
    Net als service_google: faalt de check, dan een exception (nooit stil
    'vrij'), want een half gecontroleerd slot is geen vrij slot."""
    raw = await _list_events(start, end)
    busy: List[dict] = []
    for ev in raw:
        if ev.get("isCancelled"):
            continue
        if ev.get("showAs") not in ("busy", "tentative", "oof"):
            continue
        if (ev.get("responseStatus") or {}).get("response") == "declined":
            continue
        s, e = ev.get("start", {}).get("dateTime"), ev.get("end", {}).get("dateTime")
        if s and e:
            busy.append({"start": s, "end": e})
    return busy


async def verify_access() -> dict:
    """Kan de eigen agenda écht gelezen worden? Los van is_configured() (dat
    zegt alleen dat er een app-registratie is), zelfde onderscheid als
    service_google — 'geconfigureerd' bewijst geen bereikbaarheid."""
    if not is_configured():
        return {"reachable": False, "calendar_id": None,
                "busy_calendar_ids": [], "error": "OUTLOOK_CLIENT_ID niet ingesteld"}
    if not outlook_service.is_authenticated():
        return {"reachable": False, "calendar_id": None, "busy_calendar_ids": [],
                "error": "Nog niet ingelogd bij Outlook — koppel via Instellingen → Outlook (device-code-login)."}
    now = datetime.now(timezone.utc)
    try:
        await _list_events(now, now + timedelta(days=1))
        info = outlook_service.get_account_info()
        cid = (info or {}).get("email") or "me"
        return {"reachable": True, "calendar_id": cid,
                "busy_calendar_ids": [cid], "error": None}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "calendar_id": None,
                "busy_calendar_ids": [], "error": explain_error(e)}


# ── Schrijven ──────────────────────────────────────────────────────────────

async def block_time(
    title: str,
    start: datetime,
    end: datetime,
    description: str = "",
    calendar_id: Optional[str] = None,
    all_day: bool = False,
) -> dict:
    """Blokkeer een tijdslot in de eigen Outlook-agenda. `calendar_id` bestaat
    alleen voor signatuur-compatibiliteit met service_google — Outlook-auth is
    per persoon (delegated), er is hier maar één schrijfbare agenda: 'me'."""
    token = _require_token()
    body = {
        "subject": title,
        "body": {"contentType": "Text", "content": description or "Geblokkeerd via Agent OS"},
        "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        "showAs": "busy",
        "isAllDay": bool(all_day),
    }
    result = await _graph("POST", "/me/events", token, json=body)
    return {
        "success": True,
        "event_id": result.get("id"),
        "html_link": result.get("webLink"),
    }


async def get_today_summary() -> str:
    """Korte Nederlandse samenvatting van vandaag — voor Iris' briefing."""
    today = datetime.now(timezone.utc).date()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    raw = await _list_events(start, end)
    todays = [ev for ev in raw if not ev.get("isCancelled")]
    if not todays:
        return "Je agenda is vandaag leeg — mooi, tijd voor dieptewerk of acquisitie."
    lines = [f"Je hebt vandaag {len(todays)} afspraak(en):"]
    for ev in sorted(todays, key=lambda e: str(e.get("start", {}).get("dateTime") or "")):
        s = (ev.get("start") or {}).get("dateTime") or ""
        t = s[11:16] if "T" in s else "hele dag"
        lines.append(f"- {t}: {ev.get('subject') or '(geen titel)'}")
    return "\n".join(lines)
