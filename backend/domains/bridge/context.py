"""
Rijke context voor Iris Remote — het verschil tussen een afstandsbediening en
een assistent.

De bridge duwde tot nu toe alleen "wat wacht op een mens" naar de cloud. Dat
maakt beslissen onderweg mogelijk, maar niet meedenken: Iris kon onderweg niet
zien hoe je dag eruitziet, wat er in je mailbox omgaat, of het verkeer stijgt
of daalt. Deze module verzamelt dat, in een vorm die zowel de telefoon kan
tekenen als een LLM kan lezen.

Vier secties, elk met een eigen `status` (`ok` / `off` / `error`) zodat de
telefoon "niet geconfigureerd" nooit als "alles rustig" toont:

    mail       Outlook-achterstand, urgente berichten, afzenderpatronen
    agenda     vandaag + 7 dagen, vrije blokken, reistijd-waarschuwingen
    analytics  GA4 laatste 7 dagen vs. de 7 daarvoor, bronnen, top-pagina's
    seo        per site: GSC-trend, stijgers en dalers

Plus `pulse`: een deterministische "wat gaat goed / wat gaat slecht"-lijst,
afgeleid uit bovenstaande. Bewust zónder LLM — een oordeel dat wegvalt zodra
de gateway hapert is geen oordeel, en de chat-Iris krijgt deze lijst juist als
grondstof mee zodat ze niet hoeft te gokken.

Caching is geen optimalisatie maar een vereiste: `bridge_sync` draait elke drie
minuten en zou anders per dag honderden GA4-, Graph- en Agenda-calls doen. Per
sectie een eigen TTL, in `bridge_context_cache`. Een verlopen cache die niet
ververst kan worden wordt liever oud dan leeg teruggegeven (met `stale: true`)
— verouderde cijfers mét datum zijn bruikbaar, een lege sectie is dat niet.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Amsterdam")

# TTL per sectie, in seconden. Mail beweegt het snelst, GA het traagst (en is
# het duurst om op te halen — GA4 levert bovendien pas cijfers t/m gisteren).
TTL_MAIL = 5 * 60
TTL_AGENDA = 15 * 60
TTL_ANALYTICS = 6 * 60 * 60
TTL_SEO = 60 * 60


def _now() -> datetime:
    return datetime.now(TZ)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── Cache ───────────────────────────────────────────────────────────────────

def _cache_read(key: str, ttl: int) -> tuple:
    """(payload, vers?) — een verlopen payload komt nog steeds terug, zodat de
    aanroeper hem als terugval kan gebruiken als verversen mislukt."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM bridge_context_cache WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None, False
    try:
        payload = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        return None, False
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(row["updated_at"])).total_seconds()
    except (ValueError, TypeError):
        return payload, False
    return payload, age < ttl


def _cache_write(key: str, payload: Dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bridge_context_cache (key, payload, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (key, json.dumps(payload, ensure_ascii=False, default=str),
             datetime.now(timezone.utc).isoformat()),
        )


async def _section(key: str, ttl: int, builder: Callable) -> Dict[str, Any]:
    """Bouw een sectie, of geef de cache terug. Een bouwfout maakt nooit de hele
    sync stuk — hij levert een `error`-sectie (of de laatste bekende cijfers
    met `stale: true`)."""
    cached, fresh = _cache_read(key, ttl)
    if fresh:
        return cached
    try:
        if asyncio.iscoroutinefunction(builder):
            result = await builder()
        else:
            # GA4 en de mail-aggregaties zijn blokkerend; ze mogen de sync-loop
            # (en daarmee de scheduler) niet stilzetten.
            result = await asyncio.to_thread(builder)
    except Exception as e:  # noqa: BLE001
        logger.warning("Bridge-context '%s' bouwen mislukt: %s", key, e)
        if cached:
            return {**cached, "stale": True, "error": str(e)[:200]}
        return {"status": "error", "error": str(e)[:200]}
    result.setdefault("status", "ok")
    result["generated_at"] = _iso(_now())
    _cache_write(key, result)
    return result


# ── Mail ────────────────────────────────────────────────────────────────────

def build_mail() -> Dict[str, Any]:
    """Wat er in de mailbox omgaat — achterstand, urgentie, patronen.

    Twee bronnen die niet door elkaar mogen lopen: `outlook_emails` is Vincents
    eigen postvak (lezen/triage), `mail_replies` zijn helpdesk-concepten die op
    goedkeuring wachten. Het eerste is werk dat hij zelf moet doen, het tweede
    werk dat een agent al deed.
    """
    from ..outlook import service as outlook

    if not outlook.is_configured():
        return {"status": "off", "reason": "Outlook niet geconfigureerd"}
    if not outlook.is_authenticated():
        return {"status": "off", "reason": "Outlook niet ingelogd (device-flow)",
                "action_hint": "Log opnieuw in via de Mail-tab in Agent OS."}

    # Concepten liggen klaar vóórdat Vincent kijkt — de bridge is een pull-model
    # (max 1x/3 min), dus 'tik en genereer nu' bestaat niet. Kost een LLM-call
    # per nieuwe urgente mail; de functie zelf bewaakt budget/quota en slaat
    # stil over als die op is (geen kaart, geen crash — zie ensure_suggested_replies).
    try:
        asyncio.run(outlook.ensure_suggested_replies(limit=3))
    except Exception:  # noqa: BLE001
        logger.warning("Bridge-context: ensure_suggested_replies mislukt", exc_info=True)

    stats = outlook.get_stats()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # Elke telling hieronder staat op het postvak IN én laat weggefilterde mail
    # buiten beschouwing. Vóór 11 aug 2026 telden ze op de héle tabel: verzonden
    # post, spam en ruis telden mee, en zo werd "121 open" het grootste getal op
    # het scherm terwijl geen enkele knop daar iets aan veranderde. Wat om een
    # handeling vraagt en wat er alleen ligt zijn twee verschillende getallen.
    ECHT = ("folder='inbox' AND filter_rule_id IS NULL "
            "AND triage_label NOT IN ('spam','archief')")

    with get_conn() as conn:
        # Onbeantwoord én ongelezen in het postvak IN — de echte achterstand.
        # 'is_replied' is de betrouwbaarste 'afgehandeld'-indicator die we
        # hebben; ongelezen alleen zou elke nieuwsbrief meetellen.
        backlog = conn.execute(
            f"SELECT COUNT(*) c FROM outlook_emails "
            f"WHERE {ECHT} AND is_replied=0 AND is_read=0"
        ).fetchone()["c"]
        week_in = conn.execute(
            f"SELECT COUNT(*) c FROM outlook_emails WHERE {ECHT} AND received_at >= ?",
            (week_ago,),
        ).fetchone()["c"]
        week_replied = conn.execute(
            f"SELECT COUNT(*) c FROM outlook_emails "
            f"WHERE {ECHT} AND received_at >= ? AND is_replied=1",
            (week_ago,),
        ).fetchone()["c"]
        # Wat de afzenderregels weghielden. Apart getal, telt nergens in mee:
        # zichtbaar zodat je kunt beoordelen of het filter te streng staat.
        weggefilterd = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails "
            "WHERE filter_rule_id IS NOT NULL AND received_at >= ?", (week_ago,),
        ).fetchone()["c"]
        oldest = conn.execute(
            f"SELECT subject, from_name, from_email, received_at FROM outlook_emails "
            f"WHERE {ECHT} AND is_replied=0 AND is_read=0 "
            f"ORDER BY received_at ASC LIMIT 1"
        ).fetchone()
        # Een greep uit wat er is weggehouden, mét de reden. Dit is wat strenger
        # filteren verantwoord maakt: op de telefoon kun je zien wát er weg is en
        # de afzender met één tik weer toelaten. Zonder dat pad is een filter
        # niet te beoordelen en wordt "0 urgent" ononderscheidbaar van "alles
        # weggefilterd" (zelfde afweging als de bak Uitgefilterd bij de kansen).
        filtered_recent = conn.execute(
            "SELECT id, subject, from_name, from_email, received_at, filter_reason, "
            "       triage_label FROM outlook_emails "
            "WHERE filter_rule_id IS NOT NULL ORDER BY received_at DESC LIMIT 10"
        ).fetchall()
        # Urgent = wat de triage hoog scoorde en nog openstaat. Archief
        # (geen potentiële klant: webshop/vacature/digest/systeem) valt er
        # expliciet uit — net als in list_sorted_db — zodat gearchiveerde
        # noise nooit in het urgent-blok op de telefoon belandt.
        urgent = conn.execute(
            f"SELECT id, subject, from_name, from_email, received_at, priority, "
            f"       triage_label, ai_summary, ai_action, suggested_reply "
            f"FROM outlook_emails WHERE {ECHT} AND is_replied=0 "
            f"AND priority >= 70 AND received_at >= ? "
            f"ORDER BY priority DESC, received_at DESC LIMIT 8",
            (month_ago,),
        ).fetchall()
        senders = conn.execute(
            f"SELECT from_email, from_name, COUNT(*) c, "
            f"       SUM(CASE WHEN is_replied=1 THEN 1 ELSE 0 END) replied "
            f"FROM outlook_emails WHERE {ECHT} AND received_at >= ? AND from_email != '' "
            f"GROUP BY from_email ORDER BY c DESC LIMIT 5",
            (month_ago,),
        ).fetchall()
        # Klaarstaande conceptantwoorden op Vincents éígen mail. Stond niet in
        # de payload, waardoor de telefoon alleen `helpdesk_pending` kon tonen
        # onder de kop "Concepten" — twee verschillende soorten, één getal, en
        # de nieuwe soort telde niet mee.
        personal_drafts = conn.execute(
            f"SELECT COUNT(*) c FROM outlook_emails "
            f"WHERE {ECHT} AND is_replied=0 AND suggested_reply_dismissed=0 "
            f"AND suggested_reply IS NOT NULL AND suggested_reply != ''"
        ).fetchone()["c"]

    helpdesk = 0
    try:
        from ..mail import service as mail
        helpdesk = len(mail.pending_replies())
    except Exception:  # noqa: BLE001
        logger.warning("Bridge-context: helpdesk-concepten tellen mislukt", exc_info=True)

    # Triage is de motor onder zowel urgentie als conceptantwoorden: zonder een
    # priority >= 70 selecteert `ensure_suggested_replies` niets en blijft het
    # urgent-blok leeg. Ligt de LLM plat (quota/budget), dan is die leegte geen
    # rust maar honger — en precies dát moet de telefoon kunnen zeggen. Zwijgen
    # laat "43 open, 0 urgent" lezen als een opgeruimde mailbox terwijl 68 mails
    # nooit door de triage zijn gekomen (gemeten, 7 aug 2026).
    llm_paused = False
    try:
        from ...shared.outcomes import llm_budget_exceeded
        llm_paused = llm_budget_exceeded()
    except Exception:  # noqa: BLE001
        logger.warning("Bridge-context: LLM-budgetstand ophalen mislukt", exc_info=True)

    oldest_days = None
    if oldest:
        try:
            oldest_days = (datetime.now(timezone.utc) - datetime.fromisoformat(
                str(oldest["received_at"]).replace("Z", "+00:00"))).days
        except (ValueError, TypeError):
            oldest_days = None

    # Kai-stijl groepering (needs_reply/fyi/waiting) bovenop dezelfde triage die
    # 'urgent' en 'by_label' al voeden — geen nieuwe classificatie, alleen een
    # andere presentatie die de telefoon rechtstreeks kan renderen als pills.
    sorted_inbox = outlook.list_sorted_db(limit_per_bucket=8)

    # Beantwoord-percentage mag alleen op het scherm als het waargenomen is.
    # `is_replied` werd tot 11 aug 2026 uitsluitend gezet door onze eigen
    # verstuurknop, dus alles wat Vincent gewoon in Outlook beantwoordde telde
    # nooit mee en stond er permanent "0% beantwoord (7d)". Nu leest
    # `_sync_sent_items` het uit Verzonden items; is dat pad (nog) nooit iets
    # tegengekomen, dan is het antwoord `None` — "we weten het niet" — en nooit
    # een 0 die als oordeel leest. Zelfde regel als de speculatieve SEO-kans.
    with get_conn() as conn:
        ooit_waargenomen = conn.execute(
            "SELECT COUNT(*) c FROM outlook_emails WHERE replied_at != ''"
        ).fetchone()["c"]
    reply_meetbaar = bool(ooit_waargenomen)

    return {
        "backlog": backlog,
        "unread": stats.get("unread", 0),
        "untriaged": stats.get("untriaged", 0),
        "by_label": stats.get("by_label", {}),
        "filtered_week": weggefilterd,
        "filtered_total": stats.get("filtered", 0),
        "filtered_recent": [dict(r) for r in filtered_recent],
        "helpdesk_pending": helpdesk,
        "personal_drafts": personal_drafts,
        "llm_paused": llm_paused,
        "week": {"received": week_in, "replied": week_replied,
                 "measured": reply_meetbaar,
                 "reply_rate": (round(100 * week_replied / week_in)
                                if week_in and reply_meetbaar else None)},
        "oldest_open": ({"subject": oldest["subject"],
                         "from": oldest["from_name"] or oldest["from_email"],
                         "received_at": oldest["received_at"],
                         "days": oldest_days} if oldest else None),
        "urgent": [dict(r) for r in urgent],
        "top_senders": [dict(r) for r in senders],
        "sorted": sorted_inbox,
    }


# ── Agenda ──────────────────────────────────────────────────────────────────

# Een werkdag loopt van 08:00 tot 18:00 — buiten die uren is "vrij" geen gat
# maar avond, en die als beschikbaar aanbieden is precies wat een assistent
# onuitstaanbaar maakt.
_DAY_START_HOUR = 8
_DAY_END_HOUR = 18
_MIN_GAP_MINUTES = 45


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _free_gaps(events: List[Dict], day: datetime,
               not_before: Optional[datetime] = None) -> List[Dict[str, str]]:
    """Aaneengesloten vrije blokken van ≥45 min binnen de werkdag.

    `not_before` snijdt het verleden weg: een gat van 09:00–11:00 is om 14:00
    geen aanbod meer, en het als vrije tijd tonen maakt het hele overzicht
    onbetrouwbaar."""
    start = day.replace(hour=_DAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=_DAY_END_HOUR, minute=0, second=0, microsecond=0)
    if not_before and not_before > start:
        start = min(not_before, end)
    busy = []
    for ev in events:
        if ev.get("all_day") or ev.get("declined"):
            continue
        s, e = _parse_dt(ev.get("start")), _parse_dt(ev.get("end"))
        if s and e and e > start and s < end:
            busy.append((max(s, start), min(e, end)))
    busy.sort()

    gaps, cursor = [], start
    for s, e in busy:
        if (s - cursor).total_seconds() >= _MIN_GAP_MINUTES * 60:
            gaps.append({"start": cursor.strftime("%H:%M"), "end": s.strftime("%H:%M")})
        cursor = max(cursor, e)
    if (end - cursor).total_seconds() >= _MIN_GAP_MINUTES * 60:
        gaps.append({"start": cursor.strftime("%H:%M"), "end": end.strftime("%H:%M")})
    return gaps


def _watch_for(attendees: List[Dict]) -> Optional[str]:
    """Eén deterministische 'waar moet je op letten'-regel per afspraak.

    Vraagt `outlook.lookup_contact()` (puur SQL, geen LLM) per deelnemer en
    stopt bij het eerste signaal dat de moeite van het melden waard is: een
    liggende onbeantwoorde mail van hen weegt zwaarder dan hun leadstatus.
    Geen signaal = None, nooit een gedwongen zin — een lege agenda-notitie is
    eerlijker dan verzonnen context.
    """
    if not attendees:
        return None
    from ..outlook import service as outlook

    for a in attendees:
        email = a.get("email")
        if not email:
            continue
        try:
            info = outlook.lookup_contact(email)
        except Exception:  # noqa: BLE001
            logger.warning("Bridge-context: lookup_contact mislukt voor %s", email, exc_info=True)
            continue
        open_email = info.get("open_email")
        if open_email and open_email.get("days") is not None:
            return f"{a.get('name')}: nog geen antwoord op hun mail van {open_email['days']} dag(en) geleden"
        lead = info.get("lead")
        if lead and lead.get("status"):
            return f"{a.get('name')}: lead — status {lead['status']}"
    return None


async def build_agenda() -> Dict[str, Any]:
    """Vandaag in detail, de week in vogelvlucht, plus waar de gaten zitten."""
    from ..calendar import service as cal

    if not cal.is_configured():
        return {"status": "off", "reason": "Agenda niet geconfigureerd"}

    now = _now()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    result = await cal.get_events_range(day0, day0 + timedelta(days=8))
    if result.get("error"):
        return {"status": "off", "reason": result["error"]}

    events = result.get("events", [])
    today_str = day0.date().isoformat()
    today, upcoming = [], []
    for ev in events:
        s = _parse_dt(ev.get("start"))
        if not s:
            continue
        is_today = s.date().isoformat() == today_str
        row = {
            "summary": ev["summary"],
            "start": ev["start"],
            "end": ev["end"],
            "time": "hele dag" if ev["all_day"] else s.strftime("%H:%M"),
            "location": ev["location"],
            "online": bool(ev["hangout_link"]),
            "attendees": ev["attendees"],
            # Alleen voor vandaag berekend — dit vergt per deelnemer een
            # databasequery, en "morgen 6 afspraken" heeft geen per-afspraak
            # detail nodig, alleen de telling hieronder.
            "watch_for": _watch_for(ev["attendees"]) if is_today and not ev["declined"] else None,
            "declined": ev["declined"],
            "date": s.date().isoformat(),
        }
        (today if is_today else upcoming).append(row)

    # Per dag tellen — "morgen 6 afspraken" is de waarschuwing die telt.
    by_day: Dict[str, Dict[str, Any]] = {}
    for row in today + upcoming:
        if row["declined"]:
            continue
        d = by_day.setdefault(row["date"], {"date": row["date"], "count": 0, "first": None,
                                            "last": None, "titles": []})
        d["count"] += 1
        d["titles"] = (d["titles"] + [row["summary"]])[:4]
        if not row["start"]:
            continue
        if d["first"] is None or row["start"] < d["first"]:
            d["first"] = row["start"]
        if d["last"] is None or (row["end"] or row["start"]) > d["last"]:
            d["last"] = row["end"] or row["start"]

    pending = 0
    try:
        from ..calendar import agent as cal_agent
        pending = len(cal_agent.pending_proposals())
    except Exception:  # noqa: BLE001
        logger.warning("Bridge-context: agenda-voorstellen tellen mislukt", exc_info=True)

    next_up = None
    for row in today:
        s = _parse_dt(row["start"])
        if s and s > now and not row["declined"]:
            next_up = row
            break

    return {
        "today": today,
        "today_date": today_str,
        "next": next_up,
        "upcoming": upcoming[:20],
        "days": [by_day[k] for k in sorted(by_day)],
        "free_today": _free_gaps(today, now, not_before=now),
        "free_tomorrow": _free_gaps(
            [r for r in upcoming if r["date"] == (day0 + timedelta(days=1)).date().isoformat()],
            day0 + timedelta(days=1)),
        "pending_proposals": pending,
        # Onbereikbare agenda's zijn geen detail: ze betekenen dat dit
        # overzicht niet compleet is.
        "unreachable": result.get("unreachable", []),
        "calendars": result.get("calendars", []),
    }


# ── Analytics ───────────────────────────────────────────────────────────────

def _delta(now_val: Optional[float], prev_val: Optional[float]) -> Optional[Dict[str, Any]]:
    if now_val is None or prev_val is None:
        return None
    diff = now_val - prev_val
    pct = round(100 * diff / prev_val, 1) if prev_val else None
    return {"now": now_val, "prev": prev_val, "delta": round(diff, 1), "pct": pct}


def build_analytics() -> Dict[str, Any]:
    """GA4: de laatste 7 dagen naast de 7 daarvóór.

    Absolute cijfers zeggen niets zonder vergelijking — "412 sessies" is pas
    informatie als je weet dat het er vorige week 530 waren. GA4 kent geen
    'vorige periode' in één call, dus we halen 14 dagen op en splitsen die
    zelf op de dagreeks (die de API wél per dag levert).
    """
    from ..analytics import ga_service

    if not ga_service.is_configured():
        return {"status": "off", "reason": "GA4 niet geconfigureerd (GA4_PROPERTY_ID)"}

    data = ga_service.fetch_weekly_data(days=14)
    daily = data.get("daily") or []
    recent, previous = daily[-7:], daily[-14:-7]

    def total(rows: List[Dict], field: str) -> Optional[int]:
        return sum(int(r.get(field) or 0) for r in rows) if rows else None

    return {
        "period_days": 7,
        "summary": data.get("summary") or {},
        "compare": {
            "sessions": _delta(total(recent, "sessions"), total(previous, "sessions")),
            "users": _delta(total(recent, "users"), total(previous, "users")),
            "pageviews": _delta(total(recent, "pageviews"), total(previous, "pageviews")),
        },
        "daily": daily,
        "top_pages": (data.get("top_pages") or [])[:8],
        "channels": data.get("channels") or [],
        "devices": data.get("devices") or [],
        "countries": (data.get("countries") or [])[:5],
    }


# ── SEO ─────────────────────────────────────────────────────────────────────

def build_seo() -> Dict[str, Any]:
    """Per site: trend, stijgers en dalers uit `gsc_history`."""
    from ..seo import history as seo_history

    with get_conn() as conn:
        sites = conn.execute("SELECT id, name, base_url FROM sites").fetchall()

    out = []
    for site in sites:
        try:
            movers = seo_history.page_movers(site["id"], limit=3)
            out.append({
                "site_id": site["id"],
                "name": site["name"],
                "base_url": site["base_url"],
                "trend": seo_history.site_trend(site["id"]),
                "risers": movers.get("risers", []),
                "fallers": movers.get("fallers", []),
                "top_pages": seo_history.top_pages(site["id"], limit=5),
            })
        except Exception:  # noqa: BLE001
            logger.warning("Bridge-context: SEO voor %s mislukt", site["id"], exc_info=True)
    return {"sites": out}


# ── Pulse: wat gaat goed, wat gaat slecht ───────────────────────────────────

def _pulse_mail(mail: Dict, good: List, bad: List) -> None:
    if mail.get("status") != "ok":
        return
    oldest = mail.get("oldest_open") or {}
    days = oldest.get("days")
    if days is not None and days >= 3:
        bad.append({"area": "mail", "severity": "hoog" if days >= 7 else "midden",
                    "what": f"Oudste onbeantwoorde mail is {days} dagen oud",
                    "detail": f"{oldest.get('from', '?')}: {oldest.get('subject', '')}"[:160],
                    "why": "Hoe langer een vraag ligt, hoe duurder het antwoord."})
    backlog = mail.get("backlog") or 0
    if backlog >= 15:
        bad.append({"area": "mail", "severity": "midden",
                    "what": f"{backlog} onbeantwoorde mails in het postvak",
                    "why": "Achterstand groeit sneller dan hij slinkt."})
    elif backlog <= 3:
        good.append({"area": "mail", "what": f"Mailbox is bij ({backlog} open)"})
    rate = (mail.get("week") or {}).get("reply_rate")
    if rate is not None and rate >= 70:
        good.append({"area": "mail", "what": f"{rate}% van de mail deze week beantwoord"})
    if (mail.get("untriaged") or 0) >= 25:
        bad.append({"area": "mail", "severity": "laag",
                    "what": f"{mail['untriaged']} mails nog niet getrieerd",
                    "why": "Zonder triage kan Iris urgentie niet zien."})


def _pulse_agenda(agenda: Dict, good: List, bad: List) -> None:
    if agenda.get("status") != "ok":
        return
    if agenda.get("unreachable"):
        ids = ", ".join(u["id"] for u in agenda["unreachable"])
        bad.append({"area": "agenda", "severity": "hoog",
                    "what": f"Agenda niet volledig leesbaar ({ids})",
                    "why": "Een agenda die niet meetelt is precies hoe je dubbel boekt."})
    for day in agenda.get("days", [])[:3]:
        if day["count"] >= 6:
            bad.append({"area": "agenda", "severity": "midden",
                        "what": f"{day['date']}: {day['count']} afspraken",
                        "detail": ", ".join(day["titles"]),
                        "why": "Een volgeplande dag laat geen ruimte voor werk."})
    free = agenda.get("free_today") or []
    if free:
        def _minutes(gap: Dict) -> int:
            h1, m1 = (int(x) for x in gap["start"].split(":"))
            h2, m2 = (int(x) for x in gap["end"].split(":"))
            return (h2 * 60 + m2) - (h1 * 60 + m1)
        longest = max(free, key=_minutes)
        good.append({"area": "agenda",
                     "what": f"Vandaag nog {_minutes(longest)} min vrij "
                             f"({longest['start']}–{longest['end']})"})
    if (agenda.get("pending_proposals") or 0) > 0:
        bad.append({"area": "agenda", "severity": "laag",
                    "what": f"{agenda['pending_proposals']} afspraakvoorstel(len) wachten",
                    "why": "Onbevestigde voorstellen verlopen stil."})


def _pulse_analytics(ga: Dict, good: List, bad: List) -> None:
    if ga.get("status") != "ok":
        return
    sess = (ga.get("compare") or {}).get("sessions")
    if sess and sess.get("pct") is not None:
        pct = sess["pct"]
        if pct <= -15:
            bad.append({"area": "analytics", "severity": "hoog",
                        "what": f"Sessies {abs(pct)}% omlaag ({sess['prev']} → {sess['now']})",
                        "why": "Een daling van deze omvang is zelden ruis."})
        elif pct >= 15:
            good.append({"area": "analytics",
                         "what": f"Sessies {pct}% omhoog ({sess['prev']} → {sess['now']})"})
    br = (ga.get("summary") or {}).get("bounce_rate")
    if br is not None and br >= 75:
        bad.append({"area": "analytics", "severity": "laag",
                    "what": f"Bouncepercentage {br}%",
                    "why": "Bezoekers komen binnen en haken direct af."})


def _pulse_seo(seo: Dict, good: List, bad: List) -> None:
    if seo.get("status") != "ok":
        return
    for site in seo.get("sites", []):
        trend = site.get("trend") or {}
        clicks = trend.get("delta_clicks")
        if clicks is None:
            continue
        if clicks <= -10:
            bad.append({"area": "seo", "severity": "midden",
                        "what": f"{site['name']}: {abs(clicks)} klikken minder (week-op-week)",
                        "why": "Dalende klikken vóór dalende posities = verlies van zichtbaarheid."})
        elif clicks >= 10:
            good.append({"area": "seo",
                         "what": f"{site['name']}: {clicks} klikken meer (week-op-week)"})
        for faller in (site.get("fallers") or [])[:1]:
            bad.append({"area": "seo", "severity": "laag",
                        "what": f"{site['name']}: pagina zakt weg",
                        "detail": str(faller.get("page_url") or "")[:120],
                        "why": "Wegzakkende pagina's zijn de goedkoopste winst — refresh i.p.v. nieuw."})


_SEVERITY_ORDER = {"hoog": 0, "midden": 1, "laag": 2}


def build_pulse(sections: Dict[str, Dict]) -> Dict[str, Any]:
    """Deterministisch oordeel over alle secties samen.

    Geen LLM: dit moet blijven werken als de gateway plat ligt, én het is de
    grondstof die de chat-Iris meekrijgt zodat ze niet hoeft te gokken.
    """
    good: List[Dict] = []
    bad: List[Dict] = []
    _pulse_mail(sections.get("mail") or {}, good, bad)
    _pulse_agenda(sections.get("agenda") or {}, good, bad)
    _pulse_analytics(sections.get("analytics") or {}, good, bad)
    _pulse_seo(sections.get("seo") or {}, good, bad)

    bad.sort(key=lambda b: _SEVERITY_ORDER.get(b.get("severity"), 3))
    off = [name for name, sec in sections.items()
           if isinstance(sec, dict) and sec.get("status") in ("off", "error")]
    return {"good": good[:6], "bad": bad[:8], "unavailable": off,
            "generated_at": _iso(_now())}


# ── Samenstellen ────────────────────────────────────────────────────────────

async def build_context() -> Dict[str, Any]:
    """Alle secties + pulse. Faalt nooit als geheel: elke sectie draagt zijn
    eigen status, en één kapotte integratie mag de rest niet meenemen."""
    sections = {
        "mail": await _section("mail", TTL_MAIL, build_mail),
        "agenda": await _section("agenda", TTL_AGENDA, build_agenda),
        "analytics": await _section("analytics", TTL_ANALYTICS, build_analytics),
        "seo": await _section("seo", TTL_SEO, build_seo),
    }
    sections["pulse"] = build_pulse(sections)
    return sections
