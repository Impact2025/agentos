"""Linkbuilding-outreach — concepten klaarzetten, nooit zelf versturen.

De batch schrijft per gekwalificeerde linkkans een kort, persoonlijk
mailconcept met een concrete linksuggestie (hun pagina, onze pagina,
ankertekst en een kant-en-klaar HTML-snippet — zo kan de ontvanger de link
in 30 seconden plaatsen). De prospect krijgt status 'outreach_review' en
verschijnt in het Actiecentrum; versturen kan UITSLUITEND via de
approve-endpoint na menselijke goedkeuring (de Wachtrij-gate-regel).

Bij het klaarzetten wordt meteen de bijbehorende link_placement (pending)
aangemaakt, zodat de monitor weet wáár hij de link moet gaan zoeken.
"""
import logging
import re
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.config import LINKBUILD_WEEKLY_TARGET
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import service

logger = logging.getLogger(__name__)

# Adressen die nooit een redactie/eigenaar zijn. Let op: dit is bewust een
# ándere lijst dan bij de acquisitie — info@ en redactie@ zijn bij linkbuilding
# juist wél het goede adres (de redactie ís hier de doelgroep).
_BLOCKED_LOCAL = {
    "noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster",
    "abuse", "privacy", "webmaster",
}
_INVALID_DOMAINS = {"voorbeeld.nl", "example.com", "example.nl", "test.nl", "localhost"}

_SIGNATURE = (
    "Hartelijke groet,\n"
    "Vincent van Munster\n"
    "  E  v.munster@weareimpact.nl\n"
    "  W  weareimpact.nl"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _own_domains() -> set:
    """Eigen domeinen (alle sites in het portfolio) — nooit een prospect."""
    with get_conn() as conn:
        rows = conn.execute("SELECT base_url FROM sites").fetchall()
    doms = {service.norm_domain(r["base_url"]) for r in rows}
    doms.discard("")
    return doms | {"weareimpact.nl", "bewaardvooraltijd.nl"}


def email_ok(addr: str) -> tuple[bool, str]:
    """Valideer een adres als serieus linkbuilding-contact. Returns (ok, reden)."""
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr:
        return False, "geen geldig adres (geen @)"
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", addr):
        return False, "geen syntactisch geldig adres"
    local, _, dom = addr.partition("@")
    if dom in _INVALID_DOMAINS:
        return False, f"placeholder-domein ('{dom}')"
    if dom in _own_domains():
        return False, f"eigen domein ('{dom}')"
    if local in _BLOCKED_LOCAL:
        return False, f"systeem-adres ('{local}@')"
    return True, ""


def select_batch(count: int, site_id: str = "") -> List[Dict[str, Any]]:
    """De beste gekwalificeerde prospects mét contactadres en zonder concept."""
    q = ("SELECT * FROM link_prospects WHERE status = 'qualified' "
         "AND contact_email != '' AND outreach_draft = '' AND lost_at = ''")
    params: list = []
    if site_id:
        q += " AND site_id = ?"
        params.append(site_id)
    q += " ORDER BY relevance_score DESC, created_at ASC LIMIT ?"
    params.append(count)
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows if email_ok(dict(r)["contact_email"])[0]]


def _snippet(prospect: Dict[str, Any]) -> str:
    return f'<a href="{prospect["target_url"]}">{prospect["anchor_text"] or prospect["target_url"]}</a>'


def _draft_prompt(prospect: Dict[str, Any], site: Dict[str, Any]) -> str:
    return (
        "Schrijf een korte, persoonlijke Nederlandse outreach-mail aan de redactie/"
        "eigenaar van een website, met als doel een backlink.\n\n"
        f"Hun site: {prospect['domain']}"
        f"{' — pagina: ' + prospect['url'] if prospect.get('url') else ''}\n"
        f"Waarom zij relevant zijn: {prospect.get('rationale') or '—'}\n"
        f"Type kans: {prospect.get('prospect_type')}\n\n"
        f"Onze site: {site.get('name')} ({site.get('base_url')})\n"
        f"Onze pagina die de link verdient: {prospect['target_url']}\n"
        f"Voorgestelde ankertekst: {prospect.get('anchor_text') or '—'}\n\n"
        "Eisen:\n"
        "- Maximaal 140 woorden, toon: collegiaal en concreet, geen superlatieven of SEO-jargon\n"
        "- Open met iets specifieks over HUN site of pagina (uit 'waarom zij relevant zijn')\n"
        "- Leg in één zin uit wat hun lezers aan onze pagina hebben\n"
        "- Sluit af met de kant-en-klare linksuggestie, letterlijk zo:\n"
        f"  Kant-en-klaar: {_snippet(prospect)}\n"
        "- Bied iets terug aan (bijv. meedenken over hun content) zonder linkruil te beloven\n"
        f"- Onderteken met:\n{_SIGNATURE}\n\n"
        "Antwoord UITSLUITEND met JSON (geen markdown):\n"
        '{"subject": "onderwerpregel van max 60 tekens", "body": "de volledige mailtekst"}'
    )


async def draft_outreach(prospect: Dict[str, Any], site: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Genereer één concept (subject + body) via Claude, Hermes als terugval."""
    from ..publish.content_pipeline import _llm, _extract_json

    system = (
        "Je bent een nuchtere Nederlandse copywriter die linkbuilding-mails schrijft "
        "die gelezen worden omdat ze specifiek, kort en eerlijk zijn."
    )
    raw = await _llm(system, _draft_prompt(prospect, site), max_tokens=700,
                     purpose="linkbuilding")
    if not raw:
        return None
    try:
        data = json.loads(_extract_json(raw))
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
    except Exception:
        logger.warning("[linkbuilding] Onleesbaar concept voor %s — overslaan",
                       prospect.get("domain"))
        return None
    if not subject or not body or len(body) < 80:
        return None
    return {"subject": subject[:120], "body": body}


def _ensure_placement(prospect: Dict[str, Any]) -> None:
    """Maak (of ververs) de pending placement die de monitor gaat controleren."""
    now = _now()
    source = prospect.get("url") or f"https://{prospect['domain']}"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM link_placements WHERE prospect_id = ?",
            (prospect["id"],),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE link_placements SET source_url = ?, target_url = ?, "
                "anchor_text = ?, updated_at = ? WHERE id = ?",
                (source, prospect["target_url"], prospect["anchor_text"], now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO link_placements (id, prospect_id, site_id, source_url, "
                "target_url, anchor_text, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (str(uuid.uuid4()), prospect["id"], prospect["site_id"], source,
                 prospect["target_url"], prospect["anchor_text"], now, now),
            )


async def auto_approve_review_queue(site_id: str = "") -> Dict[str, Any]:
    """Goldie-modus: stuur alle goedgekeurde concepten in één keer verstuurt.

    Gebruikt exact dezelfde verstuur- en validatielogica als de handmatige
    approve-knop (geldig e-mailadres + geldige Outlook-token). Alleen actief
    wanneer LINKBUILD_AUTO_APPROVE aan staat. Verstuurt niets naar een adres
    dat email_ok() afkeurt, en stopt netjes als de Outlook-sessie ongeldig is.
    """
    from ..outlook import service as outlook
    from ...shared.config import LINKBUILD_AUTO_APPROVE

    if not LINKBUILD_AUTO_APPROVE:
        return {"sent": 0, "skipped": 0, "note": "auto-approve staat UIT (LINKBUILD_AUTO_APPROVE)"}
    if not outlook.get_valid_token():
        logger.warning("[linkbuilding] Auto-approve overgeslagen: geen geldige Outlook-sessie")
        return {"sent": 0, "skipped": 0, "note": "geen geldige Outlook-sessie"}

    where = "WHERE status = 'outreach_review'"
    params: list = []
    if site_id:
        where += " AND site_id = ?"
        params.append(site_id)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM link_prospects {where} ORDER BY relevance_score DESC", params
        ).fetchall()
    prospects = [dict(r) for r in rows]

    sent, skipped = 0, 0
    for p in prospects:
        target = (p.get("contact_email") or "").strip()
        subject = (p.get("outreach_subject") or "").strip()
        body = (p.get("outreach_draft") or "").strip()
        ok, why = email_ok(target)
        if not ok or not subject or not body:
            skipped += 1
            continue
        result = await outlook.send_new_email(
            to=target, subject=subject, body_html=body.replace("\n", "<br>"),
        )
        if not result.get("success"):
            skipped += 1
            logger.warning("[linkbuilding] Auto-approve versturen mislukt voor %s: %s",
                           p["domain"], result.get("error", result))
            continue
        service.advance_prospect(p["id"], "contacted")
        sent += 1
        log_outcome(
            "Linkbuilding", "link_outreach_sent",
            f"Auto-verzonden link-outreach aan {p['domain']} ({target}): '{subject}'",
            next_step="De monitor checkt dagelijks of de link geplaatst wordt; "
                      "reply-detectie staat aan.",
        )
    if sent:
        logger.info("[linkbuilding] Auto-approve: %d verstuurd, %d overgeslagen", sent, skipped)
    return {"sent": sent, "skipped": skipped}


async def prepare_linkbuilding_batch(count: int = 0, site_id: str = "") -> Dict[str, Any]:
    """Zet voor de beste linkkansen een outreach-concept klaar ter review.

    Retourneert een batchrapport; verstuurt niets."""
    from ..seo.sites import get_site

    count = count or LINKBUILD_WEEKLY_TARGET
    prospects = select_batch(count, site_id)
    if not prospects:
        return {"drafted": 0, "skipped": 0, "prospects": []}

    drafted, skipped, done = 0, 0, []
    sites_cache: Dict[str, Optional[Dict]] = {}
    now = _now()
    for p in prospects:
        site = sites_cache.setdefault(p["site_id"], get_site(p["site_id"]))
        if not site:
            skipped += 1
            continue
        if not p.get("target_url"):
            p["target_url"] = site.get("base_url") or ""
        draft = await draft_outreach(p, site)
        if not draft:
            skipped += 1
            continue
        with get_conn() as conn:
            conn.execute(
                "UPDATE link_prospects SET status = 'outreach_review', "
                "outreach_subject = ?, outreach_draft = ?, outreach_drafted_at = ?, "
                "target_url = ?, updated_at = ? WHERE id = ?",
                (draft["subject"], draft["body"], now, p["target_url"], now, p["id"]),
            )
        _ensure_placement(p)
        drafted += 1
        done.append({"id": p["id"], "domain": p["domain"], "subject": draft["subject"]})

    if drafted:
        log_outcome(
            "Linkbuilding", "linkbuilding_batch",
            f"{drafted} link-outreach-concept(en) klaargezet ter review"
            + (f" ({skipped} overgeslagen)" if skipped else ""),
            next_step="Keur de concepten goed of wijs ze af in het Actiecentrum — "
                      "pas na jouw klik wordt er verstuurd.",
        )
    logger.info("[linkbuilding] Batch klaar: %d concepten, %d overgeslagen",
                drafted, skipped)
    return {"drafted": drafted, "skipped": skipped, "prospects": done}
