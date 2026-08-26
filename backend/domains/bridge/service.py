"""
Bridge — synchroniseert de review-gates met de cloud-companion (Vercel + Neon),
zodat Vincent kan beslissen als deze machine uitstaat.

Ontwerp (pull-model, alleen uitgaand HTTPS — geen open poorten of tunnels):
  1. PUSH: de volledige actieve set "wacht op een mens"-items — de canonieke
     lijst uit het Actiecentrum (`build_inbox()`), per item verrijkt met genoeg
     preview om onderweg te kúnnen beslissen (artikel-HTML, mail-concept +
     oorspronkelijke vraag, outreach-tekst, agendaslot) — plus de laatste
     Iris-briefing en de funnel-cijfers. Full-state elke run: idempotent en
     zelfherstellend, geen delta-boekhouding; wat lokaal verdween wordt in de
     cloud gearchiveerd.
  2. PULL: besluiten die onderweg genomen zijn (`decisions`, status pending).
     Toepassen loopt via de whitelist in `actions.py` — dezelfde service-
     functies als de lokale UI-knoppen, dus alle gates blijven gelden.
  3. ACK: elk besluit krijgt applied/failed + boodschap terug, zodat de
     telefoon toont wat er echt gebeurde.

Staat de pc uit, dan stapelen besluiten zich op in Neon en voert de
eerstvolgende sync ze chronologisch uit.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ...shared import failures
from ...shared.config import BRIDGE_REMOTE_URL, BRIDGE_TOKEN
from ...shared.database import get_conn
from ...shared.projects import project_visible, filter_cross_project_mentions

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Uitslag van de laatste sync, voor GET /api/bridge/status.
_last_sync: Dict[str, Any] = {}

# Faal-reeks-sleutel: één storing = één kaart, ook over herstarts heen.
_FAIL_KEY = "bridge:sync"


def enabled() -> bool:
    return bool(BRIDGE_REMOTE_URL and BRIDGE_TOKEN)


def config_state() -> str:
    """`off` | `partial` | `on`.

    Het onderscheid tussen 'bewust uit' en 'half ingevuld' is niet cosmetisch:
    beide leeg is een verse installatie (stil overslaan is dan juist), maar één
    van de twee ingevuld betekent dat iemand de bridge wilde en halverwege bleef
    steken. Dat leest op de telefoon als "171u offline" en lokaal als niets —
    precies de stilte die dit systeem nergens mag hebben.
    """
    url, token = bool(BRIDGE_REMOTE_URL), bool(BRIDGE_TOKEN)
    if url and token:
        return "on"
    return "partial" if (url or token) else "off"


def _missing_setting() -> str:
    if not BRIDGE_REMOTE_URL:
        return "BRIDGE_REMOTE_URL"
    return "BRIDGE_TOKEN" if not BRIDGE_TOKEN else ""


# ── Verzamelen: items + previews ────────────────────────────────────────────

def _content_detail(job_id: str) -> Optional[Dict]:
    from ..publish import content_pipeline
    job = content_pipeline.get_job(job_id)
    if not job:
        return None
    return {
        "blog_html": job.get("blog_html") or "",
        "title": job.get("title") or "",
        "keyword": job.get("keyword") or "",
        "seo_score": job.get("seo_score"),
        "status": job.get("status"),
        "site_id": job.get("site_id"),
    }


def _mail_details() -> Dict[str, Dict]:
    from ..mail import service as mail
    out = {}
    for r in mail.pending_replies():
        out[str(r["id"])] = {
            "draft_body": r.get("edited_body") or r.get("draft_body") or "",
            "to_addr": r.get("to_addr"),
            "subject": r.get("subject"),
            "project": r.get("project"),
            "question_subject": r.get("question_subject"),
            "question_body": (r.get("question_body") or "")[:4000],
            "from_name": r.get("from_name"),
            "from_addr": r.get("from_addr"),
        }
    return out


def _personal_mail_details() -> Dict[str, Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject, from_name, from_email, suggested_reply, ai_summary "
            "FROM outlook_emails "
            "WHERE folder='inbox' AND is_replied=0 AND suggested_reply_dismissed=0 "
            "AND suggested_reply IS NOT NULL AND suggested_reply != ''"
        ).fetchall()
    return {
        str(r["id"]): {
            "draft_body": r["suggested_reply"],
            "subject": r["subject"],
            "from_name": r["from_name"],
            "from_addr": r["from_email"],
            "ai_summary": r["ai_summary"],
        }
        for r in rows
    }


def _social_details() -> Dict[str, Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id, m.platform, m.author_name, m.author_handle, m.text, "
            "m.kind, m.parent_url, m.draft_body, m.edited_body, i.project, i.label "
            "FROM social_inbox_msg m JOIN social_inboxes i ON i.id=m.inbox_id "
            "WHERE m.status IN ('pending_review','edited')"
        ).fetchall()
    return {
        str(r["id"]): {
            "platform": r["platform"],
            "author_name": r["author_name"],
            "author_handle": r["author_handle"],
            "text": r["text"],
            "kind": r["kind"],
            "parent_url": r["parent_url"],
            "draft_body": r["edited_body"] or r["draft_body"] or "",
        }
        for r in rows
    }


def _outreach_details() -> Dict[str, Dict]:
    from ..prospecting import outreach
    from ..prospecting.router import _svc as leads_svc
    out = {}
    for lead in leads_svc.list_leads(status="outreach_review"):
        out[str(lead["id"])] = {
            "org_name": lead.get("org_name"),
            "city": lead.get("city"),
            "subject": lead.get("outreach_subject"),
            "body": lead.get("outreach_draft"),
            "target_email": outreach.target_email_for(lead),
        }
    return out


def _calendar_details() -> Dict[str, Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, subject, from_addr, title, proposed_start, proposed_end, "
            "location, priority, conflict_checked, rationale "
            "FROM calendar_proposals WHERE status='pending_review'"
        ).fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def collect_items() -> List[Dict[str, Any]]:
    """De Actiecentrum-inbox, verrijkt met previews per item-type. `key` is de
    stabiele identiteit in de cloud (dismiss_kind + id)."""
    from ..action_center import service as ac
    inbox = ac.build_inbox()
    mail_d = _mail_details()
    personal_mail_d = _personal_mail_details()
    social_d = _social_details()
    outreach_d = _outreach_details()
    calendar_d = _calendar_details()

    items = []
    for it in inbox.get("items", []) if isinstance(inbox, dict) else inbox:
        if not project_visible(it.get("project")):
            continue
        kind = it.get("dismiss_kind") or "error"
        item_id = str(it.get("id"))
        detail: Optional[Dict] = None
        if kind == "content":
            try:
                detail = _content_detail(item_id)
            except Exception:
                logger.exception("Bridge: content-detail mislukt voor %s", item_id)
        elif kind == "mail":
            detail = mail_d.get(item_id)
        elif kind == "personal_mail":
            detail = personal_mail_d.get(item_id)
        elif kind == "social":
            detail = social_d.get(item_id)
        elif kind == "outreach":
            detail = outreach_d.get(item_id)
        elif kind == "calendar":
            detail = calendar_d.get(item_id)
        items.append({
            "key": f"{kind}:{item_id}",
            "kind": it.get("kind"),
            "dismiss_kind": kind,
            "item_id": item_id,
            "title": it.get("title"),
            "project": it.get("project"),
            "created_at": it.get("created_at"),
            "summary": it.get("summary"),
            "actions": it.get("actions") or [],
            "detail": detail,
        })
    return items


def collect_briefing() -> Dict[str, Any]:
    """Laatste Iris-briefing + funnel-cijfers als leesvoer voor onderweg.

    Naast de markdown gaat ook de gestructureerde snapshot mee (scores,
    pijlers, trend-delta's en een GSC-dagreeks per site) zodat de telefoon
    een dashboard kan tekenen in plaats van een lap tekst."""
    briefing: Dict[str, Any] = {}
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT report_date, markdown, grades, llm_ok, advice, metrics "
                "FROM iris_reports ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
        if r:
            grades = json.loads(r["grades"] or "{}")
            briefing["iris"] = {
                "date": r["report_date"],
                "markdown": r["markdown"],
                "grades": grades,
                "llm_ok": bool(r["llm_ok"]),
                "advice": filter_cross_project_mentions(json.loads(r["advice"] or "[]")),
            }
            snapshot = json.loads(r["metrics"] or "{}")
            briefing["projects"] = [_compact_project(p, grades)
                                    for p in snapshot.get("projects") or []
                                    if project_visible(p.get("project"))]
            # Knelpunten zijn Iris' belangrijkste regel: het laagste cijfer is
            # zelden het echte probleem. Zonder deze meegestuurde lijst moet de
            # telefoon dat uit de markdown-lap zien te vissen. Gefilterd vóór
            # het strippen naar (prio/issue/actie/waarom): de suggestion.scope
            # die de filter nodig heeft staat alleen op de ongestripte rij.
            filtered_bottlenecks = filter_cross_project_mentions(snapshot.get("bottlenecks") or [])
            briefing["bottlenecks"] = [
                {k: b.get(k) for k in ("prio", "issue", "actie", "waarom")}
                for b in filtered_bottlenecks[:5]
            ]
    except Exception:
        logger.exception("Bridge: Iris-briefing ophalen mislukt")
    try:
        from ..seo import history as seo_history
        series: Dict[str, List] = {}
        for p in briefing.get("projects") or []:
            if p.get("site_id"):
                series[p["site_id"]] = [
                    [d["date"], d["clicks"], d["position"]]
                    for d in seo_history.site_series(p["site_id"], days=28)
                ]
        briefing["series"] = series
    except Exception:
        logger.exception("Bridge: GSC-dagreeksen ophalen mislukt")
    try:
        from ..iris import predictions
        briefing["track_record"] = predictions.track_record()
    except Exception:
        logger.exception("Bridge: trefkans ophalen mislukt")
    try:
        from ..prospecting import funnel
        briefing["funnel"] = funnel.funnel_stats()
    except Exception:
        logger.exception("Bridge: funnel-cijfers ophalen mislukt")
    return briefing


def _compact_project(p: Dict[str, Any], grades: Dict[str, Any]) -> Dict[str, Any]:
    """Alleen wat de telefoon tekent — de volle snapshot is te zwaar voor Neon."""
    pillars = p.get("pillars") or {}
    seo = pillars.get("seo") or {}
    trend = (p.get("trend") or {}).get("site")
    return {
        "project": p.get("project"),
        "site_id": p.get("site_id"),
        "grade": p.get("grade"),
        "score": p.get("score"),
        "oordeel": (grades.get(p.get("project")) or {}).get("oordeel") or "",
        "pillars": {name: (pil or {}).get("score") for name, pil in pillars.items()},
        "seo": {k: seo.get(k) for k in
                ("clicks", "impressions", "avg_position", "ctr_pct", "pages")},
        "trend": trend,
    }


async def build_push_payload() -> Dict[str, Any]:
    """Items + briefing + de rijke context (mail/agenda/analytics/seo/pulse).

    De context zit bewust in dezelfde push: één ronde, één momentopname. Twee
    losse pushes zouden een telefoon kunnen laten zien die half oud en half
    nieuw is, en dat is erger dan consequent drie minuten achterlopen."""
    from . import context as ctx
    try:
        rich = await ctx.build_context()
    except Exception:  # noqa: BLE001
        logger.exception("Bridge: contextopbouw mislukt — push gaat door zonder")
        rich = {}
    try:
        google_cfg = ctx.build_google_config()
    except Exception:  # noqa: BLE001
        logger.warning("Bridge: Google-config verzamelen mislukt", exc_info=True)
        google_cfg = None
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": collect_items(),
        "briefing": collect_briefing(),
        "context": rich,
    }
    if google_cfg:
        payload["google"] = google_cfg
    return payload


# ── Sync-cyclus ─────────────────────────────────────────────────────────────

def _base() -> str:
    return BRIDGE_REMOTE_URL.rstrip("/")


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {BRIDGE_TOKEN}"}


# ── WhatsApp-proxy: falen is nooit stil ────────────────────────────────────
# Vóór deze versie gaf elke whatsapp_*_proxy-functie een mislukking alleen
# terug als {"ok": False, "detail": ...} aan de aanroeper — zichtbaar zolang
# iemand toevallig naar het WhatsApp-scherm kijkt, en daarna spoorloos. Precies
# de stilte die dit bestand elders (_note_sync_failed) al niet toestaat voor de
# achtergrond-sync. Eigen faal-reeks (niet _FAIL_KEY): de achtergrond-sync en
# de WhatsApp-proxy praten met dezelfde remote maar kunnen onafhankelijk breken
# (bv. alleen remote/api/ui.js's whatsapp-*-bridge-tak crasht terwijl
# /api/bridge?op=push gewoon werkt) — twee aparte kaarten die allebei zeggen
# wélk stuk stuk is, in plaats van één kaart die de verkeerde plek aanwijst.
_WA_FAIL_KEY = "bridge:whatsapp"


def _note_whatsapp_ok() -> None:
    had = failures.note_success(_WA_FAIL_KEY)
    if had:
        from ...shared.outcomes import log_outcome
        log_outcome(
            "Bridge", "whatsapp_hersteld",
            f"WhatsApp-overzicht op :1250 werkt weer na {had} mislukte pogingen op rij.",
            artifact=BRIDGE_REMOTE_URL,
            next_step="Niets — het WhatsApp-scherm toont weer de actuele stand.",
        )


def _note_whatsapp_failed(op: str, exc: BaseException) -> str:
    """Registreert een mislukte WhatsApp-proxycall en geeft de leesbare
    detailtekst terug (voor in de UI-response). Zelfde classificatie/
    escalatie-logica als _note_sync_failed hieronder."""
    detail = failures.describe_exception(exc)
    klass = failures.classify(exc)
    failures.note_failure(_WA_FAIL_KEY, detail, klass)
    if failures.should_escalate(_WA_FAIL_KEY, exc):
        from ...shared.outcomes import log_outcome
        steps = {
            failures.CLASS_AUTH: "Controleer of BRIDGE_TOKEN in .env exact gelijk is aan "
                                 "de BRIDGE_TOKEN-env-var in Vercel.",
            failures.CLASS_CONFIG: f"Controleer BRIDGE_REMOTE_URL ({BRIDGE_REMOTE_URL or 'leeg'}) "
                                   "— moet met http:// of https:// beginnen — en of de "
                                   "Vercel-deploy nog leeft.",
        }
        log_outcome(
            "Bridge", "whatsapp_proxy_failed",
            f"WhatsApp-overzicht op :1250 ('{op}') mislukt ({klass}): {detail}",
            artifact=BRIDGE_REMOTE_URL,
            next_step=steps.get(klass, "Test met GET /api/bridge/whatsapp-stats en controleer "
                                       "de Vercel-logs voor remote/api/ui.js."),
            status="error",
        )
        failures.mark_escalated(_WA_FAIL_KEY)
    return detail


# Het :1250-dashboard draait op SQLite, maar de WhatsApp-agent leeft in het
# remote-systeem (Neon-Postgres). In plaats van twee DB's te koppelen proxy't
# dit endpoint naar remote/api/ui.js?op=whatsapp-stats-bridge, dat dezelfde
# Bearer-token verifieert (resolveBridgeTenant) en de data teruggeeft voor de
# huidige tenant. Remote blijft de bron van waarheid; géén credentials gedeeld.
async def whatsapp_stats_proxy() -> Dict[str, Any]:
    if not enabled():
        return {"ok": False, "detail": "Bridge niet geconfigureerd (BRIDGE_REMOTE_URL/BRIDGE_TOKEN)"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/ui?op=whatsapp-stats-bridge")
            r.raise_for_status()
            _note_whatsapp_ok()
            return {"ok": True, **r.json()}
    except Exception as e:
        detail = _note_whatsapp_failed("whatsapp-stats-bridge", e)
        return {"ok": False, "detail": f"Remote WhatsApp-stats onbereikbaar: {detail}"}


# ── Communicatie proxy — volledig overzicht, niet alleen de cijfers ────────
# Vincent wilde het Communicatie-scherm dat op 22 aug 2026 in Iris Remote
# kwam (escalaties + nieuwe contacten + alle gesprekken + transcript) ook op
# :1250 zien, niet alleen op zijn telefoon. Zelfde proxy-patroon als
# whatsapp_stats_proxy hierboven — remote/api/ui.js kreeg er vijf `-bridge`-
# varianten bij die met dezelfde BRIDGE_TOKEN verifiëren (resolveBridgeTenant)
# in plaats van een sessiecookie. Twee schrijvende routes (reply/dismiss)
# gaan hier ook doorheen: het antwoord verstuurt Vercel zelf naar Meta (het
# WHATSAPP_TOKEN leeft alleen daar), dus deze machine hoeft geen WhatsApp-
# credential te hebben om vanaf :1250 te kunnen reageren.
async def _bridge_get(op: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not enabled():
        return {"ok": False, "detail": "Bridge niet geconfigureerd (BRIDGE_REMOTE_URL/BRIDGE_TOKEN)"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.get(f"{_base()}/api/ui", params={"op": op, **(params or {})})
            r.raise_for_status()
            _note_whatsapp_ok()
            return {"ok": True, **r.json()}
    except Exception as e:
        detail = _note_whatsapp_failed(op, e)
        return {"ok": False, "detail": f"Remote '{op}' onbereikbaar: {detail}"}


async def _bridge_post(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not enabled():
        return {"ok": False, "detail": "Bridge niet geconfigureerd (BRIDGE_REMOTE_URL/BRIDGE_TOKEN)"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.post(f"{_base()}/api/ui?op={op}", json=payload)
            r.raise_for_status()
            _note_whatsapp_ok()
            return {"ok": True, **r.json()}
    except Exception as e:
        detail = _note_whatsapp_failed(op, e)
        return {"ok": False, "detail": f"Remote '{op}' onbereikbaar: {detail}"}


async def whatsapp_list_proxy() -> Dict[str, Any]:
    return await _bridge_get("whatsapp-bridge")


async def whatsapp_conversations_proxy() -> Dict[str, Any]:
    return await _bridge_get("whatsapp-conversations-bridge")


async def whatsapp_thread_proxy(wa_id: str) -> Dict[str, Any]:
    return await _bridge_get("whatsapp-thread-bridge", {"wa_id": wa_id})


async def whatsapp_reply_proxy(item_id: Any, text: str) -> Dict[str, Any]:
    return await _bridge_post("whatsapp-reply-bridge", {"id": item_id, "text": text})


async def whatsapp_dismiss_proxy(item_id: Any) -> Dict[str, Any]:
    return await _bridge_post("whatsapp-dismiss-bridge", {"id": item_id})


# ── Uitgaand WhatsApp-berichtje (agenda-herinnering, punt 14g-achtig) ──────
# Het Meta-token leeft alleen in Vercel (Vincents eigen app, zie CLAUDE.md
# 14e-b) — dus stuurt de lokale machine geen WhatsApp zelf, maar vraagt de
# remote-kant het te doen: /api/bridge?op=reminder stuurt naar het EERSTE
# nummer in tenants.whatsapp_allowed_from (Vincent zelf), zelfde route als
# notifyMe(). Stil (False) als de bridge niet geconfigureerd is — een
# agenda-reminder die geen WhatsApp kan versturen mag de scheduler-job niet
# laten crashen, alleen 0 verstuurde herinneringen opleveren.
async def send_whatsapp_reminder(text: str) -> bool:
    if not enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.post(f"{_base()}/api/bridge?op=reminder", json={"text": text})
            r.raise_for_status()
            return bool(r.json().get("ok"))
    except Exception as e:
        logger.warning("send_whatsapp_reminder mislukt: %s", e)
        return False


# Anders dan send_whatsapp_reminder hierboven (altijd naar Vincent zelf, het
# EERSTE nummer in whatsapp_allowed_from) gaat dit naar een specifieke klant —
# bv. de bevestiging/afwijzing van een afspraak die klant-Iris via WhatsApp
# voorstelde (calendar/agent.py:notify_customer_outcome). Zelfde route: het
# Meta-token leeft alleen in Vercel, dus de lokale machine vraagt het te doen.
async def send_whatsapp_to_customer(wa_id: str, text: str) -> bool:
    if not enabled():
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.post(f"{_base()}/api/bridge?op=customer-notify",
                                   json={"wa_id": wa_id, "text": text})
            r.raise_for_status()
            return bool(r.json().get("ok"))
    except Exception as e:
        logger.warning("send_whatsapp_to_customer mislukt: %s", e)
        return False


async def sync_once() -> Dict[str, Any]:
    """Eén volledige cyclus: push state → pull besluiten → toepassen → ack."""
    global _last_sync
    if not enabled():
        return {"ok": False, "detail": "Bridge niet geconfigureerd (BRIDGE_REMOTE_URL/BRIDGE_TOKEN)"}

    summary: Dict[str, Any] = {"ok": True, "pushed": 0, "applied": 0, "failed": 0,
                               "at": datetime.now(timezone.utc).isoformat()}
    try:
        payload = await build_push_payload()
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            r = await client.post(f"{_base()}/api/bridge?op=push", json=payload)
            r.raise_for_status()
            summary["pushed"] = len(payload["items"])

            r = await client.get(f"{_base()}/api/bridge?op=decisions")
            r.raise_for_status()
            decisions = r.json().get("decisions", [])

            acks = []
            for d in decisions:
                ok, message = await _apply(d)
                acks.append({"id": d.get("id"),
                             "status": "applied" if ok else "failed",
                             "result": message})
                summary["applied" if ok else "failed"] += 1
            if acks:
                r = await client.post(f"{_base()}/api/bridge?op=ack", json={"acks": acks})
                r.raise_for_status()

            # Notities die onderweg zijn ingesproken → vault (Onderweg-map).
            r = await client.get(f"{_base()}/api/bridge?op=notes")
            r.raise_for_status()
            notes = r.json().get("notes", [])
            synced_ids = [n["id"] for n in notes if _store_note(n)]
            if synced_ids:
                r = await client.post(f"{_base()}/api/bridge?op=notes-ack",
                                      json={"ids": synced_ids})
                r.raise_for_status()
                summary["notes"] = len(synced_ids)
        _note_sync_ok()

        # Impact Calculator-leads: eigen try/except binnen impact_leads zelf,
        # dus een mislukte verrijking/analyse mag de rest van de sync-cyclus
        # (die net geslaagd is) niet alsnog als 'failed' laten boeken.
        from . import impact_leads
        summary["impact_leads"] = await impact_leads.process_pending()

        # AI Leadership Lab-leads (26 aug 2026, weareimpact.nl/lab): zelfde
        # eigen-try/except-redenering als impact_leads hierboven.
        from . import workshop_leads
        summary["workshop_leads"] = await workshop_leads.process_pending()

        # Boekingsaanvragen (26 aug 2026, weareimpact.nl): zelfde eigen-
        # try/except-redenering als impact_leads hierboven. Pusht bij elke
        # statuswijziging opnieuw (pending/approved/rejected), dus dit haalt
        # ook op als er niets nieuws bij is — zie booking_leads.py.
        from . import booking_leads
        summary["booking_leads"] = await booking_leads.process_pending()

        # LSP-workshop (24 aug 2026): zelfde eigen-try/except-redenering als
        # impact_leads hierboven — de rij bestaat al volledig (WhatsApp heeft
        # het rapport al verstuurd), dit logt alleen de Actiecentrum-kaart.
        from . import lsp_workshop
        summary["lsp_workshop"] = await lsp_workshop.process_pending()
    except Exception as e:
        logger.warning("Bridge-sync mislukt: %s", failures.describe_exception(e))
        summary = {"ok": False, "detail": failures.describe_exception(e)[:300],
                   "failure_class": failures.classify(e),
                   "at": datetime.now(timezone.utc).isoformat()}
        _note_sync_failed(e)
    _last_sync = summary
    return summary


# ── Falen: nooit stil ───────────────────────────────────────────────────────

def _note_sync_ok() -> None:
    """Geslaagde cyclus. Liep er een storing, meld dan dat hij voorbij is —
    anders blijft er een rode kaart staan voor iets dat allang werkt."""
    had = failures.note_success(_FAIL_KEY)
    if had:
        from ...shared.outcomes import log_outcome
        log_outcome(
            "Bridge", "sync_hersteld",
            f"Bridge-sync werkt weer na {had} mislukte pogingen op rij.",
            artifact=BRIDGE_REMOTE_URL,
            next_step="Niets — Iris Remote toont weer de actuele stand.",
        )


def _note_sync_failed(exc: BaseException) -> None:
    """Eén mislukte cyclus. Een blip (wifi weg, Vercel koud) is geen inbox-item;
    een verkeerd token of een dode URL wél, en meteen — daar helpt wachten niet.

    Waarom dit hier moet: bij een mislukte push blijft de telefoon vrolijk de
    láátst gepushte stand tonen. Zonder deze kaart is een kapotte bridge lokaal
    onzichtbaar en onderweg alleen te zien als een grijs 'Nu offline'-pilletje
    op een week oude lijst — de faalmodus waarvoor `shared/failures.py` bestaat.
    """
    detail = failures.describe_exception(exc)
    klass = failures.classify(exc)
    failures.note_failure(_FAIL_KEY, detail, klass)
    if not failures.should_escalate(_FAIL_KEY, exc):
        return
    steps = {
        failures.CLASS_AUTH: "Controleer of BRIDGE_TOKEN in .env exact gelijk is aan "
                             "de BRIDGE_TOKEN-env-var in Vercel.",
        failures.CLASS_CONFIG: f"Controleer BRIDGE_REMOTE_URL ({BRIDGE_REMOTE_URL or 'leeg'}) "
                               "en of de Vercel-deploy nog leeft.",
    }
    from ...shared.outcomes import log_outcome
    log_outcome(
        "Bridge", "sync_failed",
        f"Bridge-sync naar Iris Remote mislukt ({klass}): {detail}",
        artifact=BRIDGE_REMOTE_URL,
        next_step=steps.get(klass, "Test met POST /api/bridge/sync-now en controleer "
                                   "de Vercel-logs; tot die tijd toont Iris Remote een "
                                   "verouderde stand."),
        status="error",
    )
    failures.mark_escalated(_FAIL_KEY)


def report_misconfiguration() -> None:
    """Half ingevulde bridge: iemand wilde dit aanzetten en bleef steken. Dat is
    een mens-alleen fout, dus meteen melden in plaats van elke 3 minuten stil
    overslaan."""
    missing = _missing_setting()
    if not missing:
        return
    key = f"bridge:config:{missing}"
    failures.note_failure(key, f"{missing} ontbreekt", failures.CLASS_CONFIG)
    if failures.streak(key).get("escalated"):
        return
    from ...shared.outcomes import log_outcome
    log_outcome(
        "Bridge", "niet_geconfigureerd",
        f"Iris Remote staat half ingesteld: {missing} ontbreekt in .env, dus de "
        "sync slaat elke ronde over en de telefoon toont een bevroren stand.",
        next_step=f"Zet {missing} in .env en herstart ImpactOS (impactos_service.cmd).",
        status="error",
    )
    failures.mark_escalated(key)


def _store_note(note: Dict[str, Any]) -> bool:
    """Notitie van onderweg → markdown in de vault (map 'Onderweg'). Zonder
    vault-pad loggen we alleen een uitkomst-kaart, dan is de tekst niet kwijt."""
    from ...shared.config import OBSIDIAN_VAULT_PATH
    text = (note.get("text") or "").strip()
    if not text:
        return True  # lege notitie: ack'en en vergeten
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
    artifact = ""
    try:
        if OBSIDIAN_VAULT_PATH:
            from pathlib import Path
            folder = Path(OBSIDIAN_VAULT_PATH) / "Onderweg"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"Onderweg {stamp} (#{note.get('id')}).md"
            path.write_text(f"# Notitie onderweg — {stamp}\n\n{text}\n", encoding="utf-8")
            artifact = str(path)
        from ...shared.outcomes import log_outcome
        log_outcome(
            "Bridge", "note_synced",
            f"Notitie van onderweg binnengehaald: {text[:160]}",
            artifact=artifact,
            next_step="Verwerk de notitie (vault-map 'Onderweg') of voer 'm aan Iris' kennisbank.",
        )
        return True
    except Exception:
        logger.exception("Bridge: notitie opslaan mislukt (blijft pending in de cloud)")
        return False


async def _apply(decision: Dict[str, Any]) -> tuple:
    from . import actions
    ok, message = await actions.apply_decision(decision)
    # Een mislukt besluit is menselijke actie waard: het stond onderweg als
    # "gedaan" in Vincents hoofd, maar gebeurde niet.
    if not ok:
        try:
            from ...shared.outcomes import log_outcome
            log_outcome(
                "Bridge", "remote_decision_failed",
                f"Besluit onderweg ({decision.get('item_kind')}/{decision.get('action')} "
                f"op {decision.get('item_id')}) kon niet uitgevoerd worden: {message}",
                next_step="Voer de actie handmatig uit in het Actiecentrum.",
                status="error",
            )
        except Exception:
            logger.exception("Bridge: uitkomst-kaart loggen mislukt")
    return ok, message


def last_sync() -> Dict[str, Any]:
    return dict(_last_sync)


def remote_url() -> str:
    return BRIDGE_REMOTE_URL


def failure_streak() -> Dict[str, Any]:
    """De lopende storing (leeg = het draait). Staat in SQLite, dus na een
    herstart blijft "faalt al uren" leesbaar als storing en niet als 'nooit
    gedraaid'."""
    return failures.streak(_FAIL_KEY)
