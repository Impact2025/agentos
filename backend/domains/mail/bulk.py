"""Is deze mail aan míj geschreven, of aan een lijst?

Waarom dit bestand bestaat (1 aug 2026)
───────────────────────────────────────
Na vijf dagen offline stonden er vijf concept-antwoorden klaar op
nieuwsbrieven van Eurostar, GetYourGuide, HomeExchange, SkyShowtime en
Offertevergelijker — en één agenda-voorstel voor 30 mei 2027, gedestilleerd
uit een nieuwsbriefartikel over Apple en AI. Geen daarvan was een vraag van
een mens.

De oorzaak zat niet in één ontbrekend trefwoord maar in de methode. De oude
classifier telde losse hints als sub-strings over de héle body:

  * "unsubscribe" stond in de lijst, "uitschrijven" en "afmelden" niet —
    dus Nederlandse marketingmail passeerde de nieuwsbrief-check volledig.
  * Daarna volstond `"?" + één vraag-hint` voor het oordeel 'question'. In
    een marketingmail van 2000 tekens zit altijd een vraagteken, en "wat"
    matcht binnen "watersport", "hoe" binnen "schoenen". Vals-positief is
    daarmee geen risico maar een zekerheid.
  * Voor 'appointment' gold hetzelfde: "hebben we" komt in élk artikel van
    7000 tekens voor. Zo werd een nieuwsbrief een afspraak-verzoek.

Deze module vervangt dat door het signaal dat er wél toe doet: bulkmail
identificeert zichzelf. Wie een mailing verstuurt zet `List-Unsubscribe` of
`Precedence: bulk` in de headers — dat is de RFC-conforme manier om te
zeggen "dit is een verzending, geen gesprek". Een mens die jou een vraag
mailt doet dat nooit. Headers zijn daarmee bewijs; tekst-heuristiek is
slechts een vangnet voor mailers die zich niet aan de RFC houden.

De regel die volgt is simpel en hard: **op een verzending antwoord je niet,
en je plant er geen afspraak uit in.** Liever een enkele echte vraag missen
dan tien concepten die Vincent met de hand moet weggooien — dezelfde afweging
die `classify.py` al maakte, nu op een signaal dat wél discrimineert.
"""
import re
from typing import Dict, Iterable, Optional, Tuple

# ── Headers die bulk bewijzen ───────────────────────────────────────────────
# List-Unsubscribe/List-Id: RFC 2369/2919, gezet door élke serieuze mailer.
# Precedence: bulk|list|junk: de oudere conventie, nog steeds in gebruik.
# Auto-Submitted: RFC 3834, out-of-office en andere automaten.
_BULK_HEADERS = (
    "list-unsubscribe",
    "list-unsubscribe-post",
    "list-id",
    "x-mailer-list",
    "x-campaign-id",
    "x-mailchimp-campaign",
    "feedback-id",
)
_PRECEDENCE_BULK = ("bulk", "list", "junk", "auto_reply")

# ── Afmeld-frases in de body (vangnet, meertalig) ───────────────────────────
# Aanleiding: de oude lijst kende alleen Engelse termen, terwijl vrijwel alle
# mail die Vincent krijgt Nederlands is. Deze frases staan in de footer van
# praktisch elke legitieme mailing.
_UNSUB_PHRASES = (
    # Nederlands
    "uitschrijven", "uitschrijf", "afmelden", "afmeld je", "afmelding",
    "geen mails meer", "deze e-mail is verstuurd naar",
    "deze mail is verstuurd naar", "je ontvangt deze e-mail omdat",
    "je ontvangt deze mail omdat", "u ontvangt deze e-mail omdat",
    "voorkeuren beheren", "e-mailvoorkeuren", "mailvoorkeuren",
    "bekijk online", "bekijk in je browser", "niet meer ontvangen",
    "wil je deze e-mails niet meer",
    # Engels
    "unsubscribe", "opt out", "opt-out", "manage preferences",
    "email preferences", "manage your subscription", "view in browser",
    "view this email in", "you received this email because",
    "you are receiving this",
    # Duits/Frans (komt voor bij internationale merken)
    "abmelden", "se désabonner", "désabonnement",
)

# ── Afzender-patronen (zwak signaal, alleen ter bevestiging) ────────────────
# Marketingplatforms versturen vanaf eigen subdomeinen: mkt., email., e.,
# mail., news., info. — op zichzelf onvoldoende bewijs, maar in combinatie
# met een afmeld-frase overtuigend.
_MARKETING_LOCALPARTS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "newsletter", "nieuwsbrief", "mailing", "marketing", "campaign",
    "notifications", "notificatie",
)
_MARKETING_SUBDOMAINS = (
    "mkt.", "email.", "e.", "mail.", "news.", "nieuws.", "m.", "em.",
    "sendgrid.", "mailchimp", "mailgun", "sparkpostmail", "beehiiv",
    "substack", "klaviyo", "hubspot", "cmail", "createsend",
)


def _headers_to_map(headers) -> Dict[str, str]:
    """Normaliseer headers naar {lowercase-naam: waarde}.

    Slikt de drie vormen waarin ze bij ons binnenkomen: een echt
    email.message.Message (POP3), de Graph-lijst met {name, value}-dicts, en
    een gewone dict. Eén functie, zodat POP3 en Graph gegarandeerd hetzelfde
    oordeel krijgen — een filter dat maar op één transportpad werkt, laat het
    andere pad ongefilterd door.
    """
    out: Dict[str, str] = {}
    if not headers:
        return out
    # Graph: [{"name": "List-Unsubscribe", "value": "<...>"}, ...]
    if isinstance(headers, (list, tuple)):
        for h in headers:
            if isinstance(h, dict):
                naam = (h.get("name") or "").strip().lower()
                if naam:
                    out[naam] = str(h.get("value") or "")
        return out
    # email.message.Message heeft .items(); dict ook.
    items: Optional[Iterable] = None
    if hasattr(headers, "items"):
        try:
            items = headers.items()
        except Exception:
            items = None
    if items:
        for naam, waarde in items:
            if naam:
                out[str(naam).strip().lower()] = str(waarde or "")
    return out


def bulk_reason(headers=None, from_addr: str = "", subject: str = "",
                body: str = "") -> Optional[str]:
    """Waarom is dit bulkmail? Geeft de reden terug, of None bij persoonlijke mail.

    Volgorde is bewust: eerst het harde bewijs uit de headers, dan pas de
    tekst-heuristiek. Zo staat er in het logboek altijd de sterkste reden, en
    niet een toevallige woordmatch terwijl de mail zichzelf al als mailing
    aankondigde.
    """
    hdrs = _headers_to_map(headers)
    for naam in _BULK_HEADERS:
        if hdrs.get(naam):
            return f"header {naam} aanwezig — dit is een verzending, geen gesprek"
    prec = (hdrs.get("precedence") or "").strip().lower()
    if prec in _PRECEDENCE_BULK:
        return f"Precedence: {prec} — bulkverzending"
    if hdrs.get("auto-submitted", "").strip().lower() not in ("", "no"):
        return "Auto-Submitted — automatisch gegenereerd bericht"

    b = (body or "").lower()
    frm = (from_addr or "").lower()
    afmeld = next((p for p in _UNSUB_PHRASES if p in b), None)
    if afmeld:
        return f"afmeld-instructie in de tekst ('{afmeld}') — dit is een mailing"

    # Zonder afmeld-frase is een marketing-afzender op zichzelf te zwak: een
    # echt mens mailt ook wel eens vanaf info@ of vanaf een mail.-subdomein.
    # Alleen de expliciet onbeantwoordbare adressen zijn hier beslissend.
    lokaal = frm.split("@", 1)[0]
    if any(p in lokaal for p in ("noreply", "no-reply", "no_reply",
                                 "donotreply", "do-not-reply")):
        return "afzender is een no-reply-adres — een antwoord komt nergens aan"
    return None


def is_bulk(headers=None, from_addr: str = "", subject: str = "",
            body: str = "") -> bool:
    return bulk_reason(headers, from_addr, subject, body) is not None


def looks_like_marketing_sender(from_addr: str) -> bool:
    """Zwak signaal: ruikt dit adres naar een marketingplatform?

    Alleen bedoeld als extra gewicht náást een ander signaal — nooit als
    enige grond om een mail weg te filteren.
    """
    frm = (from_addr or "").lower()
    if "@" not in frm:
        return False
    lokaal, _, domein = frm.partition("@")
    if any(p in lokaal for p in _MARKETING_LOCALPARTS):
        return True
    return any(domein.startswith(s) or f".{s}" in f".{domein}"
               for s in _MARKETING_SUBDOMAINS)


# ── Woordgrens-matching ─────────────────────────────────────────────────────
_WORD_CACHE: Dict[str, "re.Pattern"] = {}


def _pattern(phrase: str) -> "re.Pattern":
    pat = _WORD_CACHE.get(phrase)
    if pat is None:
        # \b werkt niet rond een '?' of andere leestekens; die zoeken we
        # letterlijk. Voor woorden wél woordgrenzen, want dát is precies waar
        # "wat" in "watersport" op stukliep.
        if phrase.strip() and phrase[0].isalnum() and phrase[-1].isalnum():
            pat = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
        else:
            pat = re.compile(re.escape(phrase))
        _WORD_CACHE[phrase] = pat
    return pat


def count_words(text: str, phrases: Iterable[str]) -> Tuple[int, list]:
    """Tel hoeveel van `phrases` als heel woord in `text` voorkomen.

    Geeft (aantal, gevonden-frases) terug — de lijst zodat een logregel kan
    zeggen wáárom iets als vraag of afspraak gold. Zonder die verantwoording
    is een misclassificatie niet te debuggen; dat is precies waarom deze bug
    dagen kon blijven staan.
    """
    gevonden = [p for p in phrases if p and _pattern(p).search(text)]
    return len(gevonden), gevonden
