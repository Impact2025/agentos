"""
Pijlerkaart — per site: welke zoekterm hoort bij welke ene URL.

Aanleiding (27 sep 2026): voor Bewaard voor Jou stonden 16 concepten in de
wachtrij, waarvan er minstens zes een pagina dupliceerden die al rankte ('Je
levensverhaal vastleggen' naast /levensverhaal-vastleggen, twee kosten-
artikelen naast de kostenpagina op positie 4,2, …). Geen van de bestaande
controles ving ze: `create_job` ontdubbelt op `keyword`, en de Gauntlet- en
goal-concepten hebben een leeg keyword; `is_same_topic` eist overlap in twee
richtingen, en een lange concepttitel met extra woorden haalt die nooit.
Tegelijk stelde de Optimizer interne links voor die precies de verkeerde kant
op wezen ('levensboek maken' → /kennisbank), omdat hij de pagina volgt die
nú toevallig vertoont.

Een fuzzy matcher strenger of losser zetten verschuift alleen de fouten. Dit
is een expliciete, menselijke keuze: per zoekterm één pagina. Volgorde telt —
de eerste passende regel wint, dus zet specifieke termen vóór algemene.
"""
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# domein (zonder www) → [(zoekterm, pad van de pijlerpagina)]
PILLARS: Dict[str, List[Tuple[str, str]]] = {
    "bewaardvoorjou.nl": [
        ("levensverhaal kost", "/kennisbank/levensverhaal-laten-schrijven-kosten"),
        ("biografie laten schrijven", "/blog/biografie-laten-schrijven-de-complete-gids-voor-jouw-levensv"),
        ("levensboek maken", "/kennisbank/van-digitaal-verhaal-naar-tastbaar-levensboek-exporteren"),
        ("levensverhaal vastleggen", "/levensverhaal-vastleggen"),
        ("levensverhaal opschrijven", "/levensverhaal-opschrijven"),
        ("levensverhaal schrijven", "/levensverhaal-opschrijven"),
        ("levensverhaal schrijf", "/levensverhaal-opschrijven"),  # f/v: 'schrijf' ≠ prefix van 'schrijven'
        ("kraamcadeau alles", "/kennisbank/kraamcadeau-ouders-die-al-alles-hebben"),
        ("cadeau ouders alles", "/blog/7-persoonlijke-cadeaus-voor-ouders-die-alles-al-hebben"),
        ("cadeau 70 jaar", "/kennisbank/cadeau-70-jaar-originele-ideeen"),
        ("memoires schrijven", "/kennisbank/memoires-schrijven-voorbeelden-en-tips"),
        ("interview ouders", "/kennisbank/interview-ouders-25-vragen"),
        ("autobiografie hulp", "/autobiografie-hulp"),
    ],
}

_STOPWOORDEN = {
    "de", "het", "een", "en", "of", "je", "jouw", "jij", "zo", "wat", "hoe",
    "van", "voor", "met", "op", "in", "aan", "die", "dat", "al", "wel", "niet",
    "om", "te", "tot", "bij", "naar", "uit", "ze", "wij", "we", "is", "zijn",
}


def _tokens(text: str) -> List[str]:
    woorden = re.findall(r"[0-9a-zà-ÿ]+", (text or "").lower())
    return [w for w in woorden if w not in _STOPWOORDEN]


def _zelfde_woord(a: str, b: str) -> bool:
    """'kost' ~ 'kosten', 'cadeau' ~ 'cadeaus'. Onder 4 tekens alleen exact,
    anders matcht 'op' of 'je' overal."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return a.startswith(b) or b.startswith(a)


def _domein(site: Dict) -> str:
    base = (site or {}).get("base_url") or ""
    host = urlparse(base).netloc or base
    if not host:
        prop = (site or {}).get("gsc_property") or ""
        host = prop.removeprefix("sc-domain:")
    return host.lower().removeprefix("www.").strip("/")


def pillar_for_text(site: Dict, text: str) -> Optional[Tuple[str, str]]:
    """Welke pijler claimt deze tekst (titel/zoekwoord/anker)? Geeft
    (zoekterm, pad) terug, of None. Een pijler past als élk woord van zijn
    zoekterm in de tekst voorkomt."""
    regels = PILLARS.get(_domein(site))
    if not regels:
        return None
    woorden = _tokens(text)
    for term, pad in regels:
        if all(any(_zelfde_woord(t, w) for w in woorden) for t in _tokens(term)):
            return term, pad
    return None


def pillar_conflict(site: Dict, title: str, keyword: str = "", slug: str = "") -> Optional[str]:
    """Reden waarom dit concept een pijlerpagina aanvalt, of None.

    Een concept dat op de slug van de pijler zelf publiceert is een
    verbetering van die pagina en mag. Alles daarbuiten wordt een tweede
    pagina op dezelfde zoekterm."""
    hit = pillar_for_text(site, f"{title or ''} {keyword or ''}")
    if not hit:
        return None
    term, pad = hit
    pijler_slug = pad.rstrip("/").rsplit("/", 1)[-1]
    if slug and slug.strip("/").rsplit("/", 1)[-1] == pijler_slug:
        return None
    return (f"de zoekterm '{term}' is al van {pad} — een tweede artikel "
            f"concurreert met die pagina. Verwerk dit als verbetering van {pad}, "
            f"of wijs het af")
