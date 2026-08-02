"""Is dit zoekresultaat een organisatie, of gewoon een webpagina?

Aanleiding (27 juli 2026): van de 165 leads stonden er 100 op 'lost', en de
namen verrieden waarom — 'Top AI Consulting Companies in the Netherlands',
'[PDF] Haalbaarheidsonderzoek Sociale Kaart', 'De rol van AI in de
gezondheidszorg | Parseur®', 'Prism Slime Boss Fight (Genshin Impact)'. Dat zijn
paginatitels van zoekresultaten, geen bedrijven. `org_name` werd letterlijk
gevuld met `r["title"]` uit de zoekprovider.

Dat is niet alleen rommel in een lijst: elke rij wordt gescraped, door een LLM
geanalyseerd en in de vault gezet, en verpest daarna de conversiecijfers van de
acquisitieformule. De funnel meet dan de kwaliteit van de zoekresultaten in
plaats van de kwaliteit van de verkoop.

Twee taken, bewust gescheiden:
  - `looks_like_organisation()` — hoort deze rij er überhaupt in?
  - `clean_org_name()` — haal de bedrijfsnaam uit een paginatitel.

Bewust regelgebaseerd en niet via een LLM: dit draait vóór de verrijking, juist
om die LLM-call te besparen, en een dure classificatie die zelf kan hallucineren
is hier het verkeerde gereedschap.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlsplit

# Padsegmenten die zeggen "dit is een artikel/vacature op de site van iemand
# anders", niet de organisatie zelf.
_ARTICLE_PATH_PARTS = {
    "blog", "blogs", "nieuws", "news", "artikel", "artikelen", "article",
    "publicaties", "publicatie", "kennisbank", "insights", "cases", "case",
    "vacature", "vacatures", "vacancy", "jobs", "job", "werken-bij",
    "opdracht", "opdrachten", "whitepaper", "webinar", "event", "agenda",
    "pulse", "posts", "post", "story", "stories", "magazine", "podcast",
}

# Domeinen die per definitie geen prospect zijn: portals, aggregators, encyclopedieën.
_AGGREGATOR_DOMAINS = (
    "wikipedia.org", "linkedin.com", "indeed.com", "indeed.nl", "glassdoor",
    "nationalevacaturebank.nl", "monsterboard.nl", "jobbird.com", "boomingjobs",
    "youtube.com", "facebook.com", "x.com", "twitter.com", "instagram.com",
    "reddit.com", "medium.com", "substack.com", "marktplaats.nl", "google.com",
    "rijksoverheid.nl", "overheid.nl", "europa.eu", "researchgate.net",
    "scholar.google", "sciencedirect.com", "booking.com", "tripadvisor",
)

# Titels die met een vraag of een opsomming beginnen zijn artikelen.
_ARTICLE_TITLE_PATTERNS = (
    re.compile(r"^\s*\[?pdf\]?\b", re.I),
    re.compile(r"^\s*(top|beste?|de\s+beste|meest|leading)\s+\d*\s*\w", re.I),
    # Engelstalige gidsen en vergelijkingen komen net zo vaak voorbij.
    re.compile(r"\b(complete\s+guide|ultimate\s+guide|buyer'?s\s+guide|"
               r"comparison|reviewed|explained)\b", re.I),
    re.compile(r"^\s*\d+\s+(beste|tips|manieren|redenen|stappen|voorbeelden|"
               r"trends|valkuilen|ideeen|ideeën)\b", re.I),
    re.compile(r"^\s*(wat|hoe|waarom|wanneer|welke|wie)\s+(is|zijn|doe|kun|kan|"
               r"moet|werkt|kies|vind)\b", re.I),
    re.compile(r"^\s*(de|het)\s+(rol|opkomst|toekomst|impact|voordelen|nadelen|"
               r"gevaren|belofte)\s+van\b", re.I),
    re.compile(r"^\s*(gids|handleiding|checklist|overzicht|vergelijking|review|"
               r"rapport|onderzoek|analyse|column|interview|verslag)\b[:\s]", re.I),
)

# Woorden die een vacature verraden.
_VACANCY_MARKERS = (
    "vacature", "vacatures", "gezocht", "wij zoeken", "we zoeken", " m/v",
    "(m/v", "fulltime", "parttime", " fte", "interim opdracht", "detachering",
    "solliciteer",
)

# Merk-scheidingstekens in een paginatitel: "Onderwerp | Merknaam".
_BRAND_SEPARATORS = ("|", "—", "–", " - ", " · ", "»", "::")

# Juridische vormen: sterk bewijs dat een stuk tekst een bedrijfsnaam is.
_LEGAL_FORMS = re.compile(
    r"\b(b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|c\.?v\.?|holding|group|groep|stichting|"
    r"vereniging|coöperatie|cooperatie|partners|consultancy|advies|adviesbureau|"
    r"agency|studio|labs?|solutions|systems|software|ict|zorg|kliniek|praktijk)\b",
    re.I,
)


def _domain(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _path_parts(url: str) -> list[str]:
    try:
        return [p for p in urlsplit(url).path.lower().split("/") if p]
    except ValueError:
        return []


def looks_like_organisation(title: str, url: str = "",
                            snippet: str = "") -> Tuple[bool, str]:
    """(geschikt, reden-als-niet). De reden is bedoeld om te loggen."""
    t = (title or "").strip()
    if len(t) < 2:
        return False, "geen titel"

    domain = _domain(url)
    if domain and any(a in domain for a in _AGGREGATOR_DOMAINS):
        return False, f"{domain} is een portal/aggregator, geen organisatie"

    parts = _path_parts(url)
    if url and parts and parts[0] in _ARTICLE_PATH_PARTS:
        return False, f"URL wijst naar een artikel-/vacaturepagina (/{parts[0]}/)"
    if any(p in _ARTICLE_PATH_PARTS for p in parts):
        return False, "URL bevat een artikel-/vacaturepad"
    if url.lower().endswith(".pdf"):
        return False, "PDF-document, geen organisatiepagina"

    haystack = f"{t} {snippet}".lower()
    marker = next((m for m in _VACANCY_MARKERS if m in haystack), None)
    if marker:
        return False, f"lijkt een vacature ('{marker.strip()}')"

    for pattern in _ARTICLE_TITLE_PATTERNS:
        if pattern.search(t):
            return False, "titel leest als een artikel, niet als een bedrijfsnaam"

    if t.rstrip().endswith("?"):
        return False, "titel is een vraag — dat is een artikel"

    # Een titel die na het afsplitsen van het merk-suffix nog steeds een halve
    # zin is, beschrijft een onderwerp en geen organisatie. Bewust meten op het
    # langste titeldeel en niet op clean_org_name(): die valt bij een lange
    # titel terug op de domeinnaam, en die is per definitie kort — dan keurt de
    # lengtecheck zichzelf altijd goed.
    delen = [t]
    for sep in _BRAND_SEPARATORS:
        if sep in t:
            delen = [s.strip() for s in t.split(sep) if s.strip()] or [t]
            break
    langste = max(delen, key=lambda s: len(s.split()))
    if len(langste.split()) > 7 and not _LEGAL_FORMS.search(langste):
        return False, "titel is een zin, geen bedrijfsnaam"

    return True, ""


def clean_org_name(title: str, url: str = "") -> str:
    """Haal de organisatienaam uit een paginatitel.

    'Mensgericht digitaliseren voor social en non-profit - digiraf' → 'digiraf'.
    Zoekresultaten zetten het merk vrijwel altijd achteraan, achter een
    scheidingsteken. Levert dat niets bruikbaars op, dan valt hij terug op de
    domeinnaam — die is altijd nog een betere bedrijfsnaam dan een halve zin.
    """
    t = (title or "").strip()
    if not t:
        return _domain_as_name(url)

    kandidaten = [t]
    for sep in _BRAND_SEPARATORS:
        if sep in t:
            stukken = [s.strip() for s in t.split(sep) if s.strip()]
            if len(stukken) >= 2:
                kandidaten = stukken
                break

    if len(kandidaten) >= 2:
        # Het merk staat achteraan, maar niet als dat een losse kreet is
        # ('Home', 'Nederland') of juist weer een hele zin.
        for stuk in reversed(kandidaten):
            woorden = stuk.split()
            if stuk.lower() in {"home", "homepage", "nederland", "nl", "official"}:
                continue
            if 1 <= len(woorden) <= 5:
                return _trim_naam(stuk)

    naam = _trim_naam(kandidaten[0])
    if len(naam.split()) > 7:
        return _domain_as_name(url) or naam
    return naam


def _trim_naam(naam: str) -> str:
    # Handelsmerk-tekens en achterblijvende leestekens weg.
    naam = re.sub(r"[®™©]", "", naam or "").strip(" -–—|·:,.")
    return re.sub(r"\s+", " ", naam)


def _domain_as_name(url: str) -> str:
    domain = _domain(url)
    if not domain:
        return ""
    return domain.split(".")[0].replace("-", " ").strip()


def usable_contact(lead: dict) -> Optional[str]:
    """Heeft deze lead iets waarmee je hem kunt benaderen?

    Zonder e-mail, telefoon of KvK-nummer is een lead geen voorraad maar ruis:
    hij kan de funnel nooit voorbij 'enriched' komen.
    """
    for veld in ("email", "phone", "kvk_number"):
        if (lead.get(veld) or "").strip():
            return veld
    return None
