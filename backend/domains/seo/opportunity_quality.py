"""
Kwaliteitsgate voor de Kansen-lijst — "alleen kansen die écht een kans zijn".

Aanleiding (2 aug 2026): het Kansen-paneel van WeAreImpact toonde 11 "nieuwe"
kansen. Bij nalopen was er precies één echt nieuw. Twee waren al gedaan
('consultant sociaal domein' lag sinds 1 aug in de Wachtrij; 'programma manager
digitale transformatie' stond live als 'programmamanager digitale transformatie'
— één spatie verschil), vier kannibaliseerden bestaande blogs, en vier waren
ruis: een concurrent-domein (nictiz.nl), een Duitstalige query (ai strategie
beratung) en varianten van al weggeklikte kansen. "Schrijf alle 11" had dus tien
artikelen geproduceerd die de site actief schaden.

Waarom de bestaande dedupe dat niet ving:

  1. `list_opportunities_truth` corrigeerde een kans alléén omhoog naar
     'published' als er een artikel LIVE staat. "Er ligt al een concept in de
     Wachtrij" bestond niet als uitkomst.
  2. `reconcile_opportunities` kijkt uitsluitend naar kansen die al op
     'in_progress' staan — een kans op 'new' met een lopend artikel wordt nooit
     gecontroleerd.
  3. `_has_open_job` matcht op exacte/substring-gelijke `content_jobs.keyword`.
     "programma manager" bevat "programmamanager" niet, en de concepten die uit
     de goal-engine komen hebben een léég keyword-veld — die matchen per
     definitie nergens op.
  4. Er was helemaal geen topische check, alleen string-vergelijking. Vandaar de
     kannibalen.

Deze module is de enige plek waar dat oordeel valt, zodat het Kansen-paneel,
het project-advies én `select_topic` (de autonome contentmotor) exact dezelfde
lijst zien. Deterministisch, zonder LLM: een gate die zelf een gateway nodig
heeft valt stil precies wanneer de gateway plat ligt.

Uitbreiding 3 aug 2026 — de gate kende alleen tékst en liet daardoor nog
steeds bijna alles door. Bewaard voor Jou kreeg acht "nieuwe" kansen terwijl er
102 pagina's live stonden en zeven daarvan al op 'levensverhaal vastleggen'
vertoonden; de gate zei van alle acht `filter_reason: None`. Drie gaten, elk
met een eigen regel hieronder:

  5. De bronnen waren allebei administratie — `content_jobs` (wat wíj deden) en
     de sitemap (die voor de meeste sites lege titels levert, dus alleen
     slugs). `_gsc_coverage` voegt de waarneming toe: een pagina met
     vertoningen bestáát, en `top_query` zegt waar hij al voor meedoet. Dat
     gaat vóór elke tekstvergelijking en heet `rankt-al`.
  6. De vergelijking liep op exacte tokens, in een taal die vervoegt en
     samenplakt: 'ouders' dekte 'ouder' niet. Zie `_same_word`.
  7. `_CANNIBAL_OVERLAP` beloofde in zijn docstring "alle woorden op één na" en
     eiste in de code 0,99 — geen enkel woord onbedekt. Zie `_kannibaliseert`.

Elke uitgefilterde kans houdt zijn `filter_reason` + `filter_detail` (het
bewijs: wélk artikel, wélk woord). Niets verdwijnt stil — het gaat naar de bak
"Uitgefilterd", want een filter dat je niet kunt controleren is niet te
vertrouwen.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from ...shared.database import get_conn

logger = logging.getLogger(__name__)

# ── Uitkomsten ──────────────────────────────────────────────────────────────
# Volgorde = prioriteit bij het beoordelen: "staat al live" is een hardere
# verklaring dan "lijkt op iets anders".
REASON_LABELS = {
    "rankt-al": "Er rankt al een pagina op dit zoekwoord",
    "al-live": "Staat al live",
    "in-wachtrij": "Ligt al in de Wachtrij",
    "kannibaal": "Kannibaliseert bestaande content",
    "navigatie": "Navigatiezoekopdracht",
    "vreemde-taal": "Andere taal",
    "te-vaag": "Te vaag",
    "geen-zoekwoord": "Lijkt op een titel, geen zoekopdracht",
}

# Open werk: een artikel in een van deze statussen is "in behandeling".
_OPEN_JOB_STATUSES = ("pending_review", "needs_work", "approved", "publish_failed")

# ── Normaliseren ────────────────────────────────────────────────────────────

# Nederlandse functiewoorden. Bewust kort: 'beste', 'kosten', 'nederland' en
# 'hoe' dragen wél zoekintentie en horen NIET weggegooid te worden — dat zijn
# precies de woorden die twee kansen van elkaar onderscheiden.
_STOPWORDS = {
    "de", "het", "een", "en", "of", "van", "voor", "in", "op", "met", "bij",
    "aan", "te", "is", "zijn", "naar", "dat", "die", "der", "des", "om", "als",
    "je", "jouw", "ik", "we", "wij", "u", "uw", "er", "ook", "niet", "maar",
    "the", "a", "of", "for", "to", "and",
    # Vraagwoorden en hulpwerkwoorden. Ze dragen géén onderwerp maar tellen wél
    # mee in de overlap-breuk, en dat verwatert precies de longtail-kansen waar
    # het om gaat: 'hoe schrijf je een levensverhaal op' hield 3 tokens over
    # ('hoe', 'schrijf', 'levensverhaal') tegen 2 van de live pagina
    # 'levensverhaal opschrijven' — 33% overlap in plaats van 50%, en dus geen
    # duplicaat (4 aug 2026, Bewaard voor Jou: 8 van 8 kansen glipten erdoor).
    # 'wat', 'hoe' en 'waarom' zijn de drie vormen waarin een zoeker hetzelfde
    # onderwerp anders inleidt; ze mogen een pagina nooit tot nieuw onderwerp
    # promoveren.
    "hoe", "wat", "waarom", "wanneer", "welke", "welk", "waar", "wie",
    "hoeveel", "kun", "kunt", "kan", "moet", "zo", "dit", "deze", "mijn",
    "how", "what", "why",
}

# Scheidbare voorvoegsels: het Nederlands maakt hiermee uit één werkwoord tien
# vormen die allemaal hetzelfde doen. 'schrijven' / 'opschrijven',
# 'leggen' / 'vastleggen', 'nemen' / 'meenemen'. De stam-ratio van
# `_same_word` (70% van het langste woord) pakt die bewust niet: 'schrijv' is
# 64% van 'opschrijven', en de ratio ligt op 70 omdat 'levensboek' /
# 'levensverhaal' (46%) géén match mag zijn. Beide beslissingen kloppen; ze
# hebben alleen elk hun eigen mechanisme nodig.
#
# Bewust alléén voorvoegsels, nooit een algemene deel-string-test: 'verhaal' zit
# óók in 'levensverhaal', maar 'levens' is geen voorvoegsel uit deze lijst en
# een levensverhaal is echt iets anders dan een verhaal.
_SCHEIDBARE_VOORVOEGSELS = (
    "op", "aan", "af", "uit", "in", "mee", "over", "door", "bij", "vast",
    "samen", "terug", "weg", "toe", "voort", "neer", "los", "her",
)

# Woorden die een query onmiskenbaar in een andere taal zetten. Bewust een
# korte, expliciete lijst in plaats van taaldetectie: een statistische detector
# zit er op drie woorden regelmatig naast, en dan gooi je een goede kans weg.
# Uitbreiden mag — elke hit toont in de UI welk woord hem pakte.
_FOREIGN_MARKERS = {
    # Duits
    "beratung", "unternehmen", "agentur", "dienstleistungen", "mitarbeiter",
    "fuhrung", "fur", "und", "mit", "kunden", "erfahrungen", "kostenlos",
    "beispiele", "einfuhrung", "schulung",
    # Frans
    "conseil", "entreprise", "pour", "avec", "gestion", "formation",
    # Engels (alleen woorden die in het Nederlands niet voorkomen)
    "company", "services", "consulting", "agency", "near", "best", "what",
    "which", "how", "guide", "software", "solutions",
}

# Topniveau-domeinen: een query die hierop eindigt is iemand die naar een
# ándere website navigeert, geen contentvraag.
_TLD_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(nl|com|org|net|be|de|eu|io|co|info|app)\b")

# Een gekopieerde titel, geen getypte zoekopdracht. Aanleiding (9 aug 2026):
# de trend-brug zet de titel van een gevonden artikel in de queryregel
# (`seo/trends.py:_signal_query`) — op WeAreImpact waren 16 van de 23 "kansen"
# zo een letterlijke Oracle-blogtitel, een afgekapte Reddit-post
# ("...worskhop method in"), een cursustitel met "(1 jaar)" erachter, of een
# Engelse LinkedIn-kop met ®-tekens. Geen van de bestaande regels checkt op
# vórm — alleen op taal (woordenlijst) en op te-kort — dus élke van de 16 kreeg
# `filter_reason: None` en stond gewoon tussen de echte kansen.
# Leestekens die een zoeker nooit intypt: dubbele punt, uitroepteken, pipe,
# ampersand, handelsmerktekens, een liggend streepje tussen spaties (bronstaart
# à la "... - iBestuur"), of een cijfer direct na een open haakje ("(1 jaar)").
_TITEL_LEESTEKENS = re.compile(r"[:!|&®©]|\s[-–—]\s|\(\d")
# De cold-start-prompt vraagt zelf al "3-6 woorden, zoals mensen echt zoeken";
# een kop van een gevonden artikel gaat daar ver overheen.
_MAX_QUERY_WOORDEN = 8
# Een titel die is afgekapt op tekenlimiet eindigt op een voorzetsel/lidwoord
# in plaats van het onderwerp — "...kan AI verlichting" mist "bieden". Alleen
# bij ≥5 woorden, anders vangt dit legitieme korte vraagzinnen ("hoe kom ik in").
_EINDIGT_ONAF = {
    "in", "op", "van", "voor", "aan", "met", "bij", "naar", "en", "of", "de",
    "het", "een", "te", "om",
}


def _lijkt_op_titel(query: str) -> Optional[str]:
    """Onderscheid een getypte zoekopdracht van een gekopieerde titel.

    Bewust vormgericht (leestekens, lengte, afkapping) en niet taalgericht —
    dat laatste doet `_FOREIGN_MARKERS` al, en een titel kan prima foutloos
    Nederlands zijn (de cursustitel, de Wmo-naam) en toch geen zoekwoord."""
    raw = (query or "").strip()
    if not raw:
        return None
    hit = _TITEL_LEESTEKENS.search(raw)
    if hit:
        return f"bevat '{hit.group(0).strip()}' — leesteken dat niemand intypt"
    woorden = raw.split()
    if len(woorden) > _MAX_QUERY_WOORDEN:
        return f"{len(woorden)} woorden — een zoekopdracht is kort, dit is een kop"
    if len(woorden) >= 5 and normalize(woorden[-1]) in _EINDIGT_ONAF:
        return f"eindigt op '{woorden[-1]}' — lijkt afgekapt"
    return None


def normalize(text: str) -> str:
    """Kleinletter, accentloos, leestekenloos, enkelvoudige spaties.

    Accenten ontleden is niet cosmetisch: GSC levert 'ideeen' waar de site
    'ideeën' schrijft, en zonder deze stap zijn dat twee verschillende
    zoekwoorden (zie de kwaliteitsgate-vastlopers van 25 jul 2026).
    """
    decomposed = unicodedata.normalize("NFKD", (text or "").strip().lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def squash(text: str) -> str:
    """Genormaliseerd én zónder spaties.

    Dit is de enige manier om 'programma manager digitale transformatie' en
    'programmamanager digitale transformatie' als hetzelfde te herkennen.
    Nederlands schrijft samenstellingen aaneen, zoekers doen dat lang niet
    altijd, en GSC bewaart beide vormen als losse queries.
    """
    return normalize(text).replace(" ", "")


def tokens(text: str) -> set:
    """Inhoudswoorden: genormaliseerd, zonder functiewoorden en zonder losse
    letters/cijfers (die dragen geen onderwerp)."""
    return {t for t in normalize(text).split()
            if t not in _STOPWORDS and len(t) > 1}


# ── Woordgelijkheid ─────────────────────────────────────────────────────────
# Exacte tokenvergelijking is te bot voor het Nederlands, en dat kostte op
# 3 aug 2026 zeven van de acht kansen van Bewaard voor Jou: de kans
# 'voetbalontwikkeling kind zien ouders' matchte niet op het live artikel
# 'ouder inzicht in voetbalontwikkeling kind zonder druk' omdat 'ouders' en
# 'ouder' verschillende strings zijn. Nederlands vervoegt en plakt samen; een
# gate die daar blind voor is, is in het Nederlands geen gate.
#
# Bewust géén stemmer-bibliotheek en géén afkapping op N tekens: 'levensboek'
# en 'levensverhaal' delen zes beginletters en zijn écht verschillende
# onderwerpen die allebei een eigen pagina verdienen. De twee regels hieronder
# zijn zo gekozen dat dát paar juist NIET matcht.
_MIN_STAM = 5        # korter dan dit is een lettergreep, geen woord
_STAM_RATIO = 0.7    # gedeeld begin moet het lángste woord grotendeels dekken


@lru_cache(maxsize=100_000)
def _same_word(a: str, b: str) -> bool:
    """Zijn dit twee vormen van hetzelfde woord?

    Puur en deterministisch (geen DB/tijd/state) — vandaar de cache. Bij een
    site met veel coverage (gepubliceerde pagina's + GSC-queries) vergelijkt
    `_match()` elke kans woord-voor-woord tegen élk coverage-record; dezelfde
    Nederlandse woordparen ('sociaal'/'sociale', 'domein'/'domeinen') komen
    dan duizenden keren voorbij. Gemeten (9 aug 2026): 733k aanroepen voor één
    site van 38 kansen × 276 coverage-records, goed voor 13 van de 15 seconden
    die `list_opportunities_truth` toen kostte — puur herhaald rekenwerk over
    identieke invoer, geen enkele kans op een vervuilde cache tussen aanroepen
    door want de functie kijkt nergens anders naar dan zijn twee argumenten.

    Twee gevallen, allebei uit echte data:
      * voorvoegsel — 'ouder' ⊂ 'ouders', 'voetbal' ⊂ 'voetbalskills';
      * gedeelde stam — 'individueel' / 'individuele' delen 9 van de 11 letters.

    En één geval dat expliciet géén match mag zijn: 'levensverhaal' /
    'levensboek' delen zes letters, maar dat is 46% van het langste woord —
    onder de ratio, dus twee onderwerpen.
    """
    if a == b:
        return True
    kort, lang = (a, b) if len(a) <= len(b) else (b, a)
    if len(kort) < _MIN_STAM:
        return False
    if _begint_met(lang, kort):
        return True
    gedeeld = _gedeeld_begin(a, b)
    if gedeeld >= _MIN_STAM and gedeeld >= _STAM_RATIO * len(lang):
        return True
    return _zelfde_werkwoord_met_voorvoegsel(kort, lang)


# Nederlandse medeklinkerwisseling bij verbuiging: schrijf/schrijven,
# brief/brieven, huis/huizen, leef/leven. Zonder deze gelijkstelling loopt de
# stamvergelijking precies één letter mis — 'schrijf'/'schrijven' deelt dan
# 'schrij' (6 letters, 66% van het langste woord) en valt nét onder de ratio
# van 70%. Dat is geen randgeval maar de standaardvervoeging van elk
# Nederlands werkwoord op -ven en -zen, en het kostte op 4 aug 2026 de match
# tussen de kans 'hoe schrijf je een levensverhaal op' en de live pagina
# '/levensverhaal-opschrijven' (62 vertoningen, positie 26,9).
_WISSELPAREN = {"f": "v", "v": "v", "s": "z", "z": "z"}


def _zelfde_letter(x: str, y: str) -> bool:
    return x == y or _WISSELPAREN.get(x, x) == _WISSELPAREN.get(y, y)


def _gedeeld_begin(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if not _zelfde_letter(x, y):
            break
        n += 1
    return n


def _begint_met(lang: str, kort: str) -> bool:
    return len(kort) <= len(lang) and _gedeeld_begin(lang, kort) == len(kort)


def _zelfde_werkwoord_met_voorvoegsel(kort: str, lang: str) -> bool:
    """'schrijf' en 'opschrijven' zijn hetzelfde werkwoord.

    Strip een scheidbaar voorvoegsel van het langste woord en toets de rest
    opnieuw op stam-gelijkheid. Dat is precies de vorm die de gate op 4 aug 2026
    liet lopen: de kans 'hoe schrijf je een levensverhaal op' tegen de live
    pagina '/levensverhaal-opschrijven', met 62 vertoningen op positie 26,9.

    De ondergrens op de reststam houdt de ergste onzin buiten de deur ('in' +
    'ga'), maar dekt niet alles: 'inzicht' en 'zicht' matchen hierdoor wél,
    terwijl dat twee woorden zijn. Dat is een bewust geaccepteerde
    vals-positief, geen oversight — de fout valt naar 'te streng', en te streng
    is hier herstelbaar: de kans belandt in de bak 'Uitgefilterd' mét het bewijs
    en een knop 'Toch oppakken'. Een gemiste duplicaat kost een artikel dat een
    bestaande pagina kannibaliseert, en dát is niet terug te draaien.
    """
    for pre in _SCHEIDBARE_VOORVOEGSELS:
        if not lang.startswith(pre):
            continue
        rest = lang[len(pre):]
        if len(rest) < _MIN_STAM:
            continue
        if _begint_met(rest, kort) or _begint_met(kort, rest):
            return True
        gedeeld = _gedeeld_begin(rest, kort)
        if gedeeld >= _MIN_STAM and gedeeld >= _STAM_RATIO * max(len(rest), len(kort)):
            return True
    return False


def _covered(a: set, b: set) -> set:
    """De tokens uit `a` waarvoor `b` een woordvorm bevat."""
    return {t for t in a if any(_same_word(t, r) for r in b)}


def _overlap(a: set, b: set) -> Tuple[float, float]:
    """Aandeel van a dat in b zit, en omgekeerd — woordvorm-tolerant."""
    if not a or not b:
        return 0.0, 0.0
    return len(_covered(a, b)) / len(a), len(_covered(b, a)) / len(b)


# ── Wat er al bestaat ───────────────────────────────────────────────────────

_coverage_cache: Dict[str, Tuple[float, List[Dict]]] = {}
_COVERAGE_TTL_SECONDS = 300


def _record(kind: str, label: str, *parts: str, url: str = "") -> Optional[Dict]:
    """Eén stuk bestaande content, klaar om tegenaan te vergelijken.

    `parts` zijn alle teksten die dit artikel identificeren (zoekwoord, titel,
    slug). Ze gaan sámen de tokenverzameling in: een job uit de goal-engine
    heeft een leeg keyword-veld en is dan alleen via zijn titel te herkennen —
    precies het gat waardoor het concept 'Digitale transformatie in het sociaal
    domein' de gelijknamige kans niet afdekte.
    """
    text = " ".join(p for p in parts if p)
    tok = tokens(text)
    if not tok:
        return None
    return {
        "kind": kind,
        "label": (label or text).strip(),
        "url": url,
        "tokens": tok,
        "squashes": {squash(p) for p in parts if p and squash(p)},
    }


def _jobs_coverage(site_id: str) -> List[Dict]:
    rows = []
    with get_conn() as conn:
        placeholders = ",".join("?" * len(_OPEN_JOB_STATUSES))
        for r in conn.execute(
            "SELECT id, title, keyword, slug, status, publish_result "
            "FROM content_jobs WHERE site_id = ? AND status = 'published' "
            "ORDER BY created_at DESC", (site_id,),
        ):
            rec = _record("al-live", r["title"] or r["keyword"] or "",
                          r["keyword"] or "", r["title"] or "", r["slug"] or "",
                          url=_url_from_publish_result(r["publish_result"]))
            if rec:
                rec["content_job_id"] = r["id"]
                rows.append(rec)
        for r in conn.execute(
            f"SELECT id, title, keyword, slug, status FROM content_jobs "
            f"WHERE site_id = ? AND status IN ({placeholders}) "
            f"ORDER BY created_at DESC", (site_id, *_OPEN_JOB_STATUSES),
        ):
            rec = _record("in-wachtrij", r["title"] or r["keyword"] or "",
                          r["keyword"] or "", r["title"] or "", r["slug"] or "")
            if rec:
                rec["content_job_id"] = r["id"]
                rec["job_status"] = r["status"]
                rows.append(rec)
    return rows


def _url_from_publish_result(raw) -> str:
    if not raw:
        return ""
    try:
        import json
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return (parsed.get("site") or {}).get("url") or parsed.get("url") or ""
    except Exception:
        return ""


def _external_coverage(site: Dict) -> List[Dict]:
    """Wat er buiten Agent OS om al op de site staat (extern CMS + live
    sitemap). Dit is de énige bron die de échte blogs kent — `published_pages`
    is voor de meeste sites leeg by design. Faalt stil: een onbereikbare
    sitemap mag het Kansen-paneel nooit platleggen."""
    try:
        from .external_content import fetch_all_known_content
        items = fetch_all_known_content(site)
    except Exception as e:  # noqa: BLE001
        logger.debug("[kansen] Externe content niet opgehaald voor %s: %s",
                     site.get("name"), str(e)[:150])
        return []
    out = []
    for item in items:
        title, slug = item.get("title") or "", item.get("slug") or ""
        rec = _record("al-live", title or slug.replace("-", " "), title, slug)
        if rec:
            base = (site.get("base_url") or "").rstrip("/")
            rec["url"] = f"{base}/{slug}" if base and slug else ""
            out.append(rec)
    return out


def _gsc_coverage(site_id: str) -> List[Dict]:
    """Wat Google zegt dat deze site heeft — de buitenwereld, niet onze
    administratie.

    Dit is de les van `afgewezen_maar_live` toegepast op de andere kant: de
    twee bronnen hierboven zijn beweringen van het systeem over zichzelf
    (`content_jobs`) of over zijn eigen sitemap. `gsc_history` is een
    waarneming: een pagina die vertoningen krijgt bestáát, staat in de index,
    en het zoekwoord waarop hij vertoont is het hardste bewijs dat er is van
    waar die pagina al voor meedoet.

    Waarom dat nodig was (3 aug 2026): Bewaard voor Jou kreeg acht "nieuwe"
    kansen aangeboden terwijl er 102 pagina's live stonden en zeven daarvan
    al vertoonden op 'levensverhaal vastleggen'. De gate kende alleen slugs
    uit de sitemap — losse woorden in een URL — en zag daardoor niet dat de
    site op precies die zoekwoorden al meedeed.

    Per pagina de laatste snapshot; `top_query` gaat als eigen veld mee zodat
    `_match` het verschil kan maken tussen "lijkt op" (tekstvergelijking) en
    "rankt al op" (waarneming).
    """
    rijen: List[Dict] = []
    try:
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT h.page_url, h.top_query, h.impressions, h.position "
                "FROM gsc_history h JOIN ("
                "  SELECT page_url, MAX(date) AS d FROM gsc_history"
                "  WHERE site_id = ? AND scope = 'page' GROUP BY page_url"
                ") l ON l.page_url = h.page_url AND l.d = h.date "
                "WHERE h.site_id = ? AND h.scope = 'page'",
                (site_id, site_id),
            ):
                url = (r["page_url"] or "").strip()
                if not url:
                    continue
                slug = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
                top_query = (r["top_query"] or "").strip()
                rec = _record("al-live", top_query or slug.replace("-", " "),
                              top_query, slug, url=url)
                if not rec:
                    continue
                if top_query:
                    rec["ranks_for"] = top_query
                    try:
                        rec["ranks_at"] = float(r["position"] or 0)
                    except (TypeError, ValueError):
                        rec["ranks_at"] = 0.0
                rijen.append(rec)
    except Exception as e:  # noqa: BLE001
        # Geen tabel (verse installatie) of een kapotte query mag het
        # Kansen-paneel nooit platleggen — dan valt de gate terug op de twee
        # tekstbronnen, precies zoals hij het hiervóór deed.
        logger.debug("[kansen] GSC-dekking niet beschikbaar voor %s: %s",
                     site_id, str(e)[:150])
    return rijen


def site_coverage(site: Dict, use_cache: bool = True) -> List[Dict]:
    """Alles wat voor deze site al bestaat, in één vergelijkbare vorm."""
    sid = site.get("id") or ""
    now = time.time()
    if use_cache:
        cached = _coverage_cache.get(sid)
        if cached and cached[0] > now:
            return cached[1]
    records = _jobs_coverage(sid) + _external_coverage(site) + _gsc_coverage(sid)
    _coverage_cache[sid] = (now + _COVERAGE_TTL_SECONDS, records)
    return records


def invalidate(site_id: str = "") -> None:
    """Cache legen — aanroepen zodra er een job bijkomt of live gaat."""
    if site_id:
        _coverage_cache.pop(site_id, None)
    else:
        _coverage_cache.clear()


# ── Het oordeel ─────────────────────────────────────────────────────────────

# Zelfde drempel als `content_pipeline._topic_already_covered`: pas "hetzelfde
# onderwerp" bij wederzijds ≥80% token-overlap. Bewust streng — 'kosten' of
# 'ervaringen' erbij is een échte andere zoekintentie en verdient een eigen
# pagina.
_DUPLICATE_OVERLAP = 0.8

# Kannibalisatie is soepeler: als álle inhoudswoorden van de kans op één na al
# door een bestaand artikel gedekt worden, gaan die twee pagina's om dezelfde
# zoekopdracht vechten. Eenzijdig, want een lang artikel mag best méér
# onderwerpen raken dan de kans.
#
# Die zin stond hier al sinds 2 aug 2026 — maar de code eronder eiste 0,99
# overlap, oftewel: géén enkel woord onbedekt. "Op één na" was dus nooit
# geïmplementeerd, en daardoor glipte op 3 aug 2026 'outdoor teambuilding
# schiphol omgeving' langs een live artikel met de titel 'Outdoor teambuilding
# Schiphol regio' (drie van de vier woorden gedekt). Nu telt de regel wat hij
# beweert te tellen: hoogstal één onbedekt woord.
#
# De drempel is een telling, geen percentage, want een percentage betekent bij
# 3 woorden iets anders dan bij 6. Bij twee woorden telt de uitzondering niet:
# 'organisatiebijdrage meten' zou dan matchen op 'impact meten', en dat is
# gewoon een ander onderwerp met één toevallig gedeeld werkwoord.
_MAX_ONBEDEKTE_WOORDEN = 1
_MIN_WOORDEN_VOOR_UITZONDERING = 3

# ...maar niet élk onbedekt woord is een detail. Sommige woorden veranderen
# wát de zoeker wil, niet alleen hoe hij het opschrijft: wie 'levensverhaal
# laten schrijven kosten' zoekt wil een prijs, niet nóg een uitleg over
# levensverhalen. Zo'n zoeker verdient een eigen pagina, en dat besluit stond
# al in dit systeem (de duplicaat-drempel is er expliciet streng voor gemaakt).
#
# Zonder deze lijst sloopt de "hoogstens één onbedekt woord"-regel dat besluit:
# hij zag 'kosten' als het ene woord dat je mag missen. Het onderscheid dat
# hier wordt vastgelegd is dus niet cosmetisch — het is het verschil tussen
# 'schiphol omgeving' / 'schiphol regio' (zelfde vraag, ander woord) en
# 'levensverhaal schrijven' / 'levensverhaal schrijven kosten' (andere vraag).
_INTENTIE_MODIFIERS = {
    # prijs en commercie
    "kosten", "kost", "prijs", "prijzen", "tarief", "tarieven", "gratis",
    "goedkoop", "goedkoopste",
    # afweging en vergelijking
    "ervaringen", "ervaring", "review", "reviews", "vergelijken", "vergelijking",
    "alternatief", "alternatieven", "nadelen", "voordelen", "verschil",
    # vorm: iemand wil een ding, geen uitleg
    "voorbeeld", "voorbeelden", "checklist", "sjabloon", "template", "zelf",
}


def is_same_topic(query: str, *parts: str) -> bool:
    """Gaat `query` over hetzelfde onderwerp als de content beschreven door
    `parts` (zoekwoord, titel, slug)? Bewust dezelfde regels als de
    duplicaat-tak van `_match`, zodat "is dit al gedaan?" overal één antwoord
    heeft."""
    rec = _record("al-live", "", *parts)
    if rec is None:
        return False
    q_tokens, q_squash = tokens(query), squash(query)
    if not q_tokens:
        return False
    if q_squash and q_squash in rec["squashes"]:
        return True
    fwd, rev = _overlap(q_tokens, rec["tokens"])
    return fwd >= _DUPLICATE_OVERLAP and rev >= _DUPLICATE_OVERLAP


def _kannibaliseert(q_tokens: set, rec_tokens: set) -> bool:
    """Vecht een kans met `q_tokens` om dezelfde zoekopdracht als deze content?

    Twee trappen, want "niets onbedekt" en "één woord onbedekt" zijn niet
    even hard bewijs:

      * élk woord gedekt → altijd kannibalisatie, ook bij twee woorden
        ('kat adopteren' onder 'kat adopteren uit het asiel');
      * één woord onbedekt → alleen bij ≥3 woorden, en alleen als dat woord
        de zoekintentie niet verlegt.
    """
    onbedekt = q_tokens - _covered(q_tokens, rec_tokens)
    if not onbedekt:
        return len(q_tokens) >= 2
    if len(onbedekt) > _MAX_ONBEDEKTE_WOORDEN:
        return False
    if len(q_tokens) < _MIN_WOORDEN_VOOR_UITZONDERING:
        return False
    return not (onbedekt & _INTENTIE_MODIFIERS)


def _match(query: str, coverage: Iterable[Dict]) -> Optional[Tuple[str, Dict]]:
    q_tokens = tokens(query)
    q_squash = squash(query)
    if not q_tokens:
        return None
    cannibal: Optional[Dict] = None
    for rec in coverage:
        # De zoekmachine zegt dat er al een pagina op dit zoekwoord vertoont.
        # Dat is geen gelijkenis maar een waarneming, dus het gaat vóór elke
        # tekstvergelijking hieronder — inclusief de squash-kortsluiting, die
        # anders juist bij een exacte treffer 'al-live' zou zeggen waar
        # 'rankt-al' de bruikbare diagnose is (optimaliseer díé pagina).
        if rec.get("ranks_for") and is_same_topic(query, rec["ranks_for"]):
            return "rankt-al", rec
        if q_squash and q_squash in rec["squashes"]:
            return rec["kind"], rec  # zelfde woorden, andere spatiëring
        fwd, rev = _overlap(q_tokens, rec["tokens"])
        if fwd >= _DUPLICATE_OVERLAP and rev >= _DUPLICATE_OVERLAP:
            return rec["kind"], rec
        # Al gedekt, maar het artikel gaat over méér: kannibalisatie. Onthouden
        # en doorzoeken — een harde duplicaat-hit verderop is een betere
        # verklaring dan deze.
        if cannibal is None and _kannibaliseert(q_tokens, rec["tokens"]):
            cannibal = rec
    if cannibal is not None:
        return "kannibaal", cannibal
    return None


def _junk_reason(query: str, site: Dict) -> Optional[Tuple[str, str]]:
    """Ruis die nooit een artikel verdient. Geeft (reden, bewijs)."""
    norm = normalize(query)
    raw = (query or "").strip().lower()

    if _TLD_RE.search(raw) or "/" in raw or raw.startswith("www."):
        # Uitzondering: het eigen domein is een merkzoekopdracht, en dáár hoor
        # je juist op nummer 1 te staan.
        own = squash((site.get("base_url") or "").replace("https://", "")
                     .replace("http://", "").split("/")[0])
        if own and own in squash(raw):
            return None
        hit = _TLD_RE.search(raw)
        return "navigatie", (hit.group(0) if hit else raw) + " — zoeker wil een andere website, geen artikel"

    titel_reden = _lijkt_op_titel(query)
    if titel_reden:
        return "geen-zoekwoord", titel_reden

    content_tokens = tokens(query)
    foreign = content_tokens & _FOREIGN_MARKERS
    if foreign:
        return "vreemde-taal", "bevat '" + "', '".join(sorted(foreign)) + "'"

    if len(content_tokens) < 2 or len(norm) < 8:
        return "te-vaag", "te weinig zoekintentie om een artikel op te bouwen"
    return None


def assess(opp: Dict, coverage: List[Dict], site: Dict) -> Dict:
    """Beoordeel één kans. Geeft de velden terug die eraan geplakt worden.

    `filter_reason` leeg = wereldklasse: nieuw, uitvoerbaar, niet-dubbel.
    """
    query = opp.get("query") or ""
    out = {
        "filter_reason": None, "filter_label": None, "filter_detail": None,
        "filter_url": None, "filter_job_id": None, "filter_source": None,
    }
    hit = _match(query, coverage)
    if hit:
        kind, rec = hit
        detail = rec["label"][:140]
        if kind == "rankt-al":
            # Het bewijs mét het getal erbij: "positie 27" vertelt meteen of de
            # juiste zet optimaliseren is of dat de pagina hopeloos ver weg
            # staat. Zonder dat getal is dit filter een dichte deur.
            pos = rec.get("ranks_at") or 0.0
            waar = rec.get("url") or rec["label"]
            detail = (f"{waar} vertoont al op '{rec['ranks_for'][:60]}'"
                      + (f" — positie {pos:.1f}".replace(".", ",") if pos else "")
                      + " — optimaliseer die pagina in plaats van een tweede te schrijven")
        out.update({
            "filter_reason": kind,
            "filter_label": REASON_LABELS[kind],
            "filter_detail": detail[:220],
            "filter_url": rec.get("url") or None,
            "filter_job_id": rec.get("content_job_id"),
            "filter_source": "gsc" if kind == "rankt-al" else "tekst",
        })
        return out
    junk = _junk_reason(query, site)
    if junk:
        reason, detail = junk
        out.update({"filter_reason": reason,
                    "filter_label": REASON_LABELS[reason],
                    "filter_detail": detail,
                    "filter_source": "regel"})
    return out


# ── Vraag-herkomst en volgorde ──────────────────────────────────────────────
# Niet elke kans komt uit gemeten vraag. Cold-start en trendsignalen zetten
# impressies/positie op 0; die verschenen tot nu toe onder de kop "Striking
# distance kansen" alsof ze uit GSC kwamen. Dat is geen filter maar wel een
# eerlijkheidskwestie: een kans mét 400 impressies op positie 7 is een andere
# belofte dan een bedacht zoekwoord.
#
# Het oordeel daarover (en de sortering die eruit volgt) staat in `potential`,
# niet hier: deze module beslist óf een kans mag meedoen, `potential` beslist
# in welke volgorde. Twee modules die allebei "wat is deze kans waard?"
# beantwoorden is precies hoe het Kansen-paneel en de contentmotor uit elkaar
# gaan lopen.


def demand_kind(opp: Dict) -> str:
    from . import potential
    return "gemeten" if potential.is_measured(opp) else "speculatief"


def annotate(opportunities: List[Dict], site: Dict) -> List[Dict]:
    """Plak op elke kans het oordeel + de verwachte opbrengst, en sorteer zo
    dat gemeten vraag boven giswerk staat."""
    from . import potential
    coverage = site_coverage(site)
    for opp in opportunities:
        opp.update(assess(opp, coverage, site))
    return potential.annotate(opportunities)
