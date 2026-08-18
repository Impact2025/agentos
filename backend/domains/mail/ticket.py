"""Ticket-notificaties ontpakken.

Sommige projecten sturen supportvragen niet rechtstreeks: hun eigen website
mailt een 'nieuw ticket'-melding vanaf een no-reply-adres, met de échte vraag
en het échte antwoordadres van de klant verpakt in de body (Ticket/Categorie/
Onderwerp/Van/E-mail/Bericht — het contactformulier-sjabloon van
Bewaardvoorjou.nl).

Zonder dit ontpakken ziet de rest van de pipeline alleen een onbeantwoordbare
no-reply-afzender: `bulk.bulk_reason()` herkent 'noreply' in het lokale deel
en concludeert "een antwoord komt nergens aan" — waarna de mail als
'newsletter' wegging, MET de body geleegd (`inbox.fetch_new` slaat bij een
genegeerde mail bewust geen tekst op). Zo bleven twee échte klantvragen
(BVJ-0002, BVJ-0003) sinds 23 jul 2026 onopgemerkt: de wrapper is inderdaad
onbeantwoordbaar, maar de vraag erin niet.
"""
import re
from typing import Dict, Optional

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_FIELD_RE = re.compile(r"(?im)^\s*([a-z\-]+)\s*:\s*(.+?)\s*$")
_BERICHT_RE = re.compile(r"(?im)^\s*bericht\s*:?\s*$")

_FIELD_ALIASES = {
    "ticket": "ticket",
    "categorie": "categorie",
    "onderwerp": "onderwerp",
    "van": "van",
    "e-mail": "email",
    "email": "email",
}


def unwrap_ticket_notification(subject: str, body: str, from_addr: str,
                                own_domain: str) -> Optional[Dict]:
    """Herkent een 'nieuw ticket'-melding van het eigen projectdomein.

    Vereist ALLE drie: afzender op `own_domain`, een 'E-mail:'-veld met een
    adres dat NIET de afzender zelf is (dát onderscheidt een automatische
    ticket-wrapper van een gewone mail die toevallig 'e-mail' bevat), en een
    'Bericht:'-marker met tekst erna. Ontbreekt één daarvan, dan is dit geen
    ticket-wrapper en raakt deze functie niets aan.

    Retourneert {ticket_id, categorie, customer_name, customer_email,
    subject, question} of None.
    """
    dom = (own_domain or "").lower().lstrip("@")
    frm = (from_addr or "").lower()
    if not dom or dom not in frm:
        return None
    body = body or ""
    fields: Dict[str, str] = {}
    for m in _FIELD_RE.finditer(body):
        key = _FIELD_ALIASES.get(m.group(1).strip().lower())
        if key:
            fields[key] = m.group(2).strip()
    email_field = fields.get("email")
    if not email_field:
        return None
    em = _EMAIL_RE.search(email_field)
    if not em:
        return None
    customer_email = em.group(0).lower()
    if customer_email in frm:
        return None  # zelfde adres — geen wrapper, gewoon een gewone mail

    bm = _BERICHT_RE.search(body)
    if not bm:
        return None
    question = body[bm.end():].strip()
    if not question:
        return None

    ticket_id = fields.get("ticket", "")
    onderwerp = fields.get("onderwerp") or subject or ""
    customer_name = fields.get("van") or customer_email.split("@")[0]
    display_subject = f"{ticket_id + ' — ' if ticket_id else ''}{onderwerp}".strip()

    return {
        "ticket_id": ticket_id,
        "categorie": fields.get("categorie", ""),
        "customer_name": customer_name,
        "customer_email": customer_email,
        "subject": display_subject or subject,
        "question": question,
    }
