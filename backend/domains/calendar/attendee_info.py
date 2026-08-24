"""Info-knop op de Agenda-tab: wie is deze deelnemer, en waar moet Vincent op
letten vóór de meeting.

Anders dan de rest van dit domein is "wie is dit" geen vormvraag maar een
inhoudsvraag — er is geen deterministisch antwoord, dus dit is bewust de ene
plek in `calendar/` die wél websearch + een LLM gebruikt. Dat maakt de regels
strenger, niet losser: `websearch.search` gooit door als alle providers falen
(nooit stil een lege lijst, zie shared/websearch.py) en de systeemprompt
verbiedt expliciet te verzinnen wat niet in de zoekresultaten staat — zelfde
FEITEN_GRONDWET-afweging als `publish/article_writer.py` en
`gauntlet/brand_brief.py`: een verzonnen functietitel of trackrecord over een
echte deelnemer is precies de fout die daar al eerder is gebeurd, nu één
gesprekspartner dichterbij.

Cache per (naam+e-mail), 30 dagen: dezelfde deelnemer komt in meerdere
afspraken terug, en een LinkedIn-/bedrijfsprofiel verandert niet per meeting
— zonder cache betaalt elke klik op de knop opnieuw een websearch + LLM-call
voor exact dezelfde vraag.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn

log = logging.getLogger(__name__)

_CACHE_DAYS = 30
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "hotmail.nl", "outlook.com", "outlook.nl",
    "live.com", "live.nl", "icloud.com", "yahoo.com", "yahoo.nl",
    "protonmail.com", "me.com", "ziggo.nl", "kpnmail.nl", "telfort.nl",
}


def _cache_key(name: str, email: str) -> str:
    raw = (email or name or "").strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _company_hint(email: str) -> str:
    """Bedrijfsdomein uit het e-mailadres — leeg bij een publieke provider,
    want 'gmail.com' is geen bedrijfsnaam om op te zoeken."""
    domain = (email or "").split("@")[-1].strip().lower()
    if not domain or domain in _PUBLIC_EMAIL_DOMAINS:
        return ""
    return domain


def _from_cache(key: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary, updated_at FROM calendar_attendee_briefings WHERE cache_key=?",
            (key,),
        ).fetchone()
    if not row or not row["summary"]:
        return None
    try:
        updated = datetime.fromisoformat(row["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if datetime.now(timezone.utc) - updated > timedelta(days=_CACHE_DAYS):
        return None
    return {"summary": row["summary"], "cached": True}


def _save_cache(key: str, name: str, email: str, summary: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO calendar_attendee_briefings (cache_key, name, email, summary, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET
                 summary=excluded.summary, updated_at=excluded.updated_at""",
            (key, name, email, summary, now),
        )


async def build_briefing(name: str, email: str = "", event_title: str = "",
                          event_description: str = "", force: bool = False) -> Dict:
    """Zoek + vat samen wie deze deelnemer is.

    Gooit door bij een echte fout (zoekproviders uitgeput, LLM onbereikbaar)
    — geen stille lege briefing, de knop toont dan expliciet dat het niet
    lukte in plaats van een leeg kaartje dat als "niets te vinden" leest."""
    name = (name or "").strip()
    if not name:
        return {"summary": "Geen naam bekend voor deze deelnemer.", "cached": False}

    key = _cache_key(name, email)
    if not force:
        cached = _from_cache(key)
        if cached:
            return cached

    from ...shared import websearch

    company = _company_hint(email)
    queries = [f"{name} {company}".strip() if company else name]
    if company:
        queries.append(company)

    results: List[Dict] = []
    errors: List[str] = []
    for q in queries:
        try:
            results.extend(websearch.search(q, max_results=4))
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    if not results:
        detail = f" ({errors[0]})" if errors else ""
        raise RuntimeError(f"Geen zoekresultaten gevonden{detail} — probeer het later opnieuw.")

    sources = "\n".join(
        f"- {r.get('title', '')} ({r.get('url', '')}): {(r.get('snippet') or '')[:300]}"
        for r in results[:8]
    )
    context = f"Afspraak: {event_title}\n{event_description}".strip()

    from ..chat import claude
    system = (
        "Je bent Iris, de manager-agent van Vincent van Munster (WeAreImpact). "
        "Vincent heeft binnenkort een meeting en wil in een paar zinnen weten "
        "met wie hij te maken heeft, vóórdat hij naar binnen loopt.\n\n"
        "Gebruik UITSLUITEND de meegegeven zoekresultaten. Verzin nooit een functie, "
        "bedrijf, trackrecord of ander feit dat niet letterlijk in de resultaten staat "
        "— bevatten de resultaten niets bruikbaars over deze persoon of organisatie, "
        "zeg dat expliciet in plaats van iets te verzinnen.\n\n"
        "Antwoord kort (max 5 regels, gewone tekst): wie is deze persoon, bij welk "
        "bedrijf/organisatie, wat doet dat bedrijf, en één concreet aandachtspunt voor "
        "het gesprek als daar iets voor te vinden is. Geen opsomming van bronnen, "
        "geen markdown-koppen, geen liggende streepjes of emoji's."
    )
    prompt = (
        f"Deelnemer: {name}" + (f" ({email})" if email else "")
        + (f"\n{context}" if context else "")
        + f"\n\nZoekresultaten:\n{sources}"
    )
    summary = await claude.get_response(
        [{"role": "user", "content": prompt}], system_prompt=system,
        max_tokens=500, purpose="calendar-attendee-info",
    )
    summary = summary.strip()
    _save_cache(key, name, email, summary)
    return {"summary": summary, "cached": False}
