"""De signaalpoort — is dit wel een signaal?

De Mission Radar haalt op, scoort en bewaart. Wat hij tot 3 aug 2026 niet deed,
is zich afvragen of het gevondene überhaupt iets is om op te reageren. Het
resultaat stond zwart op wit in de data: van de twaalf best scorende signalen
van eind juli (score 70-80, match 75-90, allemaal automatisch de vault in) was
er precies één een artikel.

    80  Programmamanager Digitale Transformatie   weareimpact.nl/…      onze eigen pagina
    76  Programmamanager digitale transformatie   weareimpact.nl/blog/… ons eigen artikel
    71  Vacature: consultant sociaal domein       iroko.nl/vacatures/   vacature
    70  Werken in de digitale transformatie       publiekracht.nl/…     werken-bij
    72  Adviesbureau Sociaal Domein               haute-equipe.nl/…     dienstpagina
    72  Sociaal domein                            platform-io.eu/…      overzichtspagina
    79  HBO Verandermanagement in Zorg en Welzijn efficienterwerken…/   opleidingsaanbod
    74  LEGO SERIOUS PLAY - Mintjes en Co         mintjesenco.nl/…      dienstpagina

De relevantie-rechter gaf ze 75-90, en dat was niet fout: ze gáán over het
sociaal domein. Maar "gaat hierover" is niet hetzelfde als "hier kun je iets
mee". Een concurrent die zijn dienstpagina online heeft staan is geen trend, een
vacature is geen ontwikkeling, en onze eigen blog terugvinden is helemaal niets.
Dat oordeel hoort niet bij een LLM te liggen: het is een vorm-vraag, geen
inhouds-vraag, en een vormtoets die zelf een gateway nodig heeft valt stil
precies wanneer de gateway plat ligt (zie `seo/opportunity_quality.py`, dezelfde
afweging, dezelfde dag).

**Wat de vorm verraadt, is het URL-pad.** Niet de padlengte — sommige ruis zit
zes segmenten diep — maar de wóórden erin. Sites zetten publicaties onder
`/blog/`, `/actueel/`, `/nieuws/`, `/dossiers/`; ze zetten hun aanbod onder
`/diensten/`, `/opleiding/`, `/what-we-do/`, `/vacatures/`. Die woordenschat is
opvallend stabiel over talen en CMS'en heen, en op de veertien gemeten gevallen
deelt hij feilloos.

De volgorde is bewust:

  1. **Publicatie-marker gevonden** → doorlaten, punt. Dit staat vóór alle
     filters omdat een blog-artikel over de eigen diensten nog steeds een
     artikel is. Zonder deze voorrang zou `/blog/onze-diensten` sneuvelen op
     het woord 'diensten'.
  2. **Eigen site** → weg. Zelfreferentie is de enige categorie waar het
     antwoord nooit "misschien" is.
  3. **Aanbod-/navigatiemarker of een kaal pad** → weg.
  4. **Anders** → doorlaten. Onbekend is geen ruis; de poort mag alleen weren
     wat hij kan aanwijzen.

Niets verdwijnt stil: een geweerd signaal wordt gewoon opgeslagen met
`status='uitgefilterd'` en de reden erbij, en is met één knop alsnog op te
pakken. Een poort die zijn afwijzingen weggooit is niet te controleren, en dan
weet je over drie maanden niet meer of hij te streng of te soepel staat.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

REASON_LABELS = {
    "eigen-site": "Onze eigen pagina",
    "vacature": "Vacature of werken-bij",
    "aanbodpagina": "Dienst- of aanbodpagina",
    "navigatie": "Navigatie- of overzichtspagina",
    "naslagwerk": "Encyclopedie of naslagwerk",
    "geen-inhoud": "Geen inhoud om op te reageren",
}

# Naslagwerken bewegen niet. Een Wikipedia-lemma kan honderd keer relevant zijn
# en is nóóit een trend: er is geen moment waarop je erop reageert. In de
# voorraad van eind juli zaten er zestien, waaronder 'Murakami (keizer)'.
_NASLAG_HOSTS = ("wikipedia.org", "wiktionary.org", "wikidata.org",
                 "britannica.com", "encyclo.nl", "woorden.org")

# Een scrape die geen titel wist te vinden geeft de URL of een placeholder
# terug. 268 van de 1782 signalen uit de laatste twee weken heetten letterlijk
# 'Link to reddit.com'. Zonder titel is er niets te beoordelen — niet door de
# relevantie-rechter, niet door een mens.
_TITELLOOS = re.compile(r"^(link to\s+|https?://|www\.)", re.I)

# ── De woordenschat van het pad ─────────────────────────────────────────────

# Segmenten die zeggen "hier publiceert iemand iets". Deze winnen van álle
# filters hieronder: een artikel blijft een artikel, ook als het over diensten
# of vacatures gáát.
_PUBLICATIE_MARKERS = {
    "blog", "blogs", "actueel", "actualiteit", "nieuws", "news", "verhalen",
    "artikel", "artikelen", "article", "articles", "insights", "inzichten",
    "kennisbank", "kennis", "dossier", "dossiers", "magazine", "post", "posts",
    "publicaties", "publications", "whitepaper", "whitepapers", "podcast",
    "column", "columns", "opinie", "interview", "interviews", "onderzoek",
    "research", "rapport", "rapporten", "story", "stories", "case-study",
    "resources", "library", "watch", "shorts", "comments", "r", "wiki",
}

# Segmenten die zeggen "hier verkoopt of navigeert iemand". Geen publicatie,
# dus geen trend om op te reageren.
_AANBOD_MARKERS = {
    # aanbod
    "diensten", "dienst", "services", "service", "producten", "product",
    "oplossingen", "solutions", "aanbod", "wat-we-doen", "what-we-do",
    "expertise", "specialismen", "domeinen", "sectoren", "branches",
    "opleiding", "opleidingen", "training", "trainingen", "cursus",
    "cursussen", "workshop", "workshops", "certification", "certificering",
    "tarieven", "prijzen", "pricing", "abonnementen", "shop", "webshop",
    # navigatie / bedrijfspagina's
    "over-ons", "overons", "about", "about-us", "over", "team", "contact",
    "strategie", "themas", "thema", "missie", "organisatie", "bestuur",
    "projecten", "portfolio", "cases", "klanten", "referenties", "partners",
    "zoeken", "search", "sitemap", "tag", "tags", "categorie", "category",
    "inloggen", "login", "account", "privacy", "voorwaarden", "disclaimer",
}

# Vacature-markers krijgen een eigen klasse: het is de grootste categorie en de
# reden leest voor een mens anders ("vacature" zegt meer dan "aanbodpagina").
_VACATURE_MARKERS = {
    "vacature", "vacatures", "vacancy", "vacancies", "jobs", "job", "careers",
    "carriere", "werkenbij", "werken-bij", "solliciteren", "sollicitatie",
    "recruitment", "traineeship", "stage", "stages",
}

_VACATURE_HOSTS = ("werkenbij.", "vacatures.", "jobs.", "careers.")

# Titel-vangnet voor het geval het pad niets prijsgeeft (`/p/12345`).
_VACATURE_TITEL = re.compile(
    r"\b(vacature|vacatures|gezocht|wij zoeken|we zoeken|solliciteer|"
    r"sollicitatie|werken bij|dienstverband|uren per week|fte)\b", re.I)


def _segmenten(url: str) -> List[str]:
    """De padsegmenten van een URL, kleingeletterd en zonder bestandsextensie."""
    try:
        pad = urlparse(url).path or ""
    except ValueError:
        return []
    ruw = [s for s in pad.lower().split("/") if s]
    return [re.sub(r"\.(html?|php|aspx?)$", "", s) for s in ruw]


def _mappen_en_woorden(segmenten: List[str]) -> Tuple[Set[str], Set[str]]:
    """(alle segmenten, de losse woorden uit de mápsegmenten).

    Waarom de laatste eruit blijft: dat segment is de slug van de pagina zélf en
    is beschrijvend proza. Een artikel met de slug
    'hoe-je-echt-contact-maakt-met-je-team' zou op het woord 'contact' sneuvelen
    — een terecht signaal weggegooid op een toevallig woord. Mápsegmenten zijn
    daarentegen door een mens gekozen rubrieksnamen ('facilitator-certification',
    'over-ons'), en juist dáár zit de vorminformatie.
    """
    alle = set(segmenten)
    woorden: Set[str] = set()
    for s in segmenten[:-1]:
        woorden.update(w for w in s.split("-") if len(w) > 2)
    return alle, woorden


def _host(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ── Onze eigen domeinen ─────────────────────────────────────────────────────

_eigen_cache: Tuple[float, Set[str]] = (0.0, set())
_EIGEN_TTL = 300.0


def eigen_hosts(use_cache: bool = True) -> Set[str]:
    """De domeinen van onze eigen sites, uit `sites.base_url`.

    Gecachet omdat de poort per scan tientallen keren langskomt en dit een
    tabel is die per week verandert, niet per seconde.
    """
    global _eigen_cache
    nu = time.time()
    if use_cache and _eigen_cache[1] and nu - _eigen_cache[0] < _EIGEN_TTL:
        return _eigen_cache[1]
    hosts: Set[str] = set()
    try:
        with get_conn() as conn:
            for r in conn.execute("SELECT base_url FROM sites WHERE base_url != ''"):
                h = _host(r["base_url"] or "")
                if h:
                    hosts.add(h)
    except Exception:  # noqa: BLE001 — een poort mag nooit op de sites-tabel stuklopen
        logger.debug("[radar-poort] Kon eigen domeinen niet lezen", exc_info=True)
        return _eigen_cache[1]
    _eigen_cache = (nu, hosts)
    return hosts


def invalidate() -> None:
    global _eigen_cache
    _eigen_cache = (0.0, set())


# ── Het oordeel ─────────────────────────────────────────────────────────────

def assess(signal: Dict, *, eigen: Optional[Set[str]] = None) -> Dict:
    """Beoordeel één ruw scanresultaat. `filter_reason` leeg = een echt signaal.

    Geeft altijd een dict met dezelfde sleutels terug, zodat de aanroeper hem
    blind op het resultaat kan plakken.
    """
    uit = {"filter_reason": None, "filter_label": None, "filter_detail": None}
    url = (signal.get("url") or "").strip()
    titel = (signal.get("title") or "").strip()
    if not url:
        return _reden(uit, "geen-inhoud", "geen URL")

    # Geen echte webpagina — bijv. 'gsc://growth/<slug>' (feedback.py:feed_radar,
    # onze eigen GSC-groeikansen teruggezet als signaal). De poort beoordeelt of
    # een gevónden pagina een publicatie is; op een synthetische URL zonder host
    # is elke pad-regel hieronder zinloos (en zou 'gsc://growth/x' bijv. als
    # 'navigatie' wegzetten — een kaal pad dat helemaal geen webpagina is).
    if "://" in url and not url.lower().startswith(("http://", "https://")):
        return uit

    segmenten = _segmenten(url)
    mappen, kruimels = _mappen_en_woorden(segmenten)
    host = _host(url)

    # 0. Geen bruikbare titel — dan valt er niets te beoordelen, door niemand.
    #    Staat vóór de publicatie-marker: een reddit-thread zónder titel is
    #    geen signaal, hoe netjes het pad ook is.
    if _TITELLOOS.match(titel) or _squash(titel) == _squash(host) or len(titel) < 8:
        return _reden(uit, "geen-inhoud",
                      f"titel {titel[:40]!r} zegt niets over de inhoud")

    # 0b. Naslagwerk: relevant kan het zijn, een trend nooit.
    if any(host == n or host.endswith("." + n) for n in _NASLAG_HOSTS):
        return _reden(uit, "naslagwerk", f"{host} — een lemma beweegt niet")

    # 1. Onze eigen site. Staat vóór álles, ook vóór de publicatie-marker: dat
    #    de radar ons eigen blogartikel terugvindt zegt alleen dat Google het
    #    geïndexeerd heeft. Zonder deze voorrang glipte
    #    'weareimpact.nl/blog/digitale-transformatie-…' er als "trend" doorheen
    #    — een artikel dat dit systeem zelf had gepubliceerd.
    eigen = eigen_hosts() if eigen is None else eigen
    if host and any(host == e or host.endswith("." + e) for e in eigen):
        return _reden(uit, "eigen-site", f"{host} is onze eigen site")

    # 2. Publiceert deze pagina iets? Dan is de rest niet meer interessant.
    #    Staat vóór de filters hieronder: '/blog/onze-nieuwe-diensten' is een
    #    artikel over diensten, geen dienstpagina.
    if (mappen | kruimels) & _PUBLICATIE_MARKERS or _lijkt_datumpad(segmenten):
        return uit

    # 3. Vacature — eigen klasse omdat het de grootste categorie is.
    vac = (mappen | kruimels) & _VACATURE_MARKERS
    if vac:
        return _reden(uit, "vacature", f"pad bevat '{sorted(vac)[0]}'")
    if any(host.startswith(p) for p in _VACATURE_HOSTS):
        return _reden(uit, "vacature", f"vacaturesubdomein {host}")
    m = _VACATURE_TITEL.search(titel)
    if m:
        return _reden(uit, "vacature", f"titel bevat '{m.group(0)}'")

    # 4. Aanbod of navigatie.
    aanbod = (mappen | kruimels) & _AANBOD_MARKERS
    if aanbod:
        return _reden(uit, "aanbodpagina", f"pad bevat '{sorted(aanbod)[0]}'")

    # 5. Een kaal pad is een homepage of een landingspagina — nooit een
    #    publicatie. 'mintjesenco.nl/lego-serious-play/' is een dienst, geen
    #    artikel over die dienst.
    if len(segmenten) <= 1:
        waar = "de homepage" if not segmenten else f"landingspagina /{segmenten[0]}/"
        return _reden(uit, "navigatie", f"{waar} — geen artikel maar een ingang")

    return uit


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


_DATUMPAD = re.compile(r"^(19|20)\d{2}$")


def _lijkt_datumpad(segmenten: List[str]) -> bool:
    """`/2026/08/hoe-wij-…` — een datum in het pad betekent per definitie een
    gedateerde publicatie. Veel blogs hebben geen /blog/-segment maar wel dit."""
    return any(_DATUMPAD.match(s) for s in segmenten)


def _reden(uit: Dict, reason: str, detail: str) -> Dict:
    uit.update({"filter_reason": reason,
                "filter_label": REASON_LABELS[reason],
                "filter_detail": detail[:180]})
    return uit


def partition(signalen: List[Dict], *, eigen: Optional[Set[str]] = None
              ) -> Tuple[List[Dict], List[Dict]]:
    """Splits ruwe scanresultaten in (echte signalen, geweerde signalen).

    De geweerde krijgen hun oordeel meegeplakt; de aanroeper slaat ze op met
    `status='uitgefilterd'` zodat de beslissing controleerbaar blijft.
    """
    eigen = eigen_hosts() if eigen is None else eigen
    door: List[Dict] = []
    weg: List[Dict] = []
    for s in signalen:
        oordeel = assess(s, eigen=eigen)
        s.update(oordeel)
        (weg if oordeel["filter_reason"] else door).append(s)
    return door, weg
