"""De waarheidsaudit — Iris zoekt wat stíl kapot is.

`selfheal.py` is de tegenhanger van dit bestand: die ruimt op wat **luid**
faalt (een exception, een non-200, een rode kaart). Dit bestand zoekt het
tegenovergestelde en veel gevaarlijkere geval: een systeem dat succes meldt
terwijl het effect nooit heeft plaatsgevonden.

Aanleiding is niet één incident maar een patroon. Tien fouten uit één ronde
(1-2 aug 2026), allemaal dezelfde soort:

  - `json_ld: ok` op FAQ-markup die Google niet als FAQPage leest
  - 62 kansen op 'in_progress' die niemand meer oppakte, terwijl de tabel vol stond
  - 51 lessen met 2 voorspellings-koppelingen: de leerlus was gebouwd, maar draaide leeg
  - 'gepubliceerd' op een slug met een '&' erin — een harde 404
  - twee artikelen live op hetzelfde zoekwoord, beide "geslaagd"
  - een paginatitel als organisatienaam, waardoor de funnel-conversie niets mat

Geen van deze tien wierp ooit een exception. Ze zijn alle tien gevonden doordat
een mens toevallig ging kijken. CLAUDE.md zegt al *"activiteit is geen effect"*,
en tóch leert de codebase die regel elke maand op een nieuwe plek opnieuw —
precies omdat er niets is dat er structureel op tóetst.

Dat is wat hier staat. Een **invariant** is een uitspraak die het systeem
impliciet over zichzelf doet ("wat 'published' heet, staat live"). Elke
invariant hieronder codeert een storing die écht is voorgekomen; het veld
`incident` vertelt welke. Ze draaien dagelijks vóór de briefing, en Iris krijgt
de uitkomst in haar prompt zodat stille schade zwaarder weegt dan de volgende
optimalisatie.

**De regel voor wie hier later iets aan toevoegt:** een stille storing die je
repareert, repareer je twee keer — één keer in de code, en één keer als
invariant hier. Anders is de volgende variant ervan weer twaalf dagen onzichtbaar.

Escalatie volgt dezelfde filosofie als `shared/failures.py`: niet alles is een
inbox-item.

  - `blokkerend` — er staat nú iets verkeerds naar buiten (een 404 live, twee
    artikelen die elkaar kannibaliseren). Meteen een kaart: wachten maakt het
    niet beter, en elke dag telt mee in de zoekresultaten.
  - `stil` — een mechanisme dat hoort te werken doet niets. Pas een kaart na
    `_STIL_ESCALATIE_DAGEN`, want een mechanisme dat morgen vanzelf weer aanslaat
    (de weekscan draait maandag) is geen storing maar een moment in de cyclus.
  - `hygiene` — voorraadvervuiling. Nooit een kaart; telt mee in de briefing en
    in de cijfers. Een rode kaart voor 2513 oude radarsignalen is ruis, geen alarm.

Bevindingen leven in `integrity_findings` met een levensloop (first_seen /
last_seen / resolved_at). Dat is bewust geen momentopname: het verschil tussen
"dit is vandaag stuk" en "dit staat al drie weken open" bepaalt de urgentie, en
een bevinding die verdwijnt sluit zichzelf mét bewijs — zo blijft er geen rode
kaart staan voor iets dat allang gerepareerd is.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from ...shared.projects import squash_project

logger = logging.getLogger(__name__)

# ── Ernst-klassen ──────────────────────────────────────────────────────────
BLOKKEREND = "blokkerend"   # er staat nu iets verkeerds naar buiten
STIL = "stil"               # een mechanisme dat hoort te werken, doet niets
HYGIENE = "hygiene"         # voorraadvervuiling; telt, maar alarmeert niet

# Hoeveel dagen een 'stil'-bevinding open mag staan vóór er een kaart komt.
# Drie dagen dekt een weekend plus de maandagse weekscan: mechanismen die
# cyclisch aanslaan krijgen zo de kans om zichzelf te corrigeren.
_STIL_ESCALATIE_DAGEN = 3

# Vanaf hier is een openstaande bevinding geen bevinding meer maar een besluit
# om er niets aan te doen. De kaarttekst zegt dat dan ook.
_VERGRIJSD_DAGEN = 14

# Per invariant maximaal zoveel gevallen tonen. Een invariant die 2000 rijen
# vindt is een cijfer, geen lijst; de volledige telling blijft wél bewaard.
_MAX_GEVALLEN_PER_INVARIANT = 25


class Bevinding(NamedTuple):
    """Eén concreet geval dat een invariant schendt.

    `subject` moet stabiel zijn over runs heen (een job-id, een URL, een
    lead-id) — daarop wordt herkend of dit dezelfde bevinding is als gisteren.
    Een subject dat per run verschilt (een timestamp, een teller) maakt van elke
    ronde een nieuwe bevinding en van de levensloop een leugen.
    """
    subject: str
    detail: str
    project: str = ""


class Invariant(NamedTuple):
    key: str
    titel: str
    incident: str          # de échte storing die dit codeert (datum + wat er misging)
    severity: str
    stap: str              # de concrete stap voor een mens
    check: Callable[[], List[Bevinding]]


# ── Hulpjes ────────────────────────────────────────────────────────────────

def _live_url(publish_result: str) -> str:
    """Haal de gepubliceerde URL uit `content_jobs.publish_result`.

    Twee vormen in het veld, allebei echt voorkomend: het platte
    {"success": true, "url": ...} van de directe publisher, en het genestelde
    {"site": {...}, "gsc": {...}} van de volledige pipeline. Een derde vorm
    (geen JSON) hoort niet te bestaan maar mag deze functie niet laten vallen.
    """
    if not publish_result:
        return ""
    try:
        data = json.loads(publish_result)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    url = data.get("url") or ""
    if not url and isinstance(data.get("site"), dict):
        url = data["site"].get("url") or ""
    return str(url or "").strip()


def _pad_van_url(url: str) -> str:
    """Het laatste pad-segment van een URL — dát is wat een router moet matchen."""
    if not url:
        return ""
    zonder_query = url.split("?", 1)[0].split("#", 1)[0]
    return zonder_query.rstrip("/").rsplit("/", 1)[-1]


# Wat een URL-pad onherstelbaar breekt: alles buiten de tekens die elke router
# ongemoeid doorlaat. Bewust ruimer dan een strikte slug-vorm: een dubbel koppelteken
# ('…-in--4') is lelijk maar geen 404, en een audit die dáárop alarm slaat leert
# de lezer om alarm te negeren.
_PAD_BREEKT = re.compile(r"[^a-z0-9._~-]")

LEEFT = "leeft"        # opgehaald en het rendert echt
WEG = "weg"            # 404, of een catch-all schil (zachte 404)
ONBEKEND = "onbekend"  # niet te bereiken — geen bewijs, in geen van beide richtingen

# Eén antwoord per URL per proces. De audit draait dagelijks over enkele
# tientallen URL's; zonder cache zou elke invariant die dezelfde pagina raakt
# opnieuw het net op.
_STATUS_CACHE: Dict[str, str] = {}


def _pagina_status(url: str) -> str:
    """Staat deze pagina er nog écht? Dezelfde regel als `_verify_live`.

    Waarom dit er moest komen (2 aug 2026): `_check_afgewezen_maar_live` beweerde
    in zijn eigen docstring dat hij "niet twee velden maar twee wérelden"
    vergelijkt, maar keek uitsluitend naar `content_jobs.publish_result` — een
    bewering van het systeem over zijn eigen verleden. Van de negen gemelde
    pagina's bleken er bij nameting vier keihard 404 te geven en één alleen de
    SPA-schil terug te sturen. De kaart vroeg dus om negen pagina's offline te
    halen waarvan er vijf al weg waren, en kon per definitie nooit dichtgaan:
    aan een pagina die er niet meer is valt niets te repareren. Een audit die
    over de buitenwereld oordeelt zonder de buitenwereld te raadplegen, is
    precies de faalmodus waar dit bestand tegen bestaat.

    HTTP 200 is niet genoeg: een SPA serveert voor élke onbekende route dezelfde
    schil met status 200. Vergelijk daarom met een URL die gegarandeerd niet
    bestaat — lijken de antwoorden op elkaar, dan rendert de pagina niet.
    """
    if not url:
        return ONBEKEND
    if url in _STATUS_CACHE:
        return _STATUS_CACHE[url]
    import httpx

    uitkomst = ONBEKEND
    try:
        headers = {"User-Agent": "ImpactOS-waarheidsaudit"}
        with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code == 404 or resp.status_code == 410:
                uitkomst = WEG
            elif resp.status_code != 200:
                # 5xx of een blokkade zegt niets over het bestaan van de pagina.
                uitkomst = ONBEKEND
            else:
                basis, _, _ = url.rstrip("/").rpartition("/")
                probe_url = f"{basis}/impactos-bestaat-niet-{uuid.uuid4().hex[:12]}"
                try:
                    probe = client.get(probe_url)
                except Exception:  # noqa: BLE001
                    uitkomst = LEEFT  # geen vergelijking mogelijk; 200 blijft 200
                else:
                    if probe.status_code != 200:
                        uitkomst = LEEFT  # de site geeft nette 404's, dus de 200 is echt
                    else:
                        a, b = len(resp.text), len(probe.text)
                        gelijk = bool(a) and abs(a - b) <= max(200, a * 0.02)
                        uitkomst = WEG if gelijk else LEEFT
    except Exception as e:  # noqa: BLE001
        # Onbereikbaar ≠ offline. Een DNS-fout of timeout mag geen bevinding
        # sluiten (dan verdwijnt een echt probleem bij de eerste netwerkhik) en
        # ook geen bevinding verzinnen.
        logger.debug("[waarheidsaudit] status van %s onbeslist: %s", url, e)
        uitkomst = ONBEKEND
    _STATUS_CACHE[url] = uitkomst
    return uitkomst


def _project_van_site(site_id: str) -> str:
    if not site_id:
        return ""
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT name FROM sites WHERE id = ?", (site_id,)).fetchone()
        return (row["name"] if row else "") or ""
    except Exception:  # noqa: BLE001 — een audit mag nooit op een naam struikelen
        return ""


# ── De invarianten ─────────────────────────────────────────────────────────
#
# Elke check is een gewone functie die een lijst bevindingen teruggeeft. Ze
# importeren hun domein lokaal: de audit mag geen importcykel introduceren, en
# een kapot domein mag de rest van de audit niet meeslepen (zie `run_audit`).

def _check_interne_taakopdracht_live() -> List[Bevinding]:
    from ..publish.content_pipeline import is_internal_document
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, publish_result FROM content_jobs "
            "WHERE status = 'published'"
        ).fetchall()
    for r in rijen:
        reden = is_internal_document(r["title"] or "", "")
        if reden:
            url = _live_url(r["publish_result"] or "")
            uit.append(Bevinding(
                subject=url or f"job:{r['id']}",
                detail=f"'{(r['title'] or '')[:70]}' staat gepubliceerd maar {reden}",
                project=_project_van_site(r["site_id"]),
            ))
    return uit


def _check_slug_onveilig() -> List[Bevinding]:
    """Toetst het gepubliceerde pad, niet de boekhoudkolom.

    De eerste versie las `content_jobs.slug` en meldde negen blokkerende
    gevallen met de tekst "de pagina geeft vrijwel zeker 404". Acht daarvan
    stonden gewoon live: de publisher slugificeert bij het publiceren, dus de
    URL was netjes ('…-casestudy-anton-127-projecten') terwijl de kolom de ruwe
    titel had bewaard ('…-casestudy-anton-(12'). De voorgeschreven stap —
    opnieuw publiceren + 301 — zou acht gezonde artikelen hebben gedupliceerd.

    Wat er live staat is het pad in `publish_result`. Is er geen URL, dan is dat
    geen slug-probleem maar een bewijs-probleem, en dat is `publicatie_onbewezen`.
    De afwijkende kolom zelf is niet onschuldig maar ook niet blokkerend; die
    heeft nu een eigen invariant (`slug_kolom_wijkt_af_van_url`).
    """
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, slug, publish_result FROM content_jobs "
            "WHERE status = 'published'"
        ).fetchall()
    for r in rijen:
        pad = _pad_van_url(_live_url(r["publish_result"] or ""))
        if not pad:
            continue
        stuk = sorted(set(_PAD_BREEKT.findall(pad)))
        if not stuk:
            continue
        uit.append(Bevinding(
            subject=f"job:{r['id']}",
            detail=(f"gepubliceerde URL eindigt op '{pad[:60]}' — de tekens "
                    f"{' '.join(repr(t) for t in stuk)} overleven geen route-matching, "
                    f"dus deze pagina geeft vrijwel zeker 404 "
                    f"(artikel: '{(r['title'] or '')[:50]}')"),
            project=_project_van_site(r["site_id"]),
        ))
    return uit


def _check_werkbon_in_de_wachtrij() -> List[Bevinding]:
    """Staat er werk klaar dat naar zichzélf verwijst in plaats van naar een artikel?

    Incident 15 aug 2026: de Wachtrij toonde "Artikel klaar (SEO 88/100) —
    goedkeuren publiceert echt op de site" onder de kop "Herschrijf het artikel
    'Zo vind je als organisatie sneller vrijwilligers' tot wereldklasse
    SEO-content (1200-1500 woorden)". Het artikel eronder was af en droeg de
    juiste H1; alleen de titel — en dus de slug, en dus de URL — beschreef de
    opdracht. 179 van de 188 wachtende items hadden die vorm.

    Waarom dit een eigen toets is en niet op `interne_taakopdracht_live` kan
    meeliften: die kijkt naar wat al gepubliceerd IS. Hier is de hele winst dat
    je het ziet vóórdat een mens op Publiceer drukt — daarna is er een URL in de
    wereld en is 301-en het enige antwoord. Vandaar `pending_review` en niet
    `published`, en vandaar blokkerend ondanks dat er nog niets buiten staat:
    één klik scheidt dit van een werkbon als webpagina.

    De toets hergebruikt `is_internal_document` — hetzelfde antwoord op dezelfde
    vraag als de publicatiegate, want twee oordelen over "is dit publiceerbaar?"
    is precies hoe ze uit elkaar lopen.
    """
    from ..publish.content_pipeline import is_internal_document

    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, content_type FROM content_jobs "
            "WHERE status = 'pending_review'"
        ).fetchall()
    for r in rijen:
        # Alleen wat als pagina bedoeld is: een 'hook' of LinkedIn-tekst krijgt
        # geen URL en mag een opdracht als werktitel houden.
        if (r["content_type"] or "blog") != "blog":
            continue
        reden = is_internal_document(r["title"] or "")
        if not reden:
            continue
        uit.append(Bevinding(
            subject=f"job:{r['id']}",
            detail=(f"wacht op goedkeuring als artikel, maar de titel is geen kop: "
                    f"'{(r['title'] or '')[:70]}' — {reden}"),
            project=_project_van_site(r["site_id"]),
        ))
    return uit


_VERZONNEN_AUTORITEIT_RE = re.compile(
    r"\bin mijn (?:\d+\s+)?jaren? als\b"
    r"|\bals (?:directeur|oprichter|eigenaar|ceo|manager|coördinator|voorzitter)\s+van\b"
    r"|\bmet (?:mijn |onze )?\d+\+?\s*jaar(?:en)?\s+ervaring als\b",
    re.IGNORECASE,
)


def _check_merkbrief_verkeerd_project() -> List[Bevinding]:
    """Schrijft een niet-WeAreImpact artikel alsof het Vincent zelf is?

    Incident 19 aug 2026: `brand_brief.get_brand_brief()` werd zonder project
    aangeroepen en dus voor élke Gauntlet-run gebruikt — ook Bijeen,
    Pootgelukkig, LiefdeVoorIedereen en TeambuildingMetImpact. De brief zegt
    letterlijk "SCHRIJF ALS VINCENT VAN MUNSTER, eerste persoon", en zonder een
    echte biografie voor het betreffende project verzon het model er zelf een
    bij: een Bijeen-artikel opende met "in mijn jaren als directeur van
    Stichting de Baan draaide ik meer dan veertig van die dagen, met 180+
    vrijwilligers... 70.000+ geluksmomenten" — een naam, functie en trackrecord
    die niet bestaan. De code-fix scoped de brief nu op project
    (`get_brand_brief(project)`); deze toets is de tweede helft — hij vindt wat
    er vóór de fix al de Wachtrij in is geglipt én vangt een toekomstige
    regressie (een nieuwe caller die de oude, ongescopeerde aanroep terugzet).
    Deterministisch: eerste-persoon functietitel-bij-organisatie-patronen,
    zonder LLM, want een fabricatie-detector die zelf een gateway nodig heeft
    valt stil precies wanneer je hem nodig hebt.
    """
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT cj.id, cj.site_id, cj.title, cj.blog_html, s.name AS project "
            "FROM content_jobs cj JOIN sites s ON s.id = cj.site_id "
            "WHERE cj.status IN ('pending_review', 'published') "
            "AND COALESCE(cj.blog_html, '') != ''"
        ).fetchall()
    for r in rijen:
        if squash_project(r["project"] or "") == "weareimpact":
            continue
        m = _VERZONNEN_AUTORITEIT_RE.search(r["blog_html"] or "")
        if not m:
            continue
        uit.append(Bevinding(
            subject=f"job:{r['id']}",
            detail=(f"'{(r['title'] or '')[:60]}' claimt persoonlijke autoriteit "
                    f"('{m.group(0)}') op een project waar geen echte biografie "
                    f"voor beschikbaar is — waarschijnlijk verzonnen."),
            project=r["project"] or "",
        ))
    return uit


def _check_slug_kolom_wijkt_af() -> List[Bevinding]:
    """De opgeslagen slug is niet wat er live staat.

    Onschuldig voor de bezoeker — de URL werkt — maar het is een stille leugen
    in de boekhouding, en die kostte op 2 aug 2026 acht valse blokkerende
    alarmen omdat `slug_onveilig` deze kolom las in plaats van de URL. Elke
    andere lezer van `content_jobs.slug` (dedupe, sitemap, interne links) loopt
    hetzelfde risico. Hygiëne: melden in de cijfers, nooit als kaart.
    """
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, slug, publish_result FROM content_jobs "
            "WHERE status = 'published' AND COALESCE(slug, '') != ''"
        ).fetchall()
    for r in rijen:
        pad = _pad_van_url(_live_url(r["publish_result"] or ""))
        if not pad or pad == (r["slug"] or ""):
            continue
        uit.append(Bevinding(
            subject=f"job:{r['id']}",
            detail=(f"kolom slug = '{(r['slug'] or '')[:45]}' maar de live URL eindigt "
                    f"op '{pad[:45]}' — wie de kolom leest, leest niet de wereld "
                    f"(artikel: '{(r['title'] or '')[:40]}')"),
            project=_project_van_site(r["site_id"]),
        ))
    return uit


def _check_zoekwoord_kannibalisatie() -> List[Bevinding]:
    from ..publish.content_pipeline import _keyword_key
    per_site: Dict[str, Dict[str, List]] = {}
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, keyword, publish_result FROM content_jobs "
            "WHERE status = 'published' AND COALESCE(keyword, '') != ''"
        ).fetchall()
    for r in rijen:
        sleutel = _keyword_key(r["keyword"])
        if not sleutel:
            continue
        per_site.setdefault(r["site_id"] or "", {}).setdefault(sleutel, []).append(r)

    uit: List[Bevinding] = []
    for site_id, per_kw in per_site.items():
        for sleutel, jobs in per_kw.items():
            if len(jobs) < 2:
                continue
            titels = " | ".join((j["title"] or "")[:45] for j in jobs[:3])
            uit.append(Bevinding(
                # Sleutel op site+zoekwoord, niet op de job-ids: verdwijnt er één
                # artikel, dan is dít geval opgelost en niet "een ander geval".
                subject=f"{site_id}::{sleutel[:80]}",
                detail=(f"{len(jobs)} artikelen live op één zoekwoord "
                        f"'{(jobs[0]['keyword'] or '')[:50]}' — ze kannibaliseren "
                        f"elkaar: {titels}"),
                project=_project_van_site(site_id),
            ))
    return uit


def _canonieke_pagina(url: str) -> str:
    """Eén sleutel per échte pagina.

    Drie vormen van dezelfde pagina die in `gsc_history` naast elkaar staan en
    die zonder deze stap als 'kannibalisatie' zouden tellen: met en zonder
    `www.`, met en zonder afsluitende slash, en met een querystring
    (`?page=1`). Google indexeert ze los, maar het is één document — en een
    invariant die een site aanrekent dat hij onder twee hostnamen bekend staat,
    meldt het verkeerde probleem met de verkeerde stap eronder.
    """
    if not url:
        return ""
    kaal = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    kaal = re.sub(r"^https?://", "", kaal, flags=re.I).lower()
    return re.sub(r"^www\.", "", kaal)


def _is_indexpagina(url: str) -> bool:
    """Is dit een sectie-/overzichtspagina in plaats van een artikel?

    `/blog` en `/kennisbank` vertonen op de onderwerpen van hun eigen artikelen
    — dat hóórt zo en is geen kannibalisatie. Zelfde vuistregel als
    `external_content.fetch_live_sitemap_slugs` (een artikelslug heeft
    koppeltekens of is lang), zodat "is dit een artikel?" één antwoord heeft.
    """
    pad = _canonieke_pagina(url)
    if "/" not in pad:
        return True  # de homepage; die vertoont op alles en concurreert met niets
    laatste = pad.rsplit("/", 1)[-1]
    return not laatste or ("-" not in laatste and len(laatste) < 8)


def _check_cluster_kannibalisatie() -> List[Bevinding]:
    """Meerdere eigen pagina's die bij Google op hetzelfde zoekwoord vertonen.

    De wereld-versie van `zoekwoord_kannibalisatie`. Die toets leest
    `content_jobs.keyword` — een bewering van het systeem over zijn eigen werk,
    en dus blind voor alles wat buiten Impact OS om is gepubliceerd. Bij Bewaard
    voor Jou stonden 102 pagina's live waarvan er acht op 'levensverhaal
    vastleggen' vertoonden; `content_jobs` kende er daarvan twee, dus de
    bestaande toets zweeg terwijl de site zichzelf op zijn kernzoekwoord
    beconcurreerde en op positie 25 bleef staan.

    `gsc_history` is hier de tweede wereld: welke pagina op welk zoekwoord
    vertoont is een waarneming, geen administratie.
    """
    from ..seo.opportunity_quality import squash, tokens

    per_cluster: Dict[Tuple[str, str], Dict[str, Any]] = {}
    with get_conn() as conn:
        # Merknaam per site: op je eigen naam hóórt de hele site te verschijnen.
        # 'bijeen komen' haalde anders /functies en /sign-in binnen als
        # "kannibalen" — twee pagina's die precies doen wat ze moeten doen.
        merk = {
            r["id"]: squash((r["base_url"] or "").split("//")[-1].split("/")[0]
                            .replace("www.", "").split(".")[0])
            for r in conn.execute("SELECT id, base_url FROM sites").fetchall()
        }
        rijen = conn.execute(
            "SELECT h.site_id, h.page_url, h.top_query, h.impressions, h.position "
            "FROM gsc_history h JOIN ("
            "  SELECT site_id, page_url, MAX(date) AS d FROM gsc_history"
            "  WHERE scope = 'page' GROUP BY site_id, page_url"
            ") l ON l.site_id = h.site_id AND l.page_url = h.page_url AND l.d = h.date "
            "WHERE h.scope = 'page' AND COALESCE(h.top_query, '') != ''"
        ).fetchall()

    for r in rijen:
        query = (r["top_query"] or "").strip()
        # Merk- en navigatiequeries overslaan: op 'bijeen' hóórt de hele site te
        # verschijnen, en dat als kannibalisatie melden is precies het soort
        # onterechte rode kaart dat een lezer leert de audit te negeren.
        if len(tokens(query)) < 2:
            continue
        eigen_merk = merk.get(r["site_id"] or "")
        if eigen_merk and len(eigen_merk) >= 4 and eigen_merk in squash(query):
            continue
        if _is_indexpagina(r["page_url"] or ""):
            continue
        sleutel = (r["site_id"] or "", squash(query))
        if not sleutel[1]:
            continue
        cluster = per_cluster.setdefault(sleutel, {"query": query, "paginas": {}})
        pad = _canonieke_pagina(r["page_url"] or "")
        if not pad:
            continue
        try:
            positie = float(r["position"] or 0)
        except (TypeError, ValueError):
            positie = 0.0
        bestaand = cluster["paginas"].get(pad)
        # Dezelfde pagina onder twee hostnamen: houd de best rankende variant.
        if bestaand is None or (positie and positie < bestaand[0]):
            cluster["paginas"][pad] = (positie, int(r["impressions"] or 0))

    uit: List[Bevinding] = []
    for (site_id, sleutel), cluster in per_cluster.items():
        paginas = cluster["paginas"]
        if len(paginas) < 2:
            continue
        gesorteerd = sorted(paginas.items(), key=lambda kv: kv[1][0] or 999)
        beste = gesorteerd[0]
        namen = ", ".join("/" + p.split("/", 1)[-1][:40] for p, _ in gesorteerd[:3])
        uit.append(Bevinding(
            # Site + zoekwoord, niet de pagina's: verdwijnt er één, dan is dít
            # geval opgelost en niet "een ander geval".
            subject=f"{site_id}::{sleutel[:80]}",
            detail=(f"{len(paginas)} eigen pagina's vertonen op "
                    f"'{cluster['query'][:50]}' — ze verdelen de autoriteit; "
                    f"de beste staat op positie {beste[1][0]:.1f}".replace(".", ",")
                    + f". Betrokken: {namen}"),
            project=_project_van_site(site_id),
        ))
    return uit


# Paren die `is_same_topic` ten onrechte als duplicate markeert maar wél
# legitieme, afzonderlijke artikelen zijn. Uitgesloten van de duplicaat-melding
# zodat ze niet per ongeluk samengevoegd worden (zie Taak 0, canonicalisatie-plan).
# Toegevoegd 2026-08-10 na handmatige review van de 25 sitemap_dubbele_pagina-
# bevindingen: deze 3 zijn valse positieven (verschillende dieren / onderwerpen).
_EXCLUDE_DUPLICATE_PAIRS = {
    # Pootgelukkig: hond vs konijn — twee aparte adoptiegidsen
    ("hond-adopteren-uit-het-asiel-complete-gids",
     "konijn-adopteren-uit-het-asiel-complete-gids"),
    # DatingAssistent: profielfoto-stappen vs profiel-stappenplan — andere angles
    ("profielfoto-5-stappen", "profiel-stappenplan"),
    # DatingAssistent: fotoshoot vs hoeveel-foto's — verschillende onderwerpen
    ("fotoshoot", "hoeveel-fotos"),
}


def _check_sitemap_dubbele_pagina() -> List[Bevinding]:
    """Twee live pagina's op dezelfde site die over hetzelfde onderwerp gaan —
    gevonden in de sitemap zelf, zonder GSC, LLM of profiel nodig.

    De derde wereld naast `zoekwoord_kannibalisatie` (leest `content_jobs`) en
    `cluster_kannibalisatie` (leest `gsc_history`): die laatste ziet alleen
    duplicaten die al vertoningen krijgen, dus een dubbele pagina waar nog
    niemand op zocht blijft daarin onzichtbaar. Aanleiding (7 aug 2026): zeven
    zulke paren op steentjebijsteentje.nl (vijf een letterlijke '-2'-kopie) en
    bewaardvoorjou.nl, gevonden via `fetch_live_sitemap_slugs` +
    `is_same_topic` — dezelfde twee bouwstenen als de Kansen-gate, want twee
    antwoorden op "is dit dezelfde pagina?" is precies hoe dit soort gaten
    ontstaat. Op dat moment stonden er 12 blokkerende
    `cluster_kannibalisatie`-bevindingen open en geen daarvan dekte dit.
    """
    from ..seo.external_content import fetch_live_sitemap_slugs
    from ..seo.opportunity_quality import is_same_topic

    uit: List[Bevinding] = []
    with get_conn() as conn:
        sites = [dict(r) for r in conn.execute(
            "SELECT id, name, base_url FROM sites WHERE COALESCE(base_url, '') != ''"
        ).fetchall()]

    for site in sites:
        try:
            slugs = [x["slug"] for x in fetch_live_sitemap_slugs(site) if x.get("slug")]
        except Exception:  # noqa: BLE001 — één trage/dode sitemap mag de rest niet blokkeren
            logger.debug("[waarheidsaudit] sitemap ophalen mislukt voor %s", site.get("name"))
            continue
        gezien: set = set()
        for i, a in enumerate(slugs):
            if a in gezien:
                continue
            for b in slugs[i + 1:]:
                if b in gezien or a == b:
                    continue
                # Valse positieven uitsluiten: paren die `is_same_topic` foutief
                # als duplicate ziet maar wél losse, geldige artikelen zijn.
                if (a, b) in _EXCLUDE_DUPLICATE_PAIRS or (b, a) in _EXCLUDE_DUPLICATE_PAIRS:
                    continue
                if is_same_topic(a.replace("-", " "), b):
                    gezien.add(b)
                    uit.append(Bevinding(
                        subject=f"{site['id']}::{a[:60]}",
                        detail=(f"Twee live pagina's lijken over hetzelfde onderwerp te gaan: "
                                f"/{a[:50]} en /{b[:50]}. Kies er één, haal de andere offline "
                                f"met een 301 ernaartoe."),
                        project=site.get("name") or "",
                    ))
                    break
    return uit


def _check_publicatie_onbewezen() -> List[Bevinding]:
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, publish_result FROM content_jobs "
            "WHERE status = 'published'"
        ).fetchall()
    for r in rijen:
        if not _live_url(r["publish_result"] or ""):
            uit.append(Bevinding(
                subject=f"job:{r['id']}",
                detail=(f"'{(r['title'] or '')[:60]}' heet 'published' maar er is geen "
                        f"URL vastgelegd — er is geen bewijs dat er iets live staat"),
                project=_project_van_site(r["site_id"]),
            ))
    return uit


def _check_afgewezen_maar_live() -> List[Bevinding]:
    """De database zegt 'afgewezen', het web zegt 'gepubliceerd'.

    Ontdekt op 2 aug 2026 door deze audit zelf, en meteen de scherpste toets van
    de set: hij vergelijkt niet twee velden maar twee wérelden. Negen pagina's
    stonden live terwijl hun job op `rejected` stond — waaronder 'Impact OS
    end-to-end publicatietest' op ictusgo.nl, de site van een klant.

    Zo ontstaat het: een job wordt gepubliceerd, iemand wijst hem daarna in de
    Wachtrij af, en de afwijzing verandert alleen de rij in de database. Er is
    geen stap die de pagina ook echt offline haalt. Daarna kijkt niemand er ooit
    nog naar, want in élk overzicht is de job keurig 'rejected' — hij verdwijnt
    uit de wachtrij, uit de tellingen, uit het zicht.

    De eerste versie stopte bij de URL in `publish_result` en redeneerde: staat
    daar iets, dan is er ooit gepubliceerd, en dat spreekt de status tegen. Dat
    is een bewering van het systeem over zijn eigen verleden — precies het soort
    bewijs dat dit bestand wantrouwt. Bij nameting op 2 aug 2026 gaven vier van
    de negen gemelde pagina's een harde 404 en gaf er één alleen de SPA-schil
    terug; ze wáren dus allang offline gehaald. De kaart vroeg om werk dat al
    gedaan was en kon nooit meer dichtgaan, want de bevinding hing aan een veld
    dat nooit meer verandert. Sindsdien halen we het net wél op (`_pagina_status`).

    Onbereikbaar is geen vrijspraak: bij een timeout of DNS-fout blijft de
    bevinding staan, mét die onzekerheid in de tekst. Anders sluit één
    netwerkhik een pagina die wél degelijk live staat.
    """
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, status, publish_result FROM content_jobs "
            "WHERE status IN ('rejected', 'stuck', 'needs_work') "
            "AND COALESCE(publish_result, '') != ''"
        ).fetchall()
    for r in rijen:
        url = _live_url(r["publish_result"] or "")
        if not url:
            continue
        status = _pagina_status(url)
        if status == WEG:
            continue  # de wereld klopt inmiddels met de database
        twijfel = ("" if status == LEEFT else
                   " (pagina was nu niet te bereiken — controleer zelf of hij nog leeft)")
        uit.append(Bevinding(
            subject=url,
            detail=(f"'{(r['title'] or '')[:55]}' staat in de database op "
                    f"'{r['status']}' maar staat nog live op {url} — "
                    f"afwijzen haalt een pagina niet offline{twijfel}"),
            project=_project_van_site(r["site_id"]),
        ))
    return uit


def _check_kans_vastgelopen() -> List[Bevinding]:
    """Kansen die uitgedeeld zijn maar nergens meer toe leiden.

    Dit is de invariant-vorm van `seo/engine.reconcile_opportunities`: die
    functie rúimt op, deze controleert of het opruimen ook echt gebeurt. Draait
    de weekscan niet (of faalt de reconciliatie stil), dan zie je het hier —
    niet pas als de contentmotor is drooggevallen.
    """
    from ..seo import engine as seo_engine
    uit: List[Bevinding] = []
    with get_conn() as conn:
        sites = [r["id"] for r in conn.execute("SELECT id FROM sites")]
    for site_id in sites:
        for opp in seo_engine.list_opportunities(site_id=site_id, status="in_progress"):
            query = opp.get("query", "")
            if seo_engine._published_job_for_query(site_id, query):
                continue
            if seo_engine._has_open_job(site_id, query):
                continue
            uit.append(Bevinding(
                subject=f"opp:{opp['id']}",
                detail=(f"zoekwoord '{query[:60]}' staat op 'in_progress' zonder lopend "
                        f"werk en zonder live artikel — het is verbruikt zonder resultaat"),
                project=_project_van_site(site_id),
            ))
    return uit


def _check_goal_vastgelopen_zonder_voortgang() -> List[Bevinding]:
    """Een 'running' goal waar niets meer aan te wachten valt.

    20 aug 2026: 'G2 — AEO-contentmotor WeAreImpact' (en twee zusje-goals voor
    andere projecten) stonden al sinds 13 aug op status='running', met een fase
    waarin alle vier de publicatietaken op 'failed'/'aborted' eindigden (elk
    artikel haalde de kwaliteitsgate niet) en géén enkele op 'completed'. De
    executie-lus markeert een fase alleen als 'completed' als er minstens één
    voltooide taak in zit; bij nul completed taken bleef de fase openstaan en
    de goal draaide zijn while-lus voor altijd rond zonder iets te doen —
    permanent 'running', zonder voortgang. Dat is dubbel onzichtbaar: een
    lopend doel wordt bewust gedempt in het Actiecentrum (7c-bis, "een lopend
    doel is een status, geen actie"), en zelfherstel (`iris/selfheal.py`)
    opereert op status='error', niet op een oneindige lus die zichzelf nooit
    als fout meldt. De executie-lus markeert zo'n fase nu 'failed' zodat de
    goal kan doorlopen naar 'partial' (`goal/service.py::_execution_loop`) —
    deze invariant vangt wat er vóór die fix al vastzat, en een toekomstige
    variant van dezelfde dood.
    """
    with get_conn() as conn:
        goals = conn.execute(
            "SELECT id, title, project, updated_at FROM goals WHERE status = 'running'"
        ).fetchall()
        uit: List[Bevinding] = []
        for g in goals:
            rows = conn.execute(
                "SELECT status FROM goal_tasks WHERE goal_id = ?", (g["id"],)
            ).fetchall()
            if not rows:
                continue
            statussen = {r["status"] for r in rows}
            # Nog iets te doen of nog iets bezig — geen deadlock, gewoon werk.
            if statussen & {"pending", "ready", "running"}:
                continue
            # Alles is terminaal (completed/failed/aborted) maar de goal zelf
            # staat nog op 'running': de lus heeft nergens meer iets aan te
            # wachten en komt hier nooit meer uit vanzelf.
            uit.append(Bevinding(
                subject=f"goal:{g['id']}",
                detail=(f"'{g['title']}' staat op 'running' maar elke taak is al "
                        f"afgerond (completed/failed/aborted) — de goal-lus zit "
                        f"vast en zal zichzelf niet meer afsluiten"),
                project=g["project"] or "",
            ))
    return uit


# Onder dit aantal gepubliceerde artikelen zegt 'geen eigen bewijs' niets over
# de site: dan is er simpelweg nog nauwelijks gepubliceerd.
_BEWIJS_MIN_ARTIKELEN = 3
# Zoveel gemeten artikelen moeten er zijn vóór 'de haak wordt niet gebruikt'
# een uitspraak is in plaats van een toevalstreffer.
_BEWIJS_MIN_GEMETEN = 3


def _check_artikel_zonder_eigen_bewijs() -> List[Bevinding]:
    """Publiceren we iets dat alleen wíj kunnen schrijven?

    De kennisbank-haak bestaat sinds de Goldie-pipeline: `_make_outline` eist
    dat één sectie de casestudy als bewijs gebruikt en `seo/knowledge.py` matcht
    er deterministisch één. Alleen stond de tabel leeg — 4 casestudies op één van
    de twaalf sites (5 aug 2026) — en dan valt die eis stilzwijgend weg. Het
    resultaat is een artikel dat elke concurrent met hetzelfde model ook krijgt,
    en de kwaliteitsgate ziet daar niets van: die meet vorm, en generiek scoort
    probleemloos 84.

    Eén bevinding per site, niet per artikel: de vraag "waar ontbreekt
    bewijsmateriaal?" wordt per site beantwoord en per site opgelost. De twee
    redenen blijven bewust gescheiden, want ze wijzen naar verschillende mensen —
    een lege kennisbank is werk voor Vincent, een ongebruikte kennisbank is een
    gat in de schrijfketen.
    """
    uit: List[Bevinding] = []
    with get_conn() as conn:
        sites = conn.execute(
            "SELECT s.id, s.name, COUNT(j.id) AS artikelen "
            "FROM sites s JOIN content_jobs j ON j.site_id = s.id "
            "WHERE j.status = 'published' GROUP BY s.id, s.name"
        ).fetchall()
        for site in sites:
            if site["artikelen"] < _BEWIJS_MIN_ARTIKELEN:
                continue
            cases = conn.execute(
                "SELECT COUNT(*) AS n FROM case_studies "
                "WHERE site_id = ? AND status = 'active'", (site["id"],)
            ).fetchone()["n"]
            project = _project_van_site(site["id"])
            if not cases:
                uit.append(Bevinding(
                    subject=f"bewijs:{site['id']}",
                    detail=(f"{site['artikelen']} artikelen live op "
                            f"{site['name'] or site['id']} en nul casestudies in de "
                            f"kennisbank — elk van die artikelen is reproduceerbare "
                            f"AI-tekst zonder eigen bewijs"),
                    project=project,
                ))
                continue
            # De site hééft bewijsmateriaal: gebruikt de schrijfketen het ook?
            # Alleen artikelen mét een oordeel tellen mee — 'niet gemeten' mag
            # nooit als 'geen bewijs' worden gelezen.
            gemeten = mislukt = 0
            for rij in conn.execute(
                "SELECT qc_report FROM content_jobs WHERE site_id = ? "
                "AND status = 'published' AND COALESCE(qc_report, '') != ''",
                (site["id"],),
            ):
                try:
                    oordeel = (json.loads(rij["qc_report"]) or {}).get("eigen_bewijs")
                except (ValueError, TypeError):
                    continue
                if not isinstance(oordeel, dict):
                    continue
                gemeten += 1
                mislukt += int(not oordeel.get("pass"))
            if gemeten >= _BEWIJS_MIN_GEMETEN and mislukt * 2 > gemeten:
                uit.append(Bevinding(
                    subject=f"bewijs-ongebruikt:{site['id']}",
                    detail=(f"{mislukt} van {gemeten} gemeten artikelen op "
                            f"{site['name'] or site['id']} verwerkte de gekoppelde "
                            f"casestudy niet — het bewijsmateriaal ligt er wel, maar "
                            f"komt niet in de tekst terecht"),
                    project=project,
                ))
    return uit


def _check_contentleerlus_zonder_lessen() -> List[Bevinding]:
    """De contentleerlus draait wekelijks, meldt 'ok' en levert nul lessen.

    Gemeten op 5 aug 2026: `agent_lessons` bevatte 2 rijen, allebei van de
    beursagent; de job `content_learning_eval` draaide 3 augustus en bracht
    niets voort. Dat kán kloppen (te weinig gerijpte artikelen om een verschil
    te meten), maar niemand kan het verschil zien tussen "nog niets te leren" en
    "de meting werkt niet" — en precies dat onderscheid is de reden dat deze
    invarianten bestaan. De bevinding draagt daarom de gemeten oorzaak mee.

    Eén bevinding, geen lijst: dit is een uitspraak over het mechanisme.
    """
    with get_conn() as conn:
        lessen = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_lessons WHERE agent = 'content'"
        ).fetchone()["n"]
        if lessen:
            return []
        run = conn.execute(
            "SELECT last_ok_at FROM scheduler_runs WHERE job_id = 'content_learning_eval'"
        ).fetchone()
    # Nooit geslaagd? Dan is dit geen stille leerlus maar een kapotte job, en
    # daar gaat `job_nooit_geslaagd` over — twee kaarten voor één storing is
    # precies wat het Actiecentrum onbruikbaar maakt.
    if not run or not (run["last_ok_at"] or ""):
        return []

    from ..publish import content_learning
    try:
        stats = content_learning.cohort_stats()
    except Exception as e:
        oorzaak = f"de cohortmeting zelf faalt ({str(e)[:80]})"
        kleinste = None
    else:
        tellingen = [w["n"] for dim in stats.values() for w in dim.values()]
        kleinste = min(tellingen) if tellingen else 0
        gemeten = max(
            (sum(w["n"] for w in dim.values()) for dim in stats.values()), default=0)
        if kleinste >= content_learning.MIN_ARTICLES_PER_VALUE:
            oorzaak = (f"{gemeten} gerijpte artikelen zijn wél gemeten, maar geen enkel "
                       f"vormverschil haalt de les-drempel")
        else:
            oorzaak = (f"maar {gemeten} gepubliceerde artikelen zijn gerijpt én meetbaar "
                       f"(kleinste cohort {kleinste}, nodig "
                       f"{content_learning.MIN_ARTICLES_PER_VALUE}) — er valt nog niets "
                       f"te concluderen, en dat zegt de job nergens")
    return [Bevinding(
        subject="agent_lessons:content",
        detail=(f"de contentleerlus draaide voor het laatst op "
                f"{(run['last_ok_at'] or '')[:10]} en heeft nog nooit een les "
                f"vastgelegd: {oorzaak}"),
        project="Content",
    )]


def _check_leerlus_leeg() -> List[Bevinding]:
    """Draait Iris' leer-lus, of staat hij alleen in de architectuur?

    Op 27 jul 2026 waren er 51 actieve lessen en 2 koppelingen aan een
    voorspelling: geen enkele les won of verloor ooit vertrouwen. De code was
    er, de tabel was er, en de briefing zei elke dag netjes "geleerd". Zulke
    stilte is per definitie onzichtbaar — daarom telt hij hier.

    Eén bevinding, geen 51: dit is een uitspraak over het mechanisme.
    """
    with get_conn() as conn:
        rij = conn.execute(
            "SELECT COUNT(*) AS n, "
            "       SUM(CASE WHEN predictions_made > 0 THEN 1 ELSE 0 END) AS gekoppeld "
            "FROM iris_lessons WHERE active = 1"
        ).fetchone()
    totaal = rij["n"] or 0
    gekoppeld = rij["gekoppeld"] or 0
    # Onder de tien lessen zegt het aandeel niets: dan is 'nog geen koppeling'
    # gewoon een jonge leerlus, geen kapotte.
    if totaal < 10:
        return []
    aandeel = gekoppeld / totaal
    if aandeel >= 0.2:
        return []
    return [Bevinding(
        subject="iris_lessons",
        detail=(f"{gekoppeld} van {totaal} actieve lessen is ooit aan een voorspelling "
                f"gekoppeld ({aandeel * 100:.0f}%) — zonder koppeling wint of verliest "
                f"een les nooit vertrouwen en leert de lus dus niets"),
        project="Iris",
    )]


def _check_triage_remedie_zonder_effect() -> List[Bevinding]:
    """Een 'bekende remedie' die nog nooit iets heeft opgelost.

    6 aug 2026: op de audit-kaart 'Meerdere eigen pagina's vertonen bij Google op
    één zoekwoord' koos de triage-LLM een contentronde. Die leverde niets op —
    en werd tóch als de bekende aanpak voor die handtekening vastgelegd, want
    `_remember` schrijft de keuze weg vóórdat het resultaat bekend is. Elke
    volgende klik op "Analyseer & fix" zou hem dan zonder LLM herhalen: een
    remedie die per constructie niets kan doen, ingesleten als beleid, met
    telkens een nette melding eronder.

    `_verleer_bij_aanhoudend_falen` zet zo'n remedie na drie vruchteloze
    pogingen op inactief. Deze toets is de tegenproef: blijft er tóch één actief
    staan met alleen mislukkingen, dan werkt die rem niet — en dan zegt de knop
    weer iets wat niet waar is.

    Bewust alleen de agent-acties uit de triage-whitelist. `selfheal` deelt deze
    tabel maar schrijft `probe`/`network_check`, en dáár is drie keer mislukken
    geen storing: die ronde stopt er zelf mee (`_MAX_POGINGEN`) en de kaart
    blijft gewoon staan. Beide meemelden zou dit een teller van mislukte
    probes maken in plaats van een uitspraak over ingesleten beleid.
    """
    from .triage import _ALLOWED_REMEDIES

    agent_acties = sorted(_ALLOWED_REMEDIES - {"human_step"})
    with get_conn() as conn:
        try:
            rijen = conn.execute(
                "SELECT signature, sample_action, remedy_type, attempts, project "
                "FROM iris_error_fixes WHERE active = 1 AND successes = 0 "
                f"AND attempts >= 3 AND remedy_type IN ({','.join('?' * len(agent_acties))})",
                agent_acties,
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [Bevinding(
        subject=f"fix:{r['signature'][:80]}",
        detail=(f"remedie '{r['remedy_type']}' voor '{(r['sample_action'] or '?')}' "
                f"draaide {r['attempts']}× zonder één succes en geldt nog steeds als "
                f"de bekende aanpak — elke klik herhaalt hem zonder nieuwe diagnose"),
        project=r["project"] or "Iris",
    ) for r in rijen]


def _check_voorspelling_niet_afgerekend() -> List[Bevinding]:
    """Voorspellingen waarvan de horizon ruim verstreken is en die nog open staan.

    `evaluate_due` hoort ze af te rekenen bij elke briefing. Staan ze er dagen
    later nog, dan draait de afrekening niet — en een voorspelling die nooit
    wordt afgerekend is geen voorspelling maar een wens.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, project, metric, due_date, statement FROM iris_predictions "
            "WHERE status = 'open' AND due_date < date('now', '-2 day')"
        ).fetchall()
    return [Bevinding(
        subject=f"pred:{r['id']}",
        detail=(f"voorspelling over {r['metric']} ({r['project']}) liep af op "
                f"{r['due_date']} en is nooit afgerekend: "
                f"'{(r['statement'] or '')[:60]}'"),
        project="Iris",
    ) for r in rijen]


# Acties waarvan een uitkomstkaart een artefact hóórt te hebben. Bewust een
# witte lijst: veel acties zijn puur informatief ('dagbriefing', 'iris_actie')
# en een artefact eisen zou van deze invariant een ruisgenerator maken.
_ARTEFACT_PLICHTIG = (
    "publiceren", "publicatie", "artikel", "content_run", "seo_refresh",
    "outreach", "linkbuilding", "agentctl",
)


def _check_uitkomst_zonder_artefact() -> List[Bevinding]:
    """CLAUDE.md: "elke taak/run die 'klaar' claimt hoort een artefact-link te
    hebben". Deze invariant is die regel, uitvoerbaar gemaakt.

    Een geslaagde publicatie zonder URL is precies de vorm die 78 doelen in juli
    als 'voltooid' liet afsluiten op louter concepten.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, project, action, detail, created_at FROM activity_log "
            "WHERE status = 'ok' AND COALESCE(artifact, '') = '' "
            "AND created_at >= datetime('now', '-7 day')"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        actie = (r["action"] or "").lower()
        if not any(p in actie for p in _ARTEFACT_PLICHTIG):
            continue
        uit.append(Bevinding(
            subject=f"log:{r['id']}",
            detail=(f"'{r['action']}' meldde succes zonder artefact-link "
                    f"({(r['detail'] or '')[:60]}) — er is niets aanwijsbaars opgeleverd"),
            project=r["project"] or "",
        ))
    return uit


def _check_agentctl_run_zonder_effect() -> List[Bevinding]:
    """Agent Control's 'Voer allemaal uit' (13 aug 2026) spawnde 13 Gauntlet-
    runs zonder tool-access; niets in de codebase las het `run_id` ooit terug,
    dus landde er niets in de Wachtrij of het Actiecentrum — de kern-fout van
    dit hele bestand, hier zelf gevonden. `agentctl_deploys` + een poller per
    pijler (agentctl/suggest.py) lossen dat op. Deze toets vangt de regressie
    waarin de poller zelf sterft — bv. een serverherstart tijdens het pollen,
    dezelfde asyncio-val die elders in CLAUDE.md staat — en een 'running'-rij
    voor altijd open laat staan.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, run_id, project, pillar, created_at FROM agentctl_deploys "
            "WHERE status = 'running' AND created_at < datetime('now', '-1 hour')"
        ).fetchall()
    return [Bevinding(
        subject=f"agentctl_deploy:{r['id']}",
        detail=(f"'{r['pillar']}'-deploy voor {r['project']} (run {r['run_id'] or '-'}) "
                f"staat sinds {r['created_at']} nog op 'running' — de poller heeft 'm "
                "nooit afgesloten."),
        project=r["project"] or "",
    ) for r in rijen]


def _check_pijler_dubbel_ingezet() -> List[Bevinding]:
    """Iris' briefing (`iris/actions.py:content_run`/`seo_refresh`) en Agent
    Control se pijler-dispatcher (`agentctl/suggest.py`) zijn twee
    onafhankelijke besliswegen naar hetzelfde werk — allebei lezen ze dezelfde
    pijlerscores en allebei mogen ze zelfstandig een contentmotor- of
    SEO-run starten.

    Incident 22 aug 2026 (gemeten tijdens een architectuur-analyse, vóór het
    ooit een kaart opleverde): `iris/metrics.py:_content_pillar` telt alleen
    `status='published'` mee, nooit `pending_review`. Schreef Iris om 06:45 een
    artikel, dan bleef de content-score van dat project laag — het concept
    stond pas ter goedkeuring. Om 07:00 zag de scheduler-job `iris_auto_deploy`
    exact diezelfde pijler nog als de zwakste en startte een tweede, volledige
    Gauntlet-run voor dezelfde site — het duurste pad in het systeem (zie
    `orchestrator_teller_teruggezet` voor wat zo'n dubbele weg kan kosten).
    `iris/pillar_guard.py` is de gedeelde toets die beide mechanismen nu vóór
    het starten raadplegen; deze invariant bewijst dat de guard het ook echt
    tegenhoudt. Vergelijkt twee administraties (`activity_log` actie
    `iris_actie` tegen `agentctl_deploys`) op dezelfde dag + project + pijler
    — niet twee velden, dezelfde reden als `orchestrator_teller_teruggezet`.
    """
    prefixmap = {"content": "Contentmotor gestart", "seo": "SEO-refresh gestart"}
    with get_conn() as conn:
        deploys = conn.execute(
            "SELECT project, pillar, date(created_at) AS d FROM agentctl_deploys "
            "WHERE pillar IN ('content','seo') AND status IN ('staged','running','no_effect') "
            "AND created_at >= datetime('now', '-14 day')"
        ).fetchall()
        uit: List[Bevinding] = []
        for r in deploys:
            prefix = prefixmap.get(r["pillar"])
            if not prefix or not r["project"]:
                continue
            row = conn.execute(
                "SELECT 1 FROM activity_log WHERE action = 'iris_actie' AND project = ? "
                "AND detail LIKE ? AND date(created_at) = ? LIMIT 1",
                (r["project"], prefix + "%", r["d"]),
            ).fetchone()
            if row:
                uit.append(Bevinding(
                    subject=f"pijler_dubbel:{r['project']}:{r['pillar']}:{r['d']}",
                    detail=(f"Op {r['d']} deden zowel Iris' briefing als Agent Control "
                            f"de pijler '{r['pillar']}' voor {r['project']} — twee "
                            "onafhankelijke runs voor hetzelfde werk op dezelfde dag."),
                    project=r["project"],
                ))
    return uit


def _check_content_job_meervoudig_herschreven() -> List[Bevinding]:
    """Hetzelfde artikel meerdere keren tegelijk 'in bewerking' — het
    structurele signaal dat een herschrijf-mechanisme een bronrecord niet
    afsluit en het dus telkens opnieuw oppakt.

    Incident 14 aug 2026: `orchestrator.process_one_under_threshold` liet een
    succesvol herschreven 'rejected'-bronrecord gewoon 'rejected' staan (geen
    `mark_superseded`), dus vond de volgende aanroep hetzelfde record terug en
    herschreef het opnieuw — één Bijeen- en één WeAreImpact-artikel elk 10+
    keer op één dag, genoeg om de hele dagbudget leeg te trekken. De code-fix
    (cross-run cap + `mark_superseded`) voorkomt het orchestrator-pad; deze
    toets is generiek (elke bron die dupliceert in plaats van sluit — ook een
    toekomstige regressie of een ánder mechanisme) en telt gewoon rijen.
    """
    from ..seo.opportunity_quality import squash
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT site_id, title, status, id FROM content_jobs "
            "WHERE status IN ('pending_review','needs_work','rejected','stuck') "
            "AND created_at >= datetime('now', '-14 day')"
        ).fetchall()
    groepen: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
    for r in rijen:
        sleutel = (r["site_id"], squash(r["title"] or ""))
        if not sleutel[1]:
            continue
        groepen.setdefault(sleutel, []).append(r)
    uit: List[Bevinding] = []
    for (site_id, _), groep in groepen.items():
        if len(groep) <= 2:
            continue  # 2 gelijktijdige versies is een normale herschrijfronde
        project = _project_van_site(site_id)
        titel = groep[0]["title"]
        statussen = ", ".join(sorted({r["status"] for r in groep}))
        uit.append(Bevinding(
            subject=f"content_job_dup:{site_id}:{squash(titel)}",
            detail=(f"{len(groep)}x '{titel}' tegelijk in de content-pijplijn "
                    f"(statussen: {statussen}) — een herschrijf-mechanisme sluit het "
                    "bronrecord niet af en dupliceert in plaats van te herstellen."),
            project=project,
        ))
    return uit


def _check_orchestrator_teller_teruggezet() -> List[Bevinding]:
    """De pogingenteller van een bronrecord vergeleken met de échte Gauntlet-
    historie — twee werelden, niet twee velden.

    Incident 15 aug 2026: `scripts/bijeen_worldclass_engine.py` POST'te
    rechtstreeks naar `/api/gauntlet` (dus buiten `process_one_under_threshold`
    om) en schreef na elke escalatie `orchestrator_attempts=1, status='stuck'`
    terug op het bronrecord. Daarmee zette het precies de twee velden terug
    waarop de cross-run cap besluit: na elke ronde stond het artikel er weer
    bij als "nog maar één keer geprobeerd, nog steeds onder de grens". Eén
    WeAreImpact-artikel is zo 17x herschreven en er kwamen 128 bijna-identieke
    duplicaten in de Wachtrij te staan; 6,2M tokens op één dag, waarmee het
    dagbudget brak en álle andere autonome runs stil kwamen te liggen.

    `content_job_meervoudig_herschreven` zag het gevólg (de duplicaten in de
    Wachtrij) en deed dat correct. Deze toets zoekt de óórzaak, en dat is een
    ander soort vraag: klopt wat het systeem over zijn eigen pogingen bijhoudt
    nog met wat er werkelijk gedraaid heeft? Zolang de teller liegt, is elke
    rem die erop rust een decoratie. Precies daarom telt hij niet de rijen in
    `content_jobs` maar de runs in `gauntlet_runs`: een tweede administratie
    bevestigt de eerste alleen als hij onafhankelijk is.

    De koppeling loopt via de artikeltitel in de objective-tekst — de Gauntlet
    kent geen bron-job-id. Dat is losjes, dus de drempel ligt bewust hoog
    (≥3 runs) en de bevinding valt weg zodra de teller ze allemaal kent.

    Eén ding moet er nog uit vóórdat er geteld wordt: de Gauntlet staget zijn
    uitvoer als een nieuwe job met de titel *"Herschrijf het artikel 'X'"*, en
    die kan later zélf weer bron worden. Een substring-telling ziet dan drie
    titels waar één artikel staat, en meldt 3x, 22x én 25x voor hetzelfde stuk.
    `_kern_titel` pelt die omhulsels af zodat de bevinding telt wat er
    werkelijk is: één artikel, één keer.
    """
    with get_conn() as conn:
        bronnen = conn.execute(
            "SELECT id, site_id, title, status, seo_score, "
            "       COALESCE(orchestrator_attempts, 0) AS pogingen "
            "FROM content_jobs "
            "WHERE status IN ('stuck', 'rejected') "
            "  AND COALESCE(title, '') <> '' "
            "  AND created_at >= datetime('now', '-30 day')"
        ).fetchall()
        runs = conn.execute(
            "SELECT objective FROM gauntlet_runs "
            "WHERE created_at >= datetime('now', '-30 day')"
        ).fetchall()

    objectives = [_kern_titel(r["objective"] or "") for r in runs]
    # Per artikel de zwaarste bron bewaren, niet per rij: de keten
    # bron → herschrijving → bron levert anders drie meldingen voor één stuk.
    per_artikel: Dict[Tuple[str, str], Tuple[int, int, sqlite3.Row]] = {}
    for bron in bronnen:
        kern = _kern_titel(bron["title"] or "")
        # Korte titels matchen te makkelijk op een ander stuk; die overslaan is
        # hier de veilige kant — vals alarm op een teller ondermijnt precies het
        # vertrouwen dat deze toets moet opleveren.
        if len(kern) < 25:
            continue
        gedraaid = sum(1 for o in objectives if kern in o)
        if gedraaid < 3:
            continue
        pogingen = int(bron["pogingen"] or 0)
        # Eén run mag ontbreken: een ronde die nu loopt heeft de teller al
        # opgehoogd vóór de run bestaat, en andersom.
        if pogingen >= gedraaid - 1:
            continue
        sleutel = (bron["site_id"] or "", kern)
        vorige = per_artikel.get(sleutel)
        if vorige is None or gedraaid > vorige[0]:
            per_artikel[sleutel] = (gedraaid, pogingen, bron)

    uit: List[Bevinding] = []
    for (site_id, kern), (gedraaid, pogingen, bron) in per_artikel.items():
        # Twee verschillende storingen met dezelfde meting, en ze verdienen
        # niet dezelfde zin. Staat de teller op 0 terwijl er tig runs zijn, dan
        # is er nooit gételd — het stuk liep langs een pad dat de cap helemaal
        # niet kent (Agent Control, een script). Staat hij op 1 of 2, dan is er
        # wél geteld en heeft iets de stand daarna teruggezet. De eerste vraagt
        # om een pad dat gaat tellen, de tweede om een schrijver die stopt.
        if pogingen == 0:
            oorzaak = ("het bronrecord heeft nooit één poging geteld — dit stuk is "
                       "de Gauntlet in gegaan langs een pad dat de cross-run cap "
                       "(ORCHESTRATOR_MAX_ATTEMPTS) helemaal niet kent")
        else:
            oorzaak = (f"het bronrecord staat op {pogingen} poging(en) — iets zet de "
                       "teller terug, waardoor de cross-run cap "
                       "(ORCHESTRATOR_MAX_ATTEMPTS) nooit aanslaat")
        uit.append(Bevinding(
            subject=f"orchestrator_teller:{site_id}:{kern[:80]}",
            detail=(f"'{kern[:90]}' is {gedraaid}x door de Gauntlet gehaald, maar "
                    f"{oorzaak} (status '{bron['status']}'). Zo blijft hetzelfde stuk "
                    "herschreven worden zolang het onder de grens staat."),
            project=_project_van_site(site_id),
        ))
    # Tweede detectieweg, dezelfde vraag (15 aug 2026). De telling hierboven
    # koppelt via de artikeltitel in de objective-tekst en heeft daarom een hoge
    # drempel (≥3 runs) — losse koppeling, dus voorzichtig. De supersede-keten
    # is een échte verwijzing (`superseded_by`), dus daar kan het exact: telt de
    # opvolger minder pogingen dan zijn bron, dan is de teller onderweg verloren
    # en begint de cap bij elke generatie opnieuw. Dat was de tweede oorzaak van
    # dezelfde storm — `mark_superseded` gaf de telling niet door — en hij is
    # zichtbaar vanaf de eerste keer, niet pas na drie runs.
    uit.extend(_check_herschrijfteller_gereset())
    return uit


# De Gauntlet staget zijn uitvoer als een nieuwe job met de titel
# "Herschrijf het artikel 'X' (project Y) naar …". Wordt die later zélf bron,
# dan ontstaat "Herschrijf het artikel 'Herschrijf het artikel 'X''". Zonder
# afpellen telt een substring-toets dezelfde X drie keer als drie artikelen.
# Bewust op de aanhalingstekens en niet op de woorden die erop volgen ('tot
# wereldklasse…', '(project X) naar…'). Een non-greedy match tot 'tot' of
# 'naar' knipt namelijk in de titel zélf: 'Van plan tot nazorg: een geslaagd
# evenement' werd 'Van plan', en twee ongelijke artikelen belandden daarmee in
# één groep — bij het opruimen van duplicaten is dat het verschil tussen een
# overbodige versie sluiten en een uniek artikel weggooien.
# Greedy tot het láátste aanhalingsteken, want titels bevatten zelf apostrofs
# ("de 3 zwakst scorende pagina's van X"); non-greedy breekt precies daarop.
_OMHULSEL = re.compile(
    r"^\s*herschrijf\s+(?:het\s+)?artikel\s*['\"‘“](.+)['\"’”]",
    re.IGNORECASE | re.DOTALL,
)


def _kern_titel(tekst: str) -> str:
    """Pel de 'Herschrijf het artikel …'-omhulsels af tot de kale artikeltitel.

    Meerdere rondes, want de omhulsels stapelen. Levert de tekst ongewijzigd
    terug zodra er niets meer af kan — een objective die geen herschrijfopdracht
    is (een [SEO Copywriter]-taak bijvoorbeeld) hoort onaangeroerd te blijven.
    """
    huidig = (tekst or "").strip()
    for _ in range(5):
        m = _OMHULSEL.match(huidig)
        if not m:
            break
        kern = m.group(1).strip().strip("'\"‘’“”").strip()
        if not kern or kern == huidig:
            break
        huidig = kern
    return huidig


def _check_radar_signaal_verlopen() -> List[Bevinding]:
    with get_conn() as conn:
        rij = conn.execute(
            "SELECT COUNT(*) AS n FROM radar_signals "
            "WHERE status = 'new' AND created_at < datetime('now', '-21 day')"
        ).fetchone()
    n = rij["n"] or 0
    if n < 50:
        return []
    return [Bevinding(
        subject="radar_signals:verlopen",
        detail=(f"{n} radarsignalen staan langer dan 21 dagen op 'new' — een trend van "
                f"drie weken oud valt niemand meer aan; ze verdringen alleen de verse"),
        project="Systeem",
    )]


def _check_besluit_onzichtbaar_op_eigen_dashboard() -> List[Bevinding]:
    """Een item zonder resolveerbaar project (Agenda-voorstel, Leads,
    Scheduler-fout) moet zichtbaar zijn in het WeAreImpact-Actiecentrum —
    dat is Vincents eigen dashboard, niet een klantproject.

    Incident 23 aug 2026: een WhatsApp-afspraakvoorstel (Steentjebij
    Steentje, 24 aug 14:00) stond keurig in de globale Control Room-inbox
    maar toonde 'Wacht op jou (0)' op het WeAreImpact-dashboard zelf, waar
    Vincent hem juist verwachtte te kunnen afhandelen — `_item_belongs_to_
    project` gaf zulke items nooit mee aan ÉÉN per-project view, WeAreImpact
    incluis. De Agenda-tab kende deze uitzondering al in de frontend
    (zichtbaar op WeAreImpact, verborgen op klantprojecten); de backend-
    filter volgde die regel niet. Vergelijkt de globale inbox met de
    WeAreImpact-scoped inbox in plaats van een vaste lijst item-kinds te
    verwachten — zo vangt hij elke toekomstige bron van 'geen project',
    niet alleen Agenda.
    """
    from ..action_center.service import build_inbox
    global_items = build_inbox().get("items", [])
    wai_keys = {
        f"{i.get('dismiss_kind')}:{i.get('id')}"
        for i in build_inbox(project="WeAreImpact").get("items", [])
    }
    uit: List[Bevinding] = []
    for it in global_items:
        if it.get("project") not in (None, "Agenda", "Leads", "Scheduler", "Systeem", "Bridge", "Postvak", "Linkbuilding"):
            continue  # heeft een eigen (klant)project — hoort daar, niet bij WeAreImpact
        key = f"{it.get('dismiss_kind')}:{it.get('id')}"
        if key not in wai_keys:
            uit.append(Bevinding(
                subject=f"besluit:{key}",
                detail=(f"'{(it.get('title') or '')[:60]}' (project '{it.get('project')}') staat in de "
                        f"globale inbox maar niet in het WeAreImpact-Actiecentrum — onzichtbaar op "
                        f"Vincents eigen dashboard."),
                project="WeAreImpact",
            ))
    return uit


def _check_impact_lead_niet_vastgelegd() -> List[Bevinding]:
    """Een Impact Calculator-lead die het logboek 'vastgelegd' noemt, moet ook
    echt in de Leads-tab staan.

    Incident 22 aug 2026: bij een uitgeputte OpenModel-quota brak de toenmalige
    `_process_one` af vóórdat `capture_impact_calculator_lead` werd aangeroepen
    — de uitkomstkaart zei letterlijk "staat als 'Geverifieerd' in de
    Leads-tab", maar er kwam nooit een rij bij. Twee échte inbound-leads (o.a.
    Impact Box: 185 FTE, EUR 1.440.691 berekende besparing/jaar) verdwenen zo
    spoorloos — alleen de permanente rij in Neons `impact_leads`-tabel bewees
    dat ze ooit waren binnengekomen. De fix (capture altijd vóór het
    LLM-verslag, nooit erna) stond al een tijd op schijf maar was niet live
    omdat de server sinds 20 aug niet herstart was — code op schijf is geen
    garantie, alleen een herstarte server telt.

    Matcht op de vrije tekst in `detail` (geen aparte kolom met het e-mailadres
    beschikbaar) — dezelfde reden waarom deze toets nooit met een LLM werkt:
    een deterministische regex over eigen loggegevens hoort nooit te kunnen
    liegen over wat hij zelf heeft weggeschreven.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, detail, created_at FROM activity_log "
            "WHERE action IN ('impact_lead_verslag_mislukt', "
            "'impact_lead_verslag_verstuurd', 'impact_lead_niet_vastgelegd') "
            "ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        leads = conn.execute(
            "SELECT org_name, email FROM leads WHERE lead_type='impact_calculator'"
        ).fetchall()
    bekend = set()
    for l in leads:
        if l["org_name"]:
            bekend.add(l["org_name"].strip().lower())
        if l["email"]:
            bekend.add(l["email"].strip().lower())

    uit: List[Bevinding] = []
    gezien = set()
    for r in rows:
        m = re.search(r"Impact Calculator-lead van (.+?)(?: is gemaild| \(| staat )",
                      r["detail"] or "")
        if not m:
            continue
        naam_of_org = m.group(1).strip().lower()
        if naam_of_org in gezien:
            continue
        gezien.add(naam_of_org)
        if naam_of_org not in bekend:
            uit.append(Bevinding(
                subject=f"impact_lead:{naam_of_org}",
                detail=(f"'{naam_of_org}' werd op {r['created_at']} in het logboek als "
                        f"Impact Calculator-lead gemeld, maar staat nergens in de "
                        f"Leads-tab — de lead is vermoedelijk kwijt."),
                project="WeAreImpact",
            ))
    return uit


def _check_lead_geen_organisatie() -> List[Bevinding]:
    """Rommel in de voorraad maakt de acquisitieformule onmeetbaar.

    Deze invariant draait dezelfde zeef als `prospecting/validate.py` over de
    leads die er al ín staan. Zo bewijst hij tegelijk dat de zeef aan de
    voorkant werkt: nieuwe rommel hoort hier niet meer bij te komen.
    """
    from ..prospecting import validate
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, org_name, website FROM leads "
            "WHERE status IN ('new', 'enriched', 'valid', 'outreach_review')"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        geschikt, reden = validate.looks_like_organisation(
            r["org_name"] or "", r["website"] or "", "")
        if not geschikt:
            uit.append(Bevinding(
                subject=f"lead:{r['id']}",
                detail=(f"'{(r['org_name'] or '')[:55]}' staat in de actieve voorraad maar "
                        f"is geen organisatie ({reden}) — het vertekent de conversieratio's"),
                project="Acquisitie",
            ))
    return uit


def _check_bulk_in_behandeling() -> List[Bevinding]:
    """Nieuwsbrieven die tóch als vraag of afspraak zijn geclassificeerd.

    De gate uit 1 aug 2026 hoort dit onmogelijk te maken. Deze invariant is het
    bewijs dat hij het ook echt doet — en de alarmbel als er een route omheen
    ontstaat (zoals de Graph-flow die de headers niet opvroeg).

    Bewust alleen berichten waar op dít moment iets voor klaarstaat: een concept
    of een afspraakvoorstel dat op goedkeuring wacht. Een nieuwsbrief die drie
    weken geleden verkeerd is geclassificeerd en waarvan het concept allang is
    afgewezen, is geschiedenis — die opdreunen maakt van de audit een archief in
    plaats van een alarm. Er staan er 47 van in de database (2 aug 2026); geen
    ervan vraagt nog iets van een mens.
    """
    from ..mail import bulk
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT i.id, i.from_addr, i.subject, i.body_text, i.classified "
            "FROM mail_inbox i "
            "WHERE i.classified IN ('question', 'appointment') AND ("
            "  EXISTS (SELECT 1 FROM mail_reply r "
            "          WHERE r.inbox_id = i.id AND r.status = 'pending_review') "
            "  OR EXISTS (SELECT 1 FROM calendar_proposals p "
            "             WHERE p.inbox_id = i.id AND p.status = 'pending_review'))"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        reden = bulk.bulk_reason(None, r["from_addr"] or "", r["subject"] or "",
                                 r["body_text"] or "")
        if reden:
            uit.append(Bevinding(
                subject=f"mail:{r['id']}",
                detail=(f"er wacht iets op goedkeuring voor '{(r['subject'] or '')[:45]}' "
                        f"van {(r['from_addr'] or '')[:35]}, maar dat is bulk ({reden}) — "
                        f"daar hoort niets voor klaar te staan"),
                project="Mail",
            ))
    return uit


def _check_outreach_voorraad_onbenut() -> List[Bevinding]:
    """Er staan mailbare leads klaar, maar er gaat niets de review in.

    2 aug 2026: de outreach-batch meldde "geen bruikbare leads — funnel-invoer is
    op" terwijl er zeven direct mailbare leads in voorraad stonden. De oorzaak was
    een `LIMIT` vóór de zeef in `select_batch_leads`: de eerste acht rijen waren
    generieke `info@`-adressen die de zeef weigert, en met count=5 kwam er nooit
    één concept uit. Omdat alle leads dezelfde score hadden was de volgorde iedere
    dag identiek — de funnel stond weken droog met een gevulde voorraad.

    De toets kijkt naar het gat zelf, niet naar de oorzaak: mailbare voorraad ja,
    concepten in review nee, en de batch heeft recent wél gedraaid. Elke andere
    route naar dezelfde stilstand valt er dus ook onder.

    Eén bevinding, geen zeven: dit is een uitspraak over het mechanisme.
    """
    from ..prospecting.outreach import count_mailable_leads
    with get_conn() as conn:
        in_review = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE status = 'outreach_review'"
        ).fetchone()["n"] or 0
        recent_gedraaid = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_log "
            "WHERE action IN ('outreach_batch', 'iris_actie') "
            "AND detail LIKE '%utreach-batch%' "
            "AND created_at >= datetime('now', '-3 day')"
        ).fetchone()["n"] or 0
    if in_review or not recent_gedraaid:
        return []
    mailbaar = count_mailable_leads()
    if mailbaar < 1:
        return []
    return [Bevinding(
        subject="outreach:voorraad_onbenut",
        detail=(f"{mailbaar} mailbare lead(s) in voorraad, 0 concepten in review, "
                f"terwijl de outreach-batch de afgelopen dagen wél draaide — de "
                f"selectie levert niets op wat de voorraad wel toestaat"),
        project="Acquisitie",
    )]


def _check_publicatiefout_zonder_kaart() -> List[Bevinding]:
    """Een artikel dat de gate passeerde en tóch niet live staat, zonder alarm.

    24 jul – 2 aug 2026: `publicatie_mislukt` werd met de standaard status 'ok'
    gelogd. De uitkomstkaart bestond, maar een 'ok'-kaart is een logregel en geen
    inbox-item, dus Ictusgo's 404 kwam drie ochtenden terug als 'les' in Iris'
    briefing zonder één keer als beslissing op het scherm te staan.

    Deze toets kijkt naar de jobs, niet naar de logregels: staat er een job op
    `publish_failed` zonder openstaande error-kaart, dan wacht er goedgekeurd werk
    dat niemand ziet — ongeacht via welke route het daar terechtkwam.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT j.id, j.site_id, j.title, j.error FROM content_jobs j "
            "WHERE j.status = 'publish_failed' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM activity_log a WHERE a.status = 'error' "
            "  AND a.detail LIKE '%' || j.title || '%')"
        ).fetchall()
    return [Bevinding(
        subject=f"job:{r['id']}",
        detail=(f"'{(r['title'] or '')[:55]}' is goedgekeurd maar niet gepubliceerd "
                f"({(r['error'] or 'reden onbekend')[:50]}) zonder dat er een "
                f"fout-kaart voor openstaat — niemand krijgt dit te zien"),
        project=_project_van_site(r["site_id"]),
    ) for r in rijen]


_LINKEDIN_ONGEPLAATST_UUR = 12


def _check_linkedin_antwoord_niet_geplaatst() -> List[Bevinding]:
    """Een goedgekeurd LinkedIn-antwoord dat lang op 'approved' blijft staan.

    LinkedIn heeft geen partner-API voor DM's/reacties; een goedkeuring in de
    Social-tab of het Actiecentrum zet zo'n bericht daarom niet meteen op
    'sent' maar op 'approved' — de browserautomatisering (via /loop, geen
    achtergrond-scheduler, want er moet een ingelogde Chrome-sessie open
    staan) plaatst 'm daarna echt en bevestigt dat via /msg/{id}/mark-sent
    (20 aug 2026, zie CLAUDE.md punt over social_inbox_msg-status). Blijft een
    bericht lang op 'approved' staan, dan draait die automatisering niet — een
    Vincent die dacht dat zijn antwoord de deur uit was, terwijl het alleen in
    de wachtrij stond, is precies de 'activiteit is geen effect'-fout die dit
    bestand bestrijdt.
    """
    grens = (datetime.now() - timedelta(hours=_LINKEDIN_ONGEPLAATST_UUR)).isoformat()
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT m.id, m.author_name, m.author_handle, m.created_at, i.project "
            "FROM social_inbox_msg m JOIN social_inboxes i ON i.id=m.inbox_id "
            "WHERE m.status='approved' AND m.manual=1 AND m.created_at < ?",
            (grens,),
        ).fetchall()
    return [Bevinding(
        subject=f"social_msg:{r['id']}",
        detail=(f"Antwoord aan {r['author_name'] or r['author_handle'] or 'iemand'} "
                f"op LinkedIn staat al {_dagen_sinds(r['created_at'])} dag(en) "
                f"goedgekeurd maar niet geplaatst — de browserautomatisering "
                f"heeft het niet opgepakt"),
        project=r["project"] or "",
    ) for r in rijen]


_BLIJFT_LIGGEN_DAGEN = 14


def _check_bevinding_blijft_liggen() -> List[Bevinding]:
    """Een blokkerende bevinding die al weken openstaat.

    De audit over zichzelf. Aanleiding (4 aug 2026): een meting over alle
    projecten telde 82 openstaande bevindingen, waarvan 54 blokkerend of stil —
    sommige al twee weken oud — terwijl `grep` over de codebase **nul**
    reparatiepaden opleverde. Elke invariant die erbij kwam produceerde een
    kaart en verder niets; zelfherstel raakt ze niet aan omdat `waarheidsaudit`
    in `_MENSELIJK_BESLUIT` staat, en dat is terecht zolang er geen remedie ís.

    Het gevolg is de faalmodus die dit hele bestand bestrijdt, één verdieping
    hoger: het systeem meldde trouw wat er stuk was, en dat melden veranderde
    niets. 'Blokkerend' betekent per definitie dat er nú iets verkeerds naar
    buiten staat; blijft zo'n bevinding twee weken staan, dan is óf de remedie
    er niet, óf hij werkt niet, óf niemand ziet de kaart. Alle drie zijn een
    storing in de keten — niet in de site.

    Bewust `stil` en niet `blokkerend`: de onderliggende bevindingen schreeuwen
    al, en een rode kaart bovenop een rode kaart is precies de dubbele melding
    die `stilstand_dubbel_gemeld` verbiedt. Eén bevinding per invariant, niet
    per geval.
    """
    grens = (datetime.now() - timedelta(days=_BLIJFT_LIGGEN_DAGEN)).isoformat()
    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT invariant, COUNT(*) AS n, MIN(first_seen) AS oudste "
            "FROM integrity_findings WHERE resolved_at IS NULL AND severity = ? "
            "AND first_seen < ? GROUP BY invariant",
            (BLOKKEREND, grens),
        ).fetchall()
    for r in rijen:
        dagen = _dagen_sinds(r["oudste"])
        uit.append(Bevinding(
            subject=f"blijft-liggen:{r['invariant']}",
            detail=(f"{r['n']} blokkerende bevinding(en) van '{r['invariant']}' staan al "
                    f"{dagen} dagen open. Blokkerend betekent dat er nú iets verkeerds naar "
                    "buiten staat — twee weken later is dat geen bevinding meer maar een "
                    "toestand. Óf er is geen remedie, óf de remedie werkt niet, óf niemand "
                    "ziet de kaart."),
            project="Waarheidsaudit",
        ))
    return uit


def _dagen_sinds(tijdstip: str) -> int:
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(tijdstip)).days)
    except (TypeError, ValueError):
        return 0


def _check_indexnow_keyfile() -> List[Bevinding]:
    """Staat het IndexNow-keybestand écht op de site-root?

    Zonder dat bestand negeren Bing, Yandex, Seznam en Naver élke aanmelding —
    stilletjes, met een HTTP 403 die alleen in `publish_result` belandde. Dit is
    dezelfde soort toets als `afgewezen_maar_live`: hij vergelijkt niet twee
    velden maar twee werelden. De sleutel staat in `sites.indexnow_key`; of hij
    ook bereikbaar is, weet alleen het web.

    En hier geldt de SPA-les dubbel: twee sites gaven op het keybestand netjes
    HTTP 200 terug met hun eigen HTML-schil erin. Wie op de statuscode toetst,
    noemt die twee gezond. De inhoud is de toets — het bestand hoort exact de
    key te bevatten en niets anders.
    """
    import httpx

    uit: List[Bevinding] = []
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, name, base_url, indexnow_key FROM sites "
            "WHERE COALESCE(indexnow_key, '') != '' AND COALESCE(base_url, '') != ''"
        ).fetchall()
    for r in rijen:
        key = (r["indexnow_key"] or "").strip()
        key_url = f"{(r['base_url'] or '').rstrip('/')}/{key}.txt"
        try:
            headers = {"User-Agent": "ImpactOS-waarheidsaudit"}
            with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
                resp = client.get(key_url)
        except Exception:
            # Onbereikbaar is geen vrijspraak, maar ook geen bewijs van schuld:
            # we melden niets liever dan een netwerkhik als storing te boeken.
            continue
        inhoud = (resp.text or "").strip()
        if resp.status_code == 200 and inhoud == key:
            continue
        if inhoud[:15].lower().startswith(("<!doctype", "<html")):
            reden = ("de site serveert hier zijn HTML-schil in plaats van het "
                     "keybestand (catch-all route, HTTP 200 bewijst dus niets)")
        elif resp.status_code != 200:
            reden = f"HTTP {resp.status_code}"
        else:
            reden = f"het bestand bevat iets anders dan de key ({inhoud[:40]!r})"
        uit.append(Bevinding(
            subject=f"indexnow:{r['id']}",
            detail=(f"IndexNow-keybestand niet bruikbaar op {key_url}: {reden}. "
                    "Bing, Yandex, Seznam en Naver negeren daardoor elke aanmelding "
                    "van nieuwe artikelen voor deze site."),
            project=r["name"] or "",
        ))
    return uit


def _check_publicatiekanaal_dood() -> List[Bevinding]:
    """Niet één artikel is stuk — het kanaal van een hele site is stuk.

    3 aug 2026: het Actiecentrum stond vol met 25 kaarten 'Publiceren mislukt',
    twaalf ervan van Ictusgo, elk met een eigen titel en een eigen knop
    'Opnieuw publiceren'. Elke kaart was op zichzelf juist: `_verify_live` had
    netjes een 404 vastgesteld. Wat er nergens stond, was de enige zin die ertoe
    deed: *op ictusgo.nl rendert geen enkel gepubliceerd artikel*. De oorzaak was
    één regel in de site (`@neondatabase/serverless` geeft een timestamp terug
    als `Date`, en `.slice(0, 10)` daarop gooit; de `catch` eromheen maakte van
    die fout een lege lijst, dus een bestaand artikel werd een 404). Alle 22
    artikelen sinds 17 juli stonden gewoon in de database van de site.

    Twaalf keer 'Opnieuw publiceren' zou twaalf keer opnieuw zijn mislukt, want
    de publicatie was nooit het probleem: de API antwoordde elke keer keurig
    `201 created`. Dat is de les die deze toets codeert — **de bevestiging van
    de ontvanger bewijst niets over wat de bezoeker ziet.**

    Vandaar de vorm: één bevinding per site, niet per artikel. Slaagt géén
    enkele recente publicatie van een site de live-controle en falen er minstens
    drie, dan is de diagnose 'de ontvangende site' en niet 'dit artikel', en
    hoort er één kaart te staan die dát zegt. Staat er wél iets live van
    dezelfde site, dan is het per-artikel-probleem het echte probleem en zwijgt
    deze toets — daar zijn `publicatiefout_zonder_kaart` en de gewone
    fout-kaarten voor.

    `ONBEKEND` telt niet mee als bewijs in beide richtingen: een site die
    tijdens de audit onbereikbaar is, mag hier geen kanaal-storing worden en mag
    er ook geen wegpoetsen.
    """
    from collections import defaultdict

    grens = (datetime.now() - timedelta(days=45)).isoformat()
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, status, publish_result FROM content_jobs "
            "WHERE status IN ('published', 'publish_failed') "
            "AND COALESCE(publish_result, '') != '' "
            # NULLIF eerst: `reviewed_at` heeft DEFAULT '' in het schema, dus
            # een kale COALESCE levert een lege string op en die is kleiner dan
            # élke datum — de hele toets zou stil niets vinden.
            "AND COALESCE(NULLIF(reviewed_at, ''), created_at) >= ? "
            "ORDER BY COALESCE(NULLIF(reviewed_at, ''), created_at) DESC",
            (grens,),
        ).fetchall()

    per_site: Dict[str, List] = defaultdict(list)
    for r in rijen:
        if _live_url(r["publish_result"] or ""):
            per_site[r["site_id"] or ""].append(r)

    uit: List[Bevinding] = []
    for site_id, jobs in per_site.items():
        dood: List[str] = []
        leeft = 0
        for r in jobs[:25]:  # recentste eerst; verder terug voegt niets toe
            status = _pagina_status(_live_url(r["publish_result"] or ""))
            if status == WEG:
                dood.append(str(r["title"] or "")[:45])
            elif status == LEEFT:
                leeft += 1
        if leeft or len(dood) < 3:
            continue
        voorbeeld = ", ".join(f"'{t}'" for t in dood[:3])
        uit.append(Bevinding(
            subject=f"site:{site_id}",
            detail=(f"{len(dood)} gepubliceerde artikelen geven allemaal een 404 en er "
                    f"staat er géén van deze site live ({voorbeeld}) — dit is geen "
                    f"artikelprobleem maar een defect in de ontvangende site; opnieuw "
                    f"publiceren lost niets op, de publicatie-API meldde elke keer succes"),
            project=_project_van_site(site_id),
        ))
    return uit


def _check_trefkans_gevleid() -> List[Bevinding]:
    """Meet de trefkans alleen de makkelijke gevallen?

    2 aug 2026: de briefing meldde 42,9% trefkans. Van de 23 afgerekende
    voorspellingen stonden er 9 op 'unclear', waarvan 5 het patroon "nauwelijks
    bewogen (0 → 0)" hadden bij een voorspelling die een expliciete drempel noemde
    ("krijgt 1 click"). Die stilstanden waren missers, maar `_judge` legde de
    ruisdrempel vóór de doeltoets en telde ze niet mee. De echte trefkans was 26%.

    Een leerlus die zijn eigen misgrepen wegstreept als 'onbeslist' leert niets en
    meldt tegelijk dat het goed gaat — de gevaarlijkste combinatie die er is.
    Daarom telt niet de trefkans zelf maar het aandeel onbesliste uitslagen: dat
    is de knop waarmee een score gevleid kan worden.
    """
    with get_conn() as conn:
        rij = conn.execute(
            "SELECT COUNT(*) AS beoordeeld, "
            "       SUM(CASE WHEN status = 'unclear' THEN 1 ELSE 0 END) AS onbeslist "
            "FROM iris_predictions WHERE status IN ('correct', 'wrong', 'unclear')"
        ).fetchone()
    beoordeeld = rij["beoordeeld"] or 0
    onbeslist = rij["onbeslist"] or 0
    # Onder de tien uitslagen zegt een aandeel niets.
    if beoordeeld < 10:
        return []
    aandeel = onbeslist / beoordeeld
    if aandeel < 0.35:
        return []
    return [Bevinding(
        subject="iris_predictions:onbeslist",
        detail=(f"{onbeslist} van {beoordeeld} afgerekende voorspellingen staat op "
                f"'unclear' ({aandeel * 100:.0f}%) — bij dit aandeel meet de "
                f"trefkans vooral welke gevallen buiten de telling vallen"),
        project="Iris",
    )]


def _check_radar_watch_dood() -> List[Bevinding]:
    """Een bron die we blijven bevragen en die nooit iets oplevert.

    3 aug 2026: twaalf van de twintig RSS-feeds in de watchlist hadden sinds hun
    aanmaak geen enkel signaal opgeleverd, en negen `site:`-watches evenmin.
    Niets meldde dat — `last_scanned_at` werd alleen bijgewerkt als er íets werd
    opgeslagen, dus een dode feed en een rustige feed zagen er identiek uit. De
    scan bleef ze elke vier uur bevragen.

    Pas ná `_MIN_SCANS` pogingen is "levert niets op" een uitspraak in plaats van
    een momentopname; een feed die vorige week is toegevoegd verdient het
    voordeel van de twijfel.
    """
    _MIN_SCANS = 5
    with get_conn() as conn:
        try:
            rijen = conn.execute(
                "SELECT project, label, type, value, scan_count FROM radar_watchlist "
                "WHERE active = 1 AND COALESCE(scan_count, 0) >= ? "
                "AND COALESCE(signal_count, 0) = 0 ORDER BY scan_count DESC",
                (_MIN_SCANS,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # radar nog nooit gebruikt op deze installatie
    return [Bevinding(
        subject=f"watch:{r['project']}:{r['value'][:60]}",
        detail=(f"{r['type']} '{r['label'][:50]}' van {r['project']} is {r['scan_count']}× "
                f"bevraagd en leverde nog nooit één signaal op — de bron is dood of "
                f"het adres klopt niet"),
        project="Mission Radar",
    ) for r in rijen]


def _check_radar_trendbrug_stil() -> List[Bevinding]:
    """De trend-brug staat stil terwijl er signalen liggen.

    3 aug 2026: de brug had sinds 27 juli geen enkele kans meer opgeleverd,
    terwijl de radar dagelijks honderden signalen binnenhaalde. Oorzaak was geen
    storing maar uitputting: het zoekwoord kwam uit de watchlist in plaats van
    uit het signaal, en na één conversie was elk watchlist-woord door de
    dedupe voor altijd verbruikt. Alle 38 kansen die de brug ooit maakte waren
    lettérlijk een watchlist-regel. Zo'n module faalt niet — hij is klaar, en
    dat is onzichtbaar.

    Deze toets meet precies dat gat: kandidaten wél, opbrengst niet.
    """
    from ..seo.trends import TREND_MIN_SIGNAL_SCORE, TREND_MIN_MATCH
    _STIL_DAGEN = 10
    with get_conn() as conn:
        try:
            kandidaten = conn.execute(
                "SELECT COUNT(*) AS n FROM radar_signals WHERE status = 'new' "
                "AND signal_score >= ? AND COALESCE(filter_reason, '') = '' "
                "AND ai_match_score >= ?",
                (TREND_MIN_SIGNAL_SCORE, TREND_MIN_MATCH),
            ).fetchone()["n"]
        except sqlite3.OperationalError:
            return []
        if kandidaten < 5:
            return []  # geen aanbod, dus ook geen verwachting
        gemaakt = conn.execute(
            "SELECT COUNT(*) AS n FROM opportunities WHERE rationale LIKE 'Radarsignaal%' "
            "AND scanned_at > datetime('now', ?)", (f"-{_STIL_DAGEN} days",),
        ).fetchone()["n"]
    if gemaakt:
        return []
    return [Bevinding(
        subject="trendbrug:stil",
        detail=(f"{kandidaten} radarsignalen halen de trend-drempel, maar de brug zette "
                f"er in {_STIL_DAGEN} dagen nul om in een content-kans — de koppeling "
                f"levert niets meer terwijl de scan gewoon doordraait"),
        project="Mission Radar",
    )]


def _check_suggestie_pijler_zonder_agent() -> List[Bevinding]:
    """Een pijler in de Iris-cijfers waar de suggestie-engine niets mee kan.

    16 aug 2026: `metrics.project_scores` kreeg een vijfde pijler `geo` in het
    `pillars`-blok, met de docstring "niet meegeteld in de totaalscore". Dat
    klopte voor de optelsom, maar iedereen die over `pillars` itereert telde hem
    wél. `agentctl/suggest.py` sorteert alle pijlers op score om de zwakste te
    vinden, en dat brak twee keer: een site zonder GEO-scan heeft `score: None`,
    dus viel de sortering om met "'<' not supported between 'NoneType' and 'int'"
    en lag de scheduler-job `iris_auto_deploy` een etmaal plat; en zodra een site
    wél een (0-100) GEO-score onder de andere pijlers (0-25) heeft, wordt `geo`
    de "zwakste", vindt hij geen agent en levert dat project stílzwijgend geen
    suggestie meer op.

    Het tweede geval is het gevaarlijke: dat gooit niets. Deze toets meet de
    voorwaarde in plaats van het gevolg — elke pijler moet óf een agent hebben,
    óf expliciet als informatief zijn aangemerkt. Een nieuwe pijler dwingt zo een
    besluit af in plaats van geruisloos gedrag te veranderen.
    """
    from ..agentctl.suggest import _PILLAR_AGENT, _INFORMATIEVE_PIJLERS
    from . import metrics as iris_metrics
    bekend = set(_PILLAR_AGENT) | set(_INFORMATIEVE_PIJLERS)
    try:
        scores = iris_metrics.project_scores()
    except Exception as exc:  # noqa: BLE001
        return [Bevinding(
            subject="pijlers:onleesbaar",
            detail=f"de Iris-cijfers zijn niet op te halen, dus de suggestie-engine "
                   f"kan er ook niets uit afleiden: {str(exc)[:120]}",
            project="Agent Control",
        )]
    onbekend: Dict[str, set] = {}
    for p in scores:
        for key, blok in (p.get("pillars") or {}).items():
            if key not in bekend:
                onbekend.setdefault(key, set()).add(p["project"])
            elif key in _PILLAR_AGENT and not isinstance((blok or {}).get("score"), (int, float)):
                # Een pijler mét agent maar zónder getal: de sortering kan hem
                # niet wegen, en dat is precies hoe de crash hierboven ontstond.
                onbekend.setdefault(f"{key} (score ontbreekt)", set()).add(p["project"])
    return [Bevinding(
        subject=f"pijler:{key}",
        detail=(f"pijler '{key}' staat in de Iris-cijfers van {len(projecten)} project(en) "
                f"({', '.join(sorted(projecten)[:3])}) maar heeft geen agent in "
                "_PILLAR_AGENT en staat niet in _INFORMATIEVE_PIJLERS — de "
                "suggestie-engine slaat dat project stil over"),
        project="Agent Control",
    ) for key, projecten in sorted(onbekend.items())]


def _check_kans_zonder_gemeten_vraag() -> List[Bevinding]:
    """Een site waarvan élke openstaande kans giswerk is.

    16 aug 2026, WeAreImpact: 24 openstaande kansen, allemaal met 0 impressies en
    positie 0 — geen enkele kwam uit Search Console. De Demand Engine leverde
    voor deze site niets, dus was de hele lijst gevuld door de trend-brug met
    koppen van andermans nieuwsberichten. Het dashboard bood er onder één knop 22
    tegelijk aan.

    Dat is geen storing die zich meldt: elke afzonderlijke kans zag er normaal
    uit en de wekelijkse scan rapporteerde gewoon 'ok'. De vraag die niemand
    stelde is of er onder de hele voorraad één meting zat. Alleen sites die
    genoeg voorraad hebben om iets over te beweren tellen mee — een site met
    twee kansen is niet stuk, die is jong.
    """
    _MIN_VOORRAAD = 8
    from ..seo import engine as demand_engine
    from ..seo import sites as sites_service
    out: List[Bevinding] = []
    for site in sites_service.list_sites():
        if not (site.get("gsc_property") or "").strip():
            continue  # zonder GSC-koppeling is 'geen gemeten vraag' geen oordeel
        try:
            kansen = demand_engine.list_opportunities_truth(
                site_id=site["id"], include_filtered=True)
        except Exception:  # noqa: BLE001
            continue
        open_kansen = [k for k in kansen if k.get("status") in ("new", "in_progress")]
        if len(open_kansen) < _MIN_VOORRAAD:
            continue
        gemeten = [k for k in open_kansen if k.get("demand") == "gemeten"]
        if gemeten:
            continue
        out.append(Bevinding(
            subject=f"kansen:{site['id']}",
            detail=(f"alle {len(open_kansen)} openstaande kansen zijn speculatief — "
                    "nul gemeten vraag in Search Console. De Demand Engine levert voor "
                    "deze site niets en de voorraad komt volledig uit de trend-brug; "
                    "elk artikel eruit is een gok"),
            project=site["name"],
        ))
    return out


def _check_stilstand_dubbel_gemeld() -> List[Bevinding]:
    """Eén stilstand, twee kaarten in dezelfde inbox.

    2 aug 2026: het Actiecentrum toonde 'biweekly_content' en
    'linkbuilding_weekly' allebei dubbel — één keer als 'gemiste_runs'-kaart uit
    `activity_log` (knop: "Analyseer & fix") en één keer als scheduler-gat uit
    `scheduler_gaps` (knop: "Nu alsnog draaien"). Zelfde tekst, letterlijk;
    alleen de knop die het werk terughaalt zat op de tweede.

    Twee meldwegen naar dezelfde beslissing is hoe een inbox onleesbaar wordt:
    je leert dat items dubbel staan, en dan lees je ze niet meer. De
    activity_log-weg is weggehaald (`shared/downtime.py`); deze toets bewaakt
    dat er niet ongemerkt een derde bij komt.
    """
    with get_conn() as conn:
        try:
            open_jobs = {
                r["job_id"] for r in conn.execute(
                    "SELECT DISTINCT job_id FROM scheduler_gaps "
                    "WHERE recovered_at IS NULL AND cost != ''")
            }
        except sqlite3.OperationalError:
            return []
        if not open_jobs:
            return []
        # Bewust níet op actie 'gemiste_runs' gefilterd. Die naam was de vórige
        # tweede meldweg; een toets die alleen dié naam kent, is blind voor de
        # volgende. Elke foutkaart die met '<job_id>|' begint claimt iets over
        # een geplande taak, en dat is genoeg om op te vergelijken.
        kaarten = conn.execute(
            "SELECT id, project, action, detail, created_at FROM activity_log "
            "WHERE status = 'error' AND created_at > datetime('now', '-3 day')"
        ).fetchall()
        # Dezelfde resolver als het Actiecentrum, niet een eigen oordeel: de
        # vraag is "staat dit dubbel op het scherm", en die vraag hoort maar één
        # antwoord te hebben. De rijen van vóór de fix staan nog in het logboek
        # maar bereiken de inbox niet meer — dat is historie, geen dubbeling.
        from . import metrics as _metrics
        kaarten = [k for k in kaarten if not _metrics._error_resolved(conn, dict(k))]
    uit: List[Bevinding] = []
    for k in kaarten:
        job_id = (k["detail"] or "").split("|")[0].strip()
        if job_id and job_id in open_jobs:
            uit.append(Bevinding(
                subject=f"dubbel:{job_id}",
                detail=(f"stilstand van '{job_id}' staat zowel als losse foutkaart "
                        f"('{k['action']}') in het Actiecentrum als in `scheduler_gaps` "
                        f"— twee kaarten voor één beslissing, en alleen de tweede "
                        f"heeft de inhaalknop"),
                project="Scheduler",
            ))
    return uit


def _check_agenda_horizon() -> List[Bevinding]:
    from ..calendar.agent import _MAX_HORIZON_DAGEN
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, title, proposed_start FROM calendar_proposals "
            "WHERE status = 'pending_review' AND proposed_start > "
            "      datetime('now', ?)",
            (f"+{_MAX_HORIZON_DAGEN} day",),
        ).fetchall()
    return [Bevinding(
        subject=f"voorstel:{r['id']}",
        detail=(f"afspraakvoorstel '{(r['title'] or '')[:45]}' staat op "
                f"{(r['proposed_start'] or '')[:10]} — verder weg dan "
                f"{_MAX_HORIZON_DAGEN} dagen is vrijwel zeker een misparse"),
        project="Agenda",
    ) for r in rijen]


def _check_afspraak_dubbel_geboekt() -> List[Bevinding]:
    """Twee geboekte voorstellen die hetzelfde moment innemen.

    De review-gate en de local-overlap-check in `agent.approve_proposal`
    voorkomen dit sindsdien vooraf (11 aug 2026) — dit is het vangnet ervoor
    en erachter: een handmatige boeking buiten de agent om, of een toekomstig
    pad dat de nieuwe check per ongeluk overslaat, moet hier alsnog opduiken.
    Zelfde vergelijking als de code-fix: bij een terugkerend blok telt alleen
    weekdag + tijdstip-op-de-dag, want de opgeslagen datum is die van de
    eerste week."""
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, title, proposed_start, proposed_end, recur_weekday, created_at "
            "FROM calendar_proposals WHERE status='booked' ORDER BY created_at"
        ).fetchall()
    geparsed = []
    for r in rijen:
        try:
            s = datetime.fromisoformat(r["proposed_start"])
            e = datetime.fromisoformat(r["proposed_end"])
        except (TypeError, ValueError):
            continue
        try:
            wd = int(r["recur_weekday"]) if r["recur_weekday"] is not None else -1
        except (TypeError, ValueError):
            wd = -1
        geparsed.append((r, s, e, wd if wd >= 0 else None))

    uit: List[Bevinding] = []
    gemeld: set = set()
    for i, (ra, sa, ea, wda) in enumerate(geparsed):
        for rb, sb, eb, wdb in geparsed[i + 1:]:
            if wda is not None or wdb is not None:
                eff_a = wda if wda is not None else sa.weekday()
                eff_b = wdb if wdb is not None else sb.weekday()
                if eff_a != eff_b:
                    continue
                overlapt = sa.time() < eb.time() and sb.time() < ea.time()
            else:
                overlapt = sa < eb and sb < ea
            if not overlapt:
                continue
            paar = tuple(sorted((ra["id"], rb["id"])))
            if paar in gemeld:
                continue
            gemeld.add(paar)
            uit.append(Bevinding(
                subject=f"voorstel:{paar[0]}+{paar[1]}",
                detail=(f"voorstel #{ra['id']} '{(ra['title'] or '')[:35]}' en "
                        f"#{rb['id']} '{(rb['title'] or '')[:35]}' zijn allebei "
                        f"geboekt op hetzelfde moment ({(ra['proposed_start'] or '')[:16]})"),
                project="Agenda",
            ))
    return uit


def _check_metatitel_afgekapt() -> List[Bevinding]:
    """De meta-titel die live gaat, is midden in een woord afgekapt.

    2 aug 2026: 47 van 103 artikelen droegen een titel die op exact 60 tekens
    was afgesneden — 'Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in
    de l' — waarvan er 15 al gepubliceerd waren. Google toont die titel zoals
    hij is. De harde `[:60]` stond op vier plekken tegelijk: de review-preview
    en de drie publicatieroutes. Voor slugs was exact deze les al geleerd (zie
    `slugify_title`, 25 jul 2026), maar de meta-titel had de fix nooit gekregen.

    Twee varianten tellen mee omdat ze dezelfde wortel hebben: de titel bevat
    de instructie-echo van het model ('(54 tekens)'), of een HTML-entiteit die
    in een <title> niets te zoeken heeft.

    De toets draait op wat er wéggeschreven wordt, niet op wat er in de body
    staat: dat is precies het verschil dat deze storing zo lang verborgen hield.

    En sinds 4 aug 2026 op wat er écht staat. De eerste versie reconstrueerde de
    gepubliceerde titel als `volledig[:60]` — een aanname over het verleden, niet
    een waarneming. Bij nameting van de zes WeAreImpact-bevindingen bleken er
    drie kerngezond: hun titel was korter dan 60 tekens of viel toevallig op een
    woordgrens, en de site had ze allang correct staan. Dat is exact de fout die
    `afgewezen_maar_live` twee dagen eerder maakte, in hetzelfde bestand: wie
    over de buitenwereld oordeelt, moet de buitenwereld raadplegen. Een audit
    met 50% vals-positieven leert de lezer om de audit weg te klikken.
    """
    from ..publish.content_pipeline import meta_title_for
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, site_id, title, status, blog_html, publish_result FROM content_jobs "
            "WHERE status = 'published' AND blog_html IS NOT NULL "
            "AND LENGTH(blog_html) > 80"
        ).fetchall()
    gevonden: List[Bevinding] = []
    for r in rijen:
        volledig = _volledige_titel(r["blog_html"])
        if not volledig:
            continue
        correct = meta_title_for(volledig)
        url = _live_url(r["publish_result"] or "")
        gepubliceerd = _live_metatitel(url)
        if gepubliceerd is None:
            # Onbereikbaar of geen URL: geen bewijs in beide richtingen. Niets
            # melden is hier juist — een netwerkhik is geen kapotte titel.
            continue
        kaal = _zonder_merkstaart(gepubliceerd)
        if kaal == correct or gepubliceerd == correct:
            continue
        # Alleen áfkapping telt, niet elk verschil. De titel in de body kan
        # sinds publicatie zijn herschreven — dan staat er live iets ánders,
        # niet iets kapots. Een eerdere versie meldde die gevallen wél en
        # leverde daarmee 'Checklist: 10 essentiële stappen voor een
        # programmamanager' op als storing, terwijl die titel kerngezond is.
        #
        # Twee vormen tellen, en alleen die twee:
        #
        #  (a) Afkapping — wat live staat is het begín van de volledige titel
        #      en houdt te vroeg op. Vergelijken doen we met `volledig` en niet
        #      met `correct`: de afgekapte versie is juist *langer* dan de
        #      correcte (60 harde tekens tegenover een nette woordgrens op 54),
        #      dus tegen `correct` afzetten laat precies het geval vallen waar
        #      deze invariant voor bestaat.
        #
        #  (b) Vervuiling — de titel overleeft onze eigen normalisator niet:
        #      een instructie-echo van het model ('(54 tekens)') of een
        #      HTML-entiteit die in een <title> niets te zoeken heeft ('&amp;').
        #      Geen afkapping, wel onleesbaar in de zoekresultaten.
        #
        # Alles daarbuiten is een titel die ná publicatie is herschreven: live
        # staat iets ánders, niet iets kapots.
        afgekapt = volledig.startswith(kaal) and len(kaal) < len(volledig)
        vervuild = meta_title_for(kaal) != kaal
        if not (afgekapt or vervuild):
            continue
        gevonden.append(Bevinding(
            subject=f"metatitel:{r['id']}",
            detail=(f"live <title> is {kaal[:70]!r}; "
                    f"hoort {correct[:70]!r} te zijn"),
            project=_project_van_site(r["site_id"]),
        ))
    return gevonden


def _zonder_merkstaart(titel: str) -> str:
    """De titel zonder het sjabloon dat de site er zelf achter plakt.

    Elke site doet dit anders: WeAreImpact gebruikt ' | WeAreImpact', Bijeen
    ' — Bijeen'. Dat is geen afwijking maar opmaak, en meevergelijken zou van
    élke titel een bevinding maken.
    """
    for scheider in (" | ", " — ", " – ", " · ", " - "):
        if scheider in titel:
            return titel.rsplit(scheider, 1)[0].strip()
    return titel.strip()


_METATITEL_CACHE: Dict[str, Optional[str]] = {}


def _live_metatitel(url: str) -> Optional[str]:
    """De <title> zoals Google hem ziet, of None als we het niet konden vaststellen.

    None is bewust géén lege string: "we weten het niet" en "er staat niets"
    leiden tot tegengestelde conclusies, en dat verschil wegpoetsen is hoe deze
    toets aan vals-positieven kwam.
    """
    if not url:
        return None
    if url in _METATITEL_CACHE:
        return _METATITEL_CACHE[url]
    import html as _html

    import httpx

    uitkomst: Optional[str] = None
    try:
        headers = {"User-Agent": "ImpactOS-waarheidsaudit"}
        with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.S | re.I)
            if m:
                uitkomst = _html.unescape(m.group(1)).strip()
    except Exception:  # noqa: BLE001
        uitkomst = None
    _METATITEL_CACHE[url] = uitkomst
    return uitkomst


def _volledige_titel(html_body: str) -> str:
    """De titel vóór elke inkorting — de maatstaf waartegen we de live waarde leggen."""
    from ..publish.content_pipeline import (_strip_meta_and_suggestions,
                                            _extract_title)
    try:
        cleaned, meta_title, _ = _strip_meta_and_suggestions(html_body or "")
        return (meta_title or _extract_title(cleaned, fallback="")).strip()
    except Exception:  # noqa: BLE001 — een audit struikelt niet over één body
        return ""


def _woorden(tekst: str) -> set:
    """Inhoudswoorden uit een tekst — kleingeletterd, zonder stopwoorden."""
    from ..seo.opportunity_quality import _STOPWORDS
    import re as _re
    return {w for w in _re.findall(r"[a-zà-ÿ]{4,}", (tekst or "").lower())
            if w not in _STOPWORDS}


def _check_content_hoort_bij_andere_site() -> List[Bevinding]:
    """Een stuk in de Wachtrij gaat aantoonbaar over een ánder project.

    15 aug 2026: `publish_to_weareimpact` viel bij een onherleidbaar project
    stil terug op WeAreImpact — de eerste site die er ooit was. Gemeten stonden
    er 25 stukken in die Wachtrij die over Bijeen, Pootgelukkig, Liefde voor
    Iedereen of TeambuildingMetImpact gingen ('De 10 beste cadeaus voor
    koppels', 'Advies hond adopteren in Antwerpen', 'WMO-rapportage evenement
    software: zo meet je impact met Bijeen'), en twee ervan zijn écht live
    gegaan op weareimpact.nl.

    De code-terugval is geschrapt, maar dit is de toets eronder: elke ándere
    weg naar een verkeerde site valt er ook onder — een handmatige klik met het
    verkeerde project, een goal die bij de verkeerde site staat, een import.

    Waarom dit deterministisch kan: elke site draagt zijn eigen woordenschat
    (profiel + de koppen die er al live staan). We vergelijken niet met een
    absolute drempel — "past dit bij de site" is een smaakvraag — maar tússen
    sites, en dat is een feitelijke: hoort dit stuk méétbaar beter bij een
    ander project dan bij het zijne? Alleen bij een duidelijke winnaar (≥2
    woorden overlap én minstens 2 méér dan de eigen site) slaat hij aan, want
    een nieuw onderwerp op de eigen site moet gewoon mogen.
    """
    from ..seo import sites as sites_service

    with get_conn() as conn:
        sites = conn.execute(
            "SELECT id, name FROM sites WHERE COALESCE(is_test, 0) = 0"
        ).fetchall()
        open_jobs = conn.execute(
            "SELECT id, site_id, title FROM content_jobs "
            "WHERE status IN ('pending_review', 'needs_work') "
            "AND COALESCE(title, '') <> ''"
        ).fetchall()

    naam = {s["id"]: s["name"] for s in sites}

    # Zelfde toets als de publiceer-gate (`approve_and_publish`) — één antwoord
    # op "hoort dit bij deze site?", anders lopen audit en gate uiteen zoals
    # `is_same_topic` dat elders al moest voorkomen.
    uit: List[Bevinding] = []
    for j in open_jobs:
        eigen = j["site_id"]
        if eigen not in naam:
            continue
        beter = sites_service.better_matching_site(j["title"], eigen)
        if beter:
            uit.append(Bevinding(
                subject=f"job:{j['id']}",
                detail=(f"'{(j['title'] or '')[:60]}' staat in de Wachtrij van "
                        f"{naam.get(eigen, eigen)}, maar hoort qua onderwerp bij "
                        f"{beter['name']} — publiceren zet het op de verkeerde site"),
                project=naam.get(eigen, str(eigen)),
            ))
    return uit


def _check_herschrijfteller_gereset() -> List[Bevinding]:
    """Tweede detectieweg voor `orchestrator_teller_teruggezet` — niet zelf een
    invariant, want het is dezelfde vraag: is de cap-teller te vertrouwen?

    Een opvolger in een supersede-keten telt minder pogingen dan zijn bron.

    De cross-run cap (`ORCHESTRATOR_MAX_ATTEMPTS`) is de enige rem op de
    herschrijflus, en hij telt op `content_jobs.orchestrator_attempts`. Zodra
    een schakel die teller niet doorgeeft, begint elke generatie weer op nul en
    bijt de cap per constructie nooit — zonder dat er ooit iets faalt.

    Dat is twee keer gebeurd, langs twee verschillende wegen. 14-15 aug 2026
    schreef `scripts/bijeen_worldclass_engine.py` na elke ronde letterlijk
    `orchestrator_attempts=1, status='stuck'` terug, waarmee het bronrecord na
    elke run exact in de begintoestand stond. En `mark_superseded` gaf de
    telling niet door aan de nieuwe job. Gemeten: 244 WeAreImpact-jobs allemaal
    op 0, terwijl één artikel zestien herschrijvingen had en er 128 duplicaten
    in de Wachtrij stonden — 20,5 miljoen tokens in drie dagen.

    Deze toets kijkt naar de keten zelf en niet naar één mechanisme, want beide
    incidenten kwamen van een andere kant. Elke toekomstige schakel die de
    teller verliest valt er ook onder.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT bron.id AS bron_id, bron.orchestrator_attempts AS bron_n, "
            "       bron.site_id AS site_id, bron.title AS titel, "
            "       opv.id AS opv_id, opv.orchestrator_attempts AS opv_n "
            "FROM content_jobs bron "
            "JOIN content_jobs opv ON opv.id = bron.superseded_by "
            "WHERE COALESCE(bron.superseded_by, '') <> '' "
            "  AND COALESCE(opv.orchestrator_attempts, 0) "
            "      < COALESCE(bron.orchestrator_attempts, 0) "
            "  AND bron.created_at >= datetime('now', '-14 day')"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        uit.append(Bevinding(
            subject=f"job:{r['opv_id']}",
            detail=(f"'{(r['titel'] or '')[:60]}' is herschreven, maar de opvolger telt "
                    f"{r['opv_n'] or 0} poging(en) tegen {r['bron_n'] or 0} op de bron — "
                    f"de cross-run cap begint bij deze generatie opnieuw en stopt de "
                    f"herschrijflus dus niet"),
            project=str(r["site_id"] or ""),
        ))
    return uit


def _check_kwaliteitsscore_is_stopregel() -> List[Bevinding]:
    """De kwaliteitsscores klonteren op één waarde vlak boven de gate.

    2 aug 2026: van 76 artikelen in de Wachtrij stonden er 39 op exact 82 en
    4 op exact 80 — meer dan de helft van de voorraad op twee getallen. Dat is
    geen verdeling van artikelkwaliteit; dat is de vingerafdruk van een lus die
    stopt zodra hij één keer boven de grens meet. De reviewer varieert 65-92 op
    identieke invoer (CLAUDE.md punt 6), dus "de eerste meting die de gate
    haalt" selecteert op mázzel: het opgeslagen cijfer zegt wanneer de lus stopte,
    niet hoe goed het stuk is. Vervolgens publiceert `approve_and_publish` op
    precies dat cijfer.

    Niets hieraan gooit ooit een fout. Het is de zuiverste vorm van wat deze
    audit zoekt: een getal dat overtuigend genoeg oogt om nooit gecontroleerd te
    worden. De toets meet de klontering zelf en niet de oorzaak — elke andere
    route naar een score die een stopmoment codeert valt er ook onder.

    `stil` en niet `blokkerend`: er staat niets verkeerds naar buiten. Wat er
    stukis, is dat het cijfer waarop besloten wordt geen meting is.
    """
    from ...shared.config import CONTENT_MIN_SCORE
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT seo_score AS score, COUNT(*) AS n FROM content_jobs "
            "WHERE status = 'pending_review' AND seo_score IS NOT NULL "
            "GROUP BY seo_score"
        ).fetchall()
    totaal = sum(r["n"] for r in rijen)
    # Onder de 20 artikelen is klontering ruis: drie stukken op 82 kan toeval zijn.
    if totaal < 20:
        return []
    for r in rijen:
        score = float(r["score"] or 0)
        # Alleen vlak bóven de gate is verdacht. Klontering op 95 zou betekenen
        # dat de artikelen echt goed zijn; klontering op gate+2 betekent dat de
        # lus daar afsloeg.
        if not (CONTENT_MIN_SCORE <= score <= CONTENT_MIN_SCORE + 4):
            continue
        if r["n"] / totaal < 0.4:
            continue
        return [Bevinding(
            subject="content:score_is_stopregel",
            detail=(f"{r['n']} van {totaal} artikelen in de Wachtrij staan op exact "
                    f"{score:.0f} (gate {CONTENT_MIN_SCORE}) — de score codeert het "
                    f"stopmoment van de verbeter-lus, niet de kwaliteit, terwijl "
                    f"publiceren wél op dat cijfer besluit"),
            project="Content",
        )]
    return []


# ── Beursmeester (domains/invest) ──────────────────────────────────────────
#
# Bij beleggen is "succes gemeld zonder effect" niet duur maar heel duur, en de
# vertekeningen zijn de klassiekers uit het vak: een stop die alleen op papier
# staat, een rendement zonder de verliezers erin, een besluit op een koers van
# vorige week. Ze zijn hier vanaf dag één ingebouwd, niet nadat ze een keer
# geld hebben gekost — dat is het enige domein waarin dat mocht.

def _check_stilstand_ouder_dan_de_job() -> List[Bevinding]:
    """Gemiste vuurmomenten van vóórdat de job bestond.

    Een nieuw toegevoegde JobSpec heeft de vuurmomenten van vorige week niet
    gemist — hij was er niet. Zonder ondergrens rekent de stilstand-teller het
    hele trigger-verleden aan hem toe, mét de kostenzin en de knop eronder.
    Dat is geen ruis maar een onwaarheid: het systeem beweert dat er werk
    verloren ging dat nooit heeft bestaan.
    """
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT g.job_id, g.label, g.scheduled_for, r.first_seen_at "
                "FROM scheduler_gaps g JOIN scheduler_runs r ON r.job_id = g.job_id "
                "WHERE g.recovered_at IS NULL AND r.first_seen_at IS NOT NULL "
                "AND g.scheduled_for < r.first_seen_at"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [Bevinding(
        subject=f"gap:{r['job_id']}:{r['scheduled_for'][:10]}",
        detail=(f"'{r['label']}' staat als gemist op {r['scheduled_for'][:10]}, "
                f"maar bestaat pas sinds {(r['first_seen_at'] or '')[:10]}"),
        project="Scheduler",
    ) for r in rijen]


def _check_positie_zonder_stop() -> List[Bevinding]:
    """Een open positie zonder stop is een positie zonder bodem.

    De risicomodule wéigert een voorstel zonder stop, dus dit kan alleen
    ontstaan buiten de normale route om (handmatige rij, migratie, een
    gedeeltelijke sluiting die de stop wiste). Precies daarom staat de toets er:
    de bescherming die je denkt te hebben, is de gevaarlijkste die ontbreekt.
    """
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, symbol, qty, avg_price, opened_on FROM invest_positions "
                "WHERE status = 'open' AND (stop IS NULL OR stop <= 0)"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [Bevinding(
        subject=f"positie:{r['id']}",
        detail=(f"{r['symbol']}: {r['qty']:g} stuks vanaf {r['opened_on']} "
                f"(kostprijs {r['avg_price']:g}) zonder stop"),
        project="Beursmeester",
    ) for r in rijen]


def _check_koers_verouderd() -> List[Bevinding]:
    """Voorstellen die op een koers rusten die niet meer actueel is.

    Een these van vorige week leest precies zo overtuigend als een van vandaag;
    dat is het probleem. De houdbaarheid verschilt per instrument (crypto
    handelt in het weekend, een ETF niet) en komt uit `invest/universe.py`.
    """
    try:
        from ..invest import history as invest_history
        from ..invest import service as invest_service
        open_voorstellen = invest_service.open_voorstellen()
    except Exception:
        return []
    bevindingen = []
    for v in open_voorstellen:
        if invest_history.is_verouderd(v["symbol"]):
            laatste = invest_history.laatste_slot(v["symbol"])
            bevindingen.append(Bevinding(
                subject=f"voorstel:{v['id']}",
                detail=(f"{v['side']} {v['symbol']} rust op de koers van {v['ref_date']}; "
                        f"laatst bekende handelsdag is {laatste[0] if laatste else 'geen'}"),
                project="Beursmeester",
            ))
    return bevindingen


def _check_kas_wijkt_af_van_grootboek() -> List[Bevinding]:
    """Twee werelden vergelijken, niet twee velden.

    De kaspositie hóórt exact te volgen uit het startkapitaal min alle fills en
    kosten. Wijkt hij af, dan is er cash bewogen buiten het grootboek om — een
    handmatige UPDATE, een half afgebroken order, een bug. In `paper`-stand is
    het grootboek de enige buitenwereld die er is; in `alpaca_paper` of `live`
    wordt dit de vergelijking met wat de broker zégt te hebben. Dit is dezelfde
    toets als `afgewezen_maar_live`, één laag dieper.
    """
    try:
        with get_conn() as conn:
            portefeuilles = conn.execute(
                "SELECT id, cash, start_capital FROM invest_portfolio").fetchall()
            bevindingen = []
            for pf in portefeuilles:
                rij = conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN -(qty * price) "
                    "ELSE (qty * price) END), 0) AS netto, COALESCE(SUM(fee), 0) AS kosten "
                    "FROM invest_trades WHERE portfolio_id = ?", (pf["id"],)
                ).fetchone()
                # Let op: dit klopt alleen zolang alles in de basisvaluta staat.
                # Zodra er in vreemde valuta wordt gehandeld, hoort hier de
                # omrekening bij — tot die tijd is een afwijking óók een signaal
                # dát er buiten EUR is gehandeld.
                verwacht = pf["start_capital"] + rij["netto"] - rij["kosten"]
                afwijking = abs(verwacht - pf["cash"])
                if afwijking > 1.0:
                    bevindingen.append(Bevinding(
                        subject=f"kas:{pf['id']}",
                        detail=(f"kas staat op {pf['cash']:.2f}, het grootboek zegt "
                                f"{verwacht:.2f} (verschil {afwijking:.2f})"),
                        project="Beursmeester",
                    ))
            return bevindingen
    except sqlite3.OperationalError:
        return []


def _check_belegging_niet_afgerekend() -> List[Bevinding]:
    """Beleggingsvoorspellingen waarvan de horizon verstreek zonder oordeel.

    De generieke toets hierboven kijkt naar `iris_predictions`; deze naar
    `agent_predictions`. Zonder afrekening bouwt de agent een trackrecord op
    dat alleen uit open posities bestaat — en dat is per definitie vleiend,
    want de verliezers zijn precies de posities die je zou willen sluiten.
    """
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, context, metric, due_date, statement FROM agent_predictions "
                "WHERE agent = 'invest' AND status = 'open' AND due_date < date('now', '-2 day')"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [Bevinding(
        subject=f"invest-pred:{r['id']}",
        detail=(f"voorspelling over {r['context']} liep af op {r['due_date']} "
                f"en is nooit afgerekend: '{(r['statement'] or '')[:60]}'"),
        project="Beursmeester",
    ) for r in rijen]


def _check_rendement_zonder_benchmark() -> List[Bevinding]:
    """Een rendementscijfer zonder vergelijking is geen cijfer.

    +4% klinkt goed tot je weet dat de index +9% deed. Ontbreekt de
    vastgelegde startkoers van de benchmark, dan kan het dashboard wél een
    rendement tonen en niet of het ergens goed voor was — en dan wint "we doen
    iets" van "het werkt".
    """
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, name, benchmark_symbol FROM invest_portfolio "
                "WHERE benchmark_start_price IS NULL"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [Bevinding(
        subject=f"benchmark:{r['id']}",
        detail=(f"portefeuille '{r['name']}' heeft geen vastgelegde startkoers voor "
                f"{r['benchmark_symbol']}; rendement is niet te vergelijken"),
        project="Beursmeester",
    ) for r in rijen]


def _check_datafeed_stil() -> List[Bevinding]:
    """Koershistorie die niet meer wordt bijgewerkt.

    Dit is de stille variant van een kapotte datafeed: er komt geen fout, de
    tabel is niet leeg, en elke berekening blijft antwoorden — op de cijfers van
    vorige week. Alleen de verhandelbare instrumenten tellen; een macro-reeks
    die hapert is vervelend, geen storing.
    """
    try:
        from ..invest import history as invest_history
        verouderd = invest_history.verouderde_symbolen()
    except Exception:
        return []
    return [Bevinding(
        subject=f"feed:{v['symbol']}",
        detail=(f"laatste koers is van {v['laatste_dag'] or 'nooit'}"
                + (f" ({v['dagen_oud']} dagen oud)" if v.get("dagen_oud") is not None else "")),
        project="Beursmeester",
    ) for v in verouderd]


def _check_navreeks_incompleet() -> List[Bevinding]:
    """Handelsdagen zonder NAV-punt in de koerslijn van de portefeuille.

    `portfolio.leg_nav_vast` slaat een dag met een onvolledige NAV bewust over —
    één verkeerd punt vervuilt de reeks die later het bewijsmateriaal is. Die
    keuze klopt, maar hij is stil: er komt alleen een `logger.warning`, en
    daarna rekent élke afgeleide maat (terugval, volatiliteit, rendement per
    risico) door over de overgebleven dagen. Juist de dagen die ontbreken zijn
    de dagen waarop iets niet klopte, dus valt het resultaat stelselmatig te
    gunstig uit — precies de vorm van een trackrecord dat zichzelf mooi rekent.

    Het dashboard weigert bij gaten een cijfer te tonen; deze invariant zorgt
    dat iemand ook hoort dát de reeks lek is in plaats van alleen te zien dat er
    een streepje staat.
    """
    try:
        from ..invest import analytics as invest_analytics
        lijn = invest_analytics.koerslijn()
    except Exception:
        return []
    if not lijn.get("punten") or not lijn.get("gaten"):
        return []
    dagen = lijn["gaten_dagen"] or []
    return [Bevinding(
        subject="navreeks",
        detail=(f"{lijn['gaten']} handelsdag(en) tussen {lijn['vanaf']} en {lijn['tot']} "
                f"hebben geen NAV-punt"
                + (f" (laatste: {', '.join(dagen[-3:])})" if dagen else "")),
        project="Beursmeester",
    )]


def _check_weekrapport_niet_vastgelegd() -> List[Bevinding]:
    """De weekrun slaagde, maar er staat geen weekbeeld in de database.

    Dit is de invariant-vorm van de fout die dit hele mechanisme veroorzaakte:
    het weekrapport meldde jarenlang succes terwijl de bevindingen alleen in een
    mail en een Obsidian-notitie landden. `scheduler_runs` zei 'ok', de mailbox
    zei 'ok', en toch kon geen enkele agent het rapport lezen. Slaagt de job
    zonder rij in `weekly_insights`, dan is dat opnieuw gebeurd — via een lege
    GSC-koppeling, een uitzondering in de opslag of een site zonder property.
    """
    try:
        from ..seo import gsc as gsc_api
        if not gsc_api.is_configured():
            return []   # geen koppeling = niets te beweren
    except Exception:  # noqa: BLE001
        return []
    try:
        with get_conn() as conn:
            run = conn.execute(
                "SELECT last_ok_at FROM scheduler_runs WHERE job_id = 'weekly_ga_report'"
            ).fetchone()
            if not run or not run["last_ok_at"]:
                return []   # nog nooit geslaagd: dat meldt de scheduler zelf al
            laatste = conn.execute(
                "SELECT week_label, created_at FROM weekly_insights "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        return []

    ok_dag = (run["last_ok_at"] or "")[:10]
    if not laatste:
        return [Bevinding(
            subject="weekrapport:geen-vastlegging",
            detail=f"weekrun slaagde op {ok_dag}, maar `weekly_insights` is leeg — "
                   "het rapport ging alleen naar de mail",
            project="Impact OS",
        )]
    # De vastlegging hoort niet ouder te zijn dan de laatste geslaagde run.
    if (laatste["created_at"] or "")[:10] < ok_dag:
        return [Bevinding(
            subject="weekrapport:vastlegging-verouderd",
            detail=f"weekrun slaagde op {ok_dag}, maar het laatst vastgelegde weekbeeld "
                   f"is {laatste['week_label']} van {(laatste['created_at'] or '')[:10]}",
            project="Impact OS",
        )]
    return []


def _check_weekkans_blijft_liggen() -> List[Bevinding]:
    """Dezelfde quick win staat al weken in het rapport en beweegt niet.

    Een advies dat zich woordelijk herhaalt zonder dat er iets verandert, is
    geen advies meer maar behang. Dit is de enige toets die kan aantonen dat het
    weekrapport wél gelezen wordt: verdwijnt een kans na verloop van tijd uit de
    lijst — opgepakt of bewust afgevoerd — dan doet het rapport zijn werk.
    """
    try:
        from ..analytics import insights
        blijvers = insights.stale_quick_wins()
    except Exception:  # noqa: BLE001
        return []
    return [Bevinding(
        subject=f"weekkans:{b['site_id']}:{(b['query'] or '').lower()}",
        detail=(f"'{b['query']}' staat {b['weken']} weken op rij als quick win"
                + (f" (positie {b['positie']}, onveranderd)" if b.get("positie") else "")),
        project=b.get("project") or "",
    ) for b in blijvers]


def _check_voorstel_zonder_backtest() -> List[Bevinding]:
    """Voorstellen die de gate haalden zonder bewijsstuk.

    De validatie weigert ze, dus dit hoort niet voor te kunnen komen. Staat er
    tóch een, dan is er een route langs de validatie ontstaan — en dan is de
    backtest-eis een regel in een document geworden in plaats van een toets.
    """
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, symbol, status, created_at FROM invest_proposals "
                "WHERE status IN ('pending_review', 'filled') "
                "AND (backtest_ref IS NULL OR backtest_ref = '')"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [Bevinding(
        subject=f"backtest:{r['id']}",
        detail=f"{r['symbol']} ({r['status']}, {(r['created_at'] or '')[:10]}) zonder backtest-artefact",
        project="Beursmeester",
    ) for r in rijen]


def _check_effect_meervoudig_geclaimd() -> List[Bevinding]:
    """Eén Wachtrij-job, meerdere taken die hem als eigen resultaat opvoeren.

    De spiegelbeeldige vorm van 'activiteit is geen effect': hier is er wél een
    effect, maar het wordt meervoudig opgeëist. `_stage_to_wachtrij` pakte per
    publisher-taak de nieuwste content-taak van het doel, zonder te onthouden
    wat er al gestaged was — een doel met "Publiceer artikel 1 t/m 19" leverde
    daardoor 19 taken op die stuk voor stuk "ECHTE ACTIE UITGEVOERD" meldden
    voor hetzelfde artikel. De voortgangstelling, de dashboard-demping en Iris'
    uitvoer-pijler steunen allemaal op dat aantal.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT goal_id, id, title, result FROM goal_tasks "
            "WHERE status = 'completed' AND skill = 'publisher' "
            "AND COALESCE(result, '') LIKE '%job `%'"
        ).fetchall()
    per_job: dict = {}
    for r in rijen:
        m = re.search(r"job `([^`]+)`", r["result"] or "")
        if m:
            per_job.setdefault((r["goal_id"], m.group(1)), []).append(r["title"] or "")
    uit: List[Bevinding] = []
    for (goal_id, job_id), titels in per_job.items():
        if len(titels) < 2:
            continue
        uit.append(Bevinding(
            subject=f"job:{job_id}",
            detail=(f"{len(titels)} voltooide publisher-taken claimen dezelfde "
                    f"Wachtrij-job {job_id[:8]} als eigen resultaat "
                    f"({', '.join(t[:28] for t in titels[:3])}…) — het doel telt "
                    f"{len(titels)} publicaties waar er één is"),
            project=_project_van_goal(goal_id),
        ))
    return uit


def _check_uitvoertaak_zonder_uitvoering() -> List[Bevinding]:
    """Voltooide publisher/outreach-taken zonder spoor van de échte actie.

    Deze twee skills staan in `_CONCEPT_ONLY_SKILLS`: ze vereisen een extern
    systeem dat de goal-engine niet heeft. Een publisher-taak hoort een job-id
    achter te laten, een outreach-taak een lead of een concept. Blijft er alleen
    proza over, dan is de taak 'voltooid' op een tekst óver het werk — precies
    de vorm waarin "GSC-data ophalen" als "# Instructie: GSC-data exporteren"
    binnenkwam (4 aug 2026: 127 van 1143 voltooide taken openden met plan- of
    instructietaal). Sinds die datum eindigen zulke taken op `failed`; deze
    toets bewijst dat, en vangt elke nieuwe route die de regel omzeilt.

    `outreach`-taken die via de generieke concept-route lopen (er bestaat geen
    partnership-outreach-systeem om écht in te staan, alleen de B2B-leadsfunnel)
    krijgen daar in code de `_CONCEPT_BANNER` voorgeplakt — dat is precies het
    "concept" dat deze toets zoekt. Vóór 13 aug 2026 herkende de toets alleen
    de twee bewijsvormen van andere paden (Wachtrij-job-id, `outreach_review`),
    dus vlagde hij élke correct gelabelde outreach-concepttaak als fabricatie
    — inclusief taken ná de fix van 4 aug (Bewaard voor Jou, 7 aug: 'Outreach-
    campagne voor 7 artikelen' opende netjes met "⚠️ CONCEPT — geen echte actie
    uitgevoerd" en werd toch gemeld). Dat is een andere fout dan het incident
    hierboven: die banner wordt deterministisch door code voorgeplakt, een LLM
    kan hem niet nabootsen, dus is hij net zo hard bewijs als een job-id.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT t.id, t.goal_id, t.title, t.skill, t.result "
            "FROM goal_tasks t WHERE t.status = 'completed' "
            "AND t.skill IN ('publisher', 'outreach') "
            "AND t.updated_at >= datetime('now', '-30 day')"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        resultaat = r["result"] or ""
        # Bewijs van de echte actie: een Wachtrij-job, een aantoonbaar
        # weggeschreven concept, of de deterministische concept-banner die
        # `_route_by_skill` zelf voorplakt (nooit door een LLM na te bootsen).
        # Bewust géén trefwoordenjacht op de tekst — "checklist" in een
        # artikeltitel is geen bewijs van iets (dezelfde valkuil als de eerste
        # versie van `slug_onveilig`, die de kolom las in plaats van de wereld).
        if (re.search(r"job `[^`]+`", resultaat) or "outreach_review" in resultaat
                or "CONCEPT — geen echte actie uitgevoerd" in resultaat):
            continue
        uit.append(Bevinding(
            subject=f"taak:{r['id']}",
            detail=(f"'{(r['title'] or '')[:50]}' ({r['skill']}) staat op voltooid, maar het "
                    f"resultaat bevat geen job-id of concept — er is niets aanwijsbaars "
                    f"de wereld in gegaan"),
            project=_project_van_goal(r["goal_id"]),
        ))
    return uit


def _check_zelfde_actiepunt_opnieuw() -> List[Bevinding]:
    """Hetzelfde doel keer op keer opnieuw aangemaakt.

    'Actiepunt: Verbeter de CTR van WeAreImpact…' werd tussen 15 en 17 juli 2026
    zeven keer aangemaakt en strandde zeven keer op 'partial': de dashboard-alert
    dempt bewust niet op 'partial' (het werk ís niet gedaan), dus kwam de knop
    terug, en elke klik maakte een nieuw doel in plaats van naar de vastloper te
    wijzen. Over de hele tabel: 28 Actiepunt-doelen, 14 unieke titels. Elke
    herhaling kost LLM-budget en zet een extra 'partial' in de statistiek.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT project, title, COUNT(*) AS n, "
            "       SUM(status IN ('partial', 'failed')) AS mislukt "
            "FROM goals WHERE created_at >= datetime('now', '-30 day') "
            "GROUP BY project, title HAVING n >= 3"
        ).fetchall()
    return [Bevinding(
        subject=f"doel:{(r['project'] or '')}:{(r['title'] or '')[:60]}",
        detail=(f"'{(r['title'] or '')[:55]}' is {r['n']}× aangemaakt in 30 dagen "
                f"({r['mislukt'] or 0}× gestrand) — hetzelfde werk opnieuw starten is "
                f"geen actie"),
        project=r["project"] or "",
    ) for r in rijen]


def _check_plan_dubbel_uitgevoerd() -> List[Bevinding]:
    """Dezelfde taak staat twee keer onder één doel — en is twee keer gedraaid.

    4 aug 2026: vijf doelen droegen hun volledige planning dubbel (vier fases
    twee keer, elke taak twee keer, allemaal met een eigen uitvoering erachter);
    57 taakruns zijn zo twee keer betaald. Het viel niet op omdat `task_count`
    de plánwaarde bewaart: het doel meldde "26/14" en dat las als een
    telfout in de weergave, niet als werk dat dubbel is gedaan.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT t.goal_id, g.title, g.project, COUNT(*) AS extra "
            "FROM (SELECT goal_id, title, COUNT(*) AS n FROM goal_tasks "
            "      GROUP BY goal_id, title HAVING n > 1) t "
            "JOIN goals g ON g.id = t.goal_id "
            "GROUP BY t.goal_id"
        ).fetchall()
    return [Bevinding(
        subject=f"goal:{r['goal_id']}",
        detail=(f"'{(r['title'] or '')[:50]}' draagt {r['extra']} dubbele taak/taken — "
                f"de planning is tweemaal weggeschreven en dus tweemaal uitgevoerd"),
        project=r["project"] or "",
    ) for r in rijen]


# Een taak die zó lang niet draaide terwijl de scheduler wél leeft, is stuk.
# Ruim genomen: de radarscan draait elke 4 uur, het weekrapport één keer per
# week. Zeven dagen is voor beide onmiskenbaar.
_STIL_NA_DAGEN = 7


def _check_job_stil_terwijl_de_rest_draait() -> List[Bevinding]:
    """Een scheduler-job die niet meer vuurt terwijl de rest gewoon doorloopt.

    4 aug 2026: `radar_sky_scan` (elke 4 uur) draaide voor het laatst op 24 juli,
    elf dagen eerder — terwijl bridge_sync, calendar_sync en goal_autoheal die
    ochtend nog vuurden. Geen enkele bestaande toets zag dat: `scheduler_gaps`
    meldt alleen jobs met een gevulde `gap_cost` (radar veroudert per dag en
    hoort dus níét gemeld te worden als gemiste run), en de escalatie 'nog nooit
    geslaagd' vergt een lege `last_ok_at` — die was gevuld. Precies daartussen
    valt het geval dat het meeste kost: een job die ooit werkte en stilletjes
    ophield. Deze toets vergelijkt de job met zijn buren in plaats van met een
    absolute klok, want een machine die een week uit stond is geen storing.
    """
    with get_conn() as conn:
        try:
            rijen = conn.execute(
                "SELECT job_id, last_run_at, last_ok_at FROM scheduler_runs "
                "WHERE COALESCE(last_run_at, '') != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    if len(rijen) < 3:
        return []  # verse installatie: te weinig buren om iets mee te vergelijken
    # Tijdstempels lopen door elkaar heen: sommige jobs schrijven met tijdzone,
    # oudere rijen zonder. Naïef vergelijken gooit een TypeError en dan zwijgt
    # de toets — precies de blinde toets die de audit hoort te vinden.
    def _tijd(waarde: str) -> Optional[datetime]:
        try:
            d = datetime.fromisoformat(waarde)
        except (TypeError, ValueError):
            return None
        return d.replace(tzinfo=None) if d.tzinfo else d

    momenten = [(r, _tijd(r["last_run_at"] or "")) for r in rijen]
    momenten = [(r, d) for r, d in momenten if d is not None]
    if len(momenten) < 3:
        return []
    nieuwste = max(d for _, d in momenten)
    grens = nieuwste - timedelta(days=_STIL_NA_DAGEN)
    uit: List[Bevinding] = []
    for r, gedraaid in momenten:
        if gedraaid >= grens:
            continue
        # `__baseline__` is de nulmeting van een verse installatie
        # (`scheduler._BASELINE_ID`), geen taak — die hoort per definitie oud te
        # zijn en als bevinding zou hij de kaart voor altijd openhouden.
        if r["job_id"] == "__baseline__":
            continue
        dagen = (nieuwste - gedraaid).days
        uit.append(Bevinding(
            subject=f"job:{r['job_id']}",
            detail=(f"'{r['job_id']}' draaide voor het laatst {dagen} dagen vóór de "
                    f"jongste scheduler-run ({(r['last_run_at'] or '')[:16]}) — de "
                    f"scheduler leeft, deze taak niet"),
            project="Systeem",
        ))
    return uit


def _check_doel_voltooid_zonder_taken() -> List[Bevinding]:
    """Een doel dat nooit een taak kreeg, heeft niets gedaan.

    4 aug 2026: twee doelen van Bewaard voor Jou staan op 'completed' met nul
    fases en nul taken — 'SEO-blitz: gap-keyword content + kennisbank-herstel'
    (8 jul) en 'Open het enige draft-doel van Bewaardvoorjou' (30 jun). Er is
    niets gepland en niets uitgevoerd; het doel telt niettemin mee als afgerond
    werk én dempt in die hoedanigheid de dashboard-alerts (`_goal_addresses`
    slaat juist `partial` en `failed` over, niet `completed`).

    Alleen 'completed' telt hier. Een lopend of gestrand doel zonder taken is
    werk in uitvoering of een mislukte planning — dat is iets anders dan een
    valse voltooiing, en het als hetzelfde melden maakt de kaart onleesbaar.
    """
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT id, title, project, created_at FROM goals g "
            "WHERE g.status = 'completed' "
            "AND NOT EXISTS (SELECT 1 FROM goal_tasks t WHERE t.goal_id = g.id)"
        ).fetchall()
    return [Bevinding(
        subject=f"goal:{r['id']}",
        detail=(f"'{(r['title'] or '')[:52]}' ({(r['created_at'] or '')[:10]}) staat op "
                f"voltooid zonder ook maar één taak — er is niets gepland en niets "
                f"uitgevoerd, en het dempt wél de alerts"),
        project=r["project"] or "",
    ) for r in rijen]


# ── Postvak ────────────────────────────────────────────────────────────────
#
# Vijf toetsen op één scherm, en dat is geen toeval: het Postvak was tot 11 aug
# 2026 de plek waar élke faalmodus uit dit bestand tegelijk stond. Een lijst die
# zegt "7 mails wachten op jouw antwoord" terwijl er vijf door jezelf verstuurd
# zijn, een teller die alleen kan groeien, een waarschuwing die permanent aan
# staat, en een filter zonder terugwerkende kracht. Geen ervan wierp ooit een
# fout op; alle vijf zijn gevonden doordat iemand naar zijn telefoon keek.

def _postvak_eigen_adressen() -> set:
    try:
        from ..outlook.service import own_addresses
        return own_addresses()
    except Exception:  # noqa: BLE001
        return set()


def _check_postvak_eigen_verzonden() -> List[Bevinding]:
    """Door jezelf verstuurde mail die als binnengekomen post in het postvak staat.

    `sync_inbox` haalde `/me/messages` op — de héle mailbox — en schreef élke rij
    weg met `folder='inbox'`. Vincents linkbuilding-outreach stond daardoor in
    'wacht op jouw antwoord' (5 van de 7 items), en één ervan kreeg een
    LLM-conceptantwoord op zijn eigen mail. De sync is gerepareerd; deze toets
    bewaakt dat het niet via een andere weg terugkomt.
    """
    eigen = _postvak_eigen_adressen()
    if not eigen:
        return []
    try:
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, subject, from_email, to_email, received_at FROM outlook_emails "
                "WHERE folder='inbox' ORDER BY received_at DESC LIMIT 200"
            ).fetchall()
    except Exception:
        return []
    bevindingen = []
    for r in rijen:
        afzender = (r["from_email"] or "").lower()
        ontvangers = (r["to_email"] or "").lower()
        if afzender in eigen and afzender not in ontvangers:
            bevindingen.append(Bevinding(
                subject=f"mail:{r['id']}",
                detail=(f"'{(r['subject'] or '')[:60]}' is door jou verstuurd aan "
                        f"{ontvangers[:40] or 'onbekend'} maar staat als binnengekomen mail"),
                project="Postvak",
            ))
    return bevindingen


def _check_postvak_regel_zonder_effect() -> List[Bevinding]:
    """Mail die aan een actieve afzenderregel voldoet maar er nog gewoon staat.

    Dit is de toets op de belofte die `rules.add_rule` doet: een regel werkt met
    terugwerkende kracht. Slaat hij aan, dan is óf het toepassen stukgegaan, óf
    er is een pad dat mail binnenhaalt zonder de regels te raadplegen — en dat
    tweede is precies hoe een filter stilzwijgend niets gaat doen.
    """
    try:
        from ..outlook import rules as mail_rules
        actief = [r for r in mail_rules.list_rules()
                  if r["action"] != mail_rules.ACTIE_ALTIJD_TONEN]
        if not actief:
            return []
        alle = mail_rules.list_rules()
        with get_conn() as conn:
            rijen = conn.execute(
                "SELECT id, from_email, subject FROM outlook_emails "
                "WHERE folder='inbox' AND filter_rule_id IS NULL "
                "  AND triage_label NOT IN ('spam','archief')"
            ).fetchall()
    except Exception:
        return []
    bevindingen = []
    for r in rijen:
        oordeel = mail_rules.verdict(r["from_email"], alle)
        if oordeel:
            bevindingen.append(Bevinding(
                subject=f"regel:{oordeel['rule_id']}:mail:{r['id']}",
                detail=(f"{r['from_email']} voldoet aan een actieve regel "
                        f"({oordeel['reason'][:60]}) maar staat nog in het postvak"),
                project="Postvak",
            ))
    return bevindingen


def _check_postvak_beantwoord_niet_waargenomen() -> List[Bevinding]:
    """Een postvak met verkeer waarin nog nooit een antwoord is wáárgenomen.

    `is_replied` werd tot 11 aug 2026 alleen gezet door onze eigen verstuurknop:
    alles wat Vincent in Outlook zelf beantwoordde telde nooit mee. Gevolg: de
    achterstand kon alleen groeien en er stond permanent "0% beantwoord (7d)".
    `_sync_sent_items` leest het nu uit Verzonden items; blijft dat leeg terwijl
    er wél post binnenkomt, dan is die meting stuk — en een kapotte meting die
    een 0 toont is erger dan geen meting.
    """
    try:
        sinds = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        with get_conn() as conn:
            binnen = conn.execute(
                "SELECT COUNT(*) c FROM outlook_emails "
                "WHERE folder='inbox' AND received_at >= ?", (sinds,)
            ).fetchone()["c"]
            waargenomen = conn.execute(
                "SELECT COUNT(*) c FROM outlook_emails WHERE replied_at != ''"
            ).fetchone()["c"]
    except Exception:
        return []
    if binnen < 20 or waargenomen:
        return []
    return [Bevinding(
        subject="postvak:reply-detectie",
        detail=(f"{binnen} mails binnengekomen in 14 dagen, geen enkel antwoord "
                f"waargenomen in Verzonden items"),
        project="Postvak",
    )]


def _check_postvak_sync_stil() -> List[Bevinding]:
    """Een postvak dat al uren niet is opgehaald terwijl de koppeling leeft.

    11 aug 2026: de laatste sync was van de dag ervóór. Er bestond geen
    scheduler-job voor Vincents eigen postvak — alleen de helpdesk-mailboxen
    hadden er een — dus werd er alleen opgehaald als een mens erom vroeg. Een
    postvak dat stilstaat ziet er van buiten precies zo uit als een rustige dag.
    """
    try:
        from ..outlook import service as outlook
        if not outlook.is_configured() or not outlook.is_authenticated():
            return []
        with get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(synced_at) s FROM outlook_emails"
            ).fetchone()
    except Exception:
        return []
    laatste = (row["s"] if row else "") or ""
    if not laatste:
        return [Bevinding(subject="postvak:sync", detail="nog nooit opgehaald",
                          project="Postvak")]
    try:
        dt = datetime.fromisoformat(str(laatste).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return []
    uren = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if uren < 6:
        return []
    return [Bevinding(
        subject="postvak:sync",
        detail=f"laatste ophaalronde was {int(uren)} uur geleden",
        project="Postvak",
    )]


def _check_postvak_triage_achterstand() -> List[Bevinding]:
    """Een triage-achterstand die niet meer wegloopt.

    De gele balk "106 mails nog niet getrieerd" stond er dagen: het commando
    triageerde er 15 per keer en er was geen job die de rest ooit oppakte. Een
    waarschuwing die altijd aan staat leert een mens hem te negeren — en dan
    werkt hij ook niet meer op de dag dat er écht iets aan de hand is.
    """
    try:
        from ..outlook import service as outlook
        if not outlook.is_authenticated():
            return []
        stats = outlook.get_stats()
    except Exception:
        return []
    n = int(stats.get("untriaged") or 0)
    if n < 25:
        return []
    return [Bevinding(
        subject="postvak:triage",
        detail=f"{n} mails wachten op een triage-oordeel",
        project="Postvak",
    )]


def _project_van_goal(goal_id: str) -> str:
    try:
        with get_conn() as conn:
            r = conn.execute("SELECT project FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return (r["project"] or "") if r else ""
    except Exception:
        return ""


def _check_onboarding_onvolledig_maar_actief() -> List[Bevinding]:
    """Preventieve invariant (Iris-onboarding, 11 aug 2026) — nog geen incident,
    geschreven vóórdat het kan gebeuren in plaats van erna.

    seo/engine.py:cold_start_opportunities eist al een profiel van >=40 tekens
    ('zonder profiel wordt keyword-onderzoek giswerk'); dezelfde drempel geldt
    hier. Een site die al draait (auto_content_enabled, of een gekoppeld
    OAuth-account) terwijl `sites.onboarded_at` leeg is of het profiel te kort
    is, produceert stil generieke content — de kwaliteitsgate meet vorm en ziet
    daar niets van (zie information-gain-bevinding `artikel_zonder_eigen_bewijs`,
    dezelfde faalvorm in een ander domein: 95% van gepubliceerde content bleek
    reproduceerbare AI-tekst zonder dat één gate het opmerkte)."""
    with get_conn() as conn:
        rijen = conn.execute(
            "SELECT s.id, s.name, s.profile, s.onboarded_at FROM sites s "
            "WHERE COALESCE(s.is_test, 0) = 0 AND ("
            "  s.auto_content_enabled = 1"
            "  OR EXISTS (SELECT 1 FROM oauth_accounts oa WHERE oa.site_id = s.id)"
            ") AND (s.onboarded_at IS NULL OR s.onboarded_at = '' "
            "       OR LENGTH(COALESCE(s.profile, '')) < 40)"
        ).fetchall()
    uit: List[Bevinding] = []
    for r in rijen:
        reden = "geen onboarding afgerond" if not r["onboarded_at"] else "profiel korter dan 40 tekens"
        uit.append(Bevinding(
            subject=f"sites:{r['id']}:onboarding",
            detail=(f"{r['name']} draait actief (auto-content of een gekoppeld account) "
                    f"terwijl de onboarding-intake niet compleet is ({reden}) — de "
                    f"contentmotor schrijft hier zonder bedrijfsdoel of merkstem."),
            project=r["name"] or "",
        ))
    return uit


def _check_ticket_notificatie_genegeerd() -> List[Bevinding]:
    """Een 'nieuw ticket'-melding die als bulkmail is weggegooid.

    13 aug 2026: Bewaardvoorjou's eigen contactformulier stuurt supportvragen
    niet rechtstreeks maar als 'nieuw ticket'-melding vanaf een no-reply-adres,
    met de échte vraag + het échte antwoordadres verpakt in de body
    (Ticket/Van/E-mail/Bericht). `bulk.bulk_reason()` herkent 'noreply' in het
    lokale deel en concludeert "een antwoord komt nergens aan" — de mail ging
    als 'newsletter' weg, MET geleegde body_text, en twee échte klantvragen
    (BVJ-0002, BVJ-0003) bleven zo onopgemerkt. `mail/ticket.py` ontpakt dit
    patroon nu vóór die beslissing, maar deze toets is het vangnet voor de
    volgende site met een net iets ander sjabloon of een tweede eigen domein:
    de onderwerpregel van zo'n melding overleeft de body-wis wél.
    """
    with get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT i.id, i.subject, i.from_addr, i.classified, "
                "m.project, m.address "
                "FROM mail_inbox i JOIN mailboxes m ON m.id = i.mailbox_id "
                "WHERE i.classified IN ('newsletter','spam','other','ignored') "
                "AND ("
                "  lower(i.subject) LIKE '%nieuwe vraag%' "
                "  OR lower(i.subject) LIKE '%nieuw ticket%' "
                "  OR lower(i.subject) LIKE '%nieuw supportticket%' "
                "  OR lower(i.subject) LIKE '%support ticket%'"
                ")"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        Bevinding(
            subject=f"mail_inbox:{r['id']}",
            detail=(f"'{r['subject']}' van {r['from_addr']} kreeg classificatie "
                    f"'{r['classified']}' i.p.v. een klantvraag ({r['address']})."),
            project=r["project"] or "",
        )
        for r in rows
    ]


def _check_campagnepost_over_datum() -> List[Bevinding]:
    """Een campagne-post waarvan het plaatsmoment verstreken is en die nog wacht.

    Dit is de toets die het incident zélf had gevonden: het zes-weken-socialplan
    voor BewaardVoorJou stond volledig uitgeschreven klaar en er is geen enkele
    post van geplaatst — zes weken lang, zonder één signaal. Een plan in een
    markdown-bestand kan niet melden dat het stilstaat; een pack met een
    plaatsdatum wel.

    Eén dag speling, want een post van gisteravond 19:30 die vanmorgen nog niet
    goedgekeurd is, is geen storing. De 'stil'-klasse legt daar nog drie dagen
    bovenop voordat er een kaart komt — een gemiste maandag die op donderdag nog
    steeds open staat is een gemiste slot, geen weekendje geduld.
    """
    grens = (datetime.now() - timedelta(days=1)).isoformat()
    with get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT project, campaign, campaign_post, theme, scheduled_for "
                "FROM social_posts "
                "WHERE campaign <> '' AND scheduled_for <> '' AND scheduled_for < ? "
                "AND status = 'pending_review' "
                "ORDER BY scheduled_for",
                (grens,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        Bevinding(
            subject=f"campagne:{r['project']}:{r['campaign']}:{r['campaign_post']}",
            detail=(f"Post {r['campaign_post']} '{r['theme']}' stond gepland voor "
                    f"{(r['scheduled_for'] or '')[:16].replace('T', ' ')} en wacht nog "
                    f"op goedkeuring."),
            project=r["project"] or "",
        )
        for r in rows
    ]


# ── Het register ───────────────────────────────────────────────────────────
#
# Dit is de institutionele herinnering van het systeem: elke regel is een
# storing die geld of vertrouwen heeft gekost, omgezet in een dagelijkse toets.

INVARIANTEN: List[Invariant] = [
    Invariant(
        key="besluit_onzichtbaar_op_eigen_dashboard",
        titel="Besluit onzichtbaar op WeAreImpact's eigen dashboard",
        incident="23 aug 2026: een WhatsApp-afspraakvoorstel (Steentjebij "
                 "Steentje, 24 aug 14:00) stond in de globale Control "
                 "Room-inbox maar toonde 'Wacht op jou (0)' op het "
                 "WeAreImpact-dashboard zelf — de per-project-filter kende "
                 "geen uitzondering voor items zonder resolveerbaar project "
                 "(Agenda, Leads, Scheduler), terwijl WeAreImpact Vincents "
                 "eigen bedrijf is en geen klantproject.",
        severity=BLOKKEREND,
        stap="Bekijk de globale Control Room-inbox (geen project geselecteerd) "
             "voor het item, of herstart de server als de fix in "
             "action_center/service.py niet actief blijkt.",
        check=_check_besluit_onzichtbaar_op_eigen_dashboard,
    ),
    Invariant(
        key="impact_lead_niet_vastgelegd",
        titel="Impact Calculator-lead niet in de Leads-tab",
        incident="22 aug 2026: bij een uitgeputte OpenModel-quota brak de "
                 "verwerking van een Impact Calculator-inzending af vóórdat de "
                 "lead werd vastgelegd — de uitkomstkaart beweerde 'staat als "
                 "Geverifieerd in de Leads-tab' terwijl er nooit een rij bijkwam. "
                 "Twee échte leads (o.a. Impact Box: 185 FTE, EUR 1.440.691 "
                 "berekende besparing/jaar) waren zo spoorloos, en alleen de "
                 "permanente rij in Neon bewees dat ze ooit binnenkwamen. De "
                 "fix stond al op schijf maar was niet live: de server was "
                 "sinds 20 aug niet herstart.",
        severity=BLOKKEREND,
        stap="Bekijk het logboek voor de exacte cijfers en leg de lead "
             "handmatig vast in de Leads-tab, of herprocesseer 'm via de "
             "Impact Calculator-rij in Neon (impact_leads-tabel).",
        check=_check_impact_lead_niet_vastgelegd,
    ),
    Invariant(
        key="goal_vastgelopen_zonder_voortgang",
        titel="Doel staat vast op 'running' zonder voortgang",
        incident="20 aug 2026: 'G2 — AEO-contentmotor WeAreImpact' (en twee "
                 "zusje-goals) stonden sinds 13 aug op 'running' terwijl elke "
                 "publicatietaak al terminaal was (failed/aborted, nul "
                 "completed) — de fase kon daardoor nooit 'completed' worden en "
                 "de executie-lus liep voor altijd rond zonder iets te doen. "
                 "Onzichtbaar in het Actiecentrum (lopende doelen worden bewust "
                 "gedempt) én buiten bereik van zelfherstel (dat op "
                 "status='error' opereert, niet op een stille oneindige lus).",
        severity=STIL,
        stap="Open het doel in de Doelen-tab en start het opnieuw (herstart de "
             "goal, of wijs de mislukte taken af) — de lus is vastgelopen en "
             "komt er zonder ingreep niet meer uit.",
        check=_check_goal_vastgelopen_zonder_voortgang,
    ),
    Invariant(
        key="campagnepost_over_datum",
        titel="Campagne-post staat over datum te wachten",
        incident="16 aug 2026: het communicatie- en socialmediaplan voor "
                 "BewaardVoorJou (18 posts, 6 juli t/m 16 augustus, compleet met "
                 "teksten, beeldbriefs en posttijden) is nooit uitgevoerd. Niet "
                 "omdat het slecht was, maar omdat het een markdown-bestand in "
                 "een Downloads-map was: niets in het systeem wist dat post 3.1 "
                 "op maandag 19:30 hoorde te staan, dus kon ook niets melden dat "
                 "hij er niet stond. Zes weken lang leek er niets aan de hand.",
        severity=STIL,
        stap="Open Social Creatie, keur de wachtende campagne-posts goed en plaats "
             "ze — of verschuif de campagne (POST /api/social-content/campaign/import "
             "met een nieuwe startmaandag) als de reeks niet meer past.",
        check=_check_campagnepost_over_datum,
    ),
    Invariant(
        key="ticket_notificatie_genegeerd",
        titel="Ticket-melding als bulkmail weggegooid",
        incident="13 aug 2026: Bewaardvoorjou's contactformulier stuurt supportvragen "
                 "als 'nieuw ticket'-melding vanaf noreply@ met de échte vraag in de "
                 "body; de no-reply-heuristiek gooide BVJ-0002 en BVJ-0003 stil weg "
                 "(classificatie 'newsletter', body_text geleegd) — twee klanten "
                 "wachtten op een antwoord dat nooit kwam.",
        severity=BLOKKEREND,
        stap="Open de mail bij de hoster (POP/webmail) en beantwoord de klant "
             "handmatig; controleer of mail/ticket.py dit sjabloon herkent — zo "
             "niet, breid het patroon uit voor dit project.",
        check=_check_ticket_notificatie_genegeerd,
    ),
    Invariant(
        key="interne_taakopdracht_live",
        titel="Interne taakopdracht staat gepubliceerd",
        incident="23 jul 2026: 'Schrijf meta-titel & -description voor Pagina 2' ging "
                 "live op bewaardvoorjou.nl via een reparatiescript dat de gate omzeilde.",
        severity=BLOKKEREND,
        stap="Haal deze pagina offline in het CMS en zet een 301 naar een relevant artikel.",
        check=_check_interne_taakopdracht_live,
    ),
    Invariant(
        key="werkbon_in_de_wachtrij",
        titel="Wachtrij-item heet naar de opdracht in plaats van naar het artikel",
        incident="15 aug 2026: 179 van de 188 wachtende items heetten 'Herschrijf het "
                 "artikel X tot wereldklasse SEO-content (1200-1500 woorden)'. De slug "
                 "wordt van de titel afgeleid, dus één klik op Publiceer had de werkbon "
                 "als URL op de site gezet. `publish_to_weareimpact` viel bij een "
                 "ontbrekende titel terug op de objective van de run, en de Orchestrator "
                 "nam die titel daarna van generatie op generatie over.",
        severity=BLOKKEREND,
        stap="Open het item in de Wachtrij en zet de titel op de H1 van het artikel zelf "
             "(die staat bovenaan de tekst); wijs het af als er geen artikel onder zit. "
             "Publiceer niet met deze titel — de slug volgt hem.",
        check=_check_werkbon_in_de_wachtrij,
    ),
    Invariant(
        key="merkbrief_verkeerd_project",
        titel="Artikel claimt Vincents identiteit op een ander project",
        incident="19 aug 2026: de Gauntlet-merkbrief ('SCHRIJF ALS VINCENT VAN "
                 "MUNSTER, eerste persoon') werd zonder project aangeroepen en dus "
                 "voor élke run gebruikt. Een Bijeen-artikel opende met 'in mijn "
                 "jaren als directeur van Stichting de Baan draaide ik meer dan "
                 "veertig van die dagen, met 180+ vrijwilligers... 70.000+ "
                 "geluksmomenten' — een volledig verzonnen naam, functie en "
                 "trackrecord, want er was voor Bijeen geen echte biografie.",
        severity=BLOKKEREND,
        stap="Wijs het item af (of haal het offline als het al gepubliceerd staat) — "
             "een verzonnen persoonlijke autoriteit is geen SEO-detail maar een "
             "geloofwaardigheids- en reputatierisico. Laat het opnieuw genereren.",
        check=_check_merkbrief_verkeerd_project,
    ),
    Invariant(
        key="slug_onveilig",
        titel="Gepubliceerde URL bevat tekens die 404 geven",
        incident="24 jul 2026: slugs met '&', '+' en '(' gingen live; de oude slugify "
                 "gebruikte een zwarte lijst, dus elk niet-bedacht teken werd een 404. "
                 "2 aug 2026: de toets las de kolom `slug` in plaats van de "
                 "gepubliceerde URL en meldde daardoor acht gezonde pagina's als 404.",
        severity=BLOKKEREND,
        stap="Publiceer het artikel opnieuw (de slug wordt nu correct gegenereerd) en "
             "zet een 301 van de oude URL.",
        check=_check_slug_onveilig,
    ),
    Invariant(
        key="slug_kolom_wijkt_af_van_url",
        titel="Opgeslagen slug is niet het pad dat live staat",
        incident="2 aug 2026: `content_jobs.slug` hield de ruwe titel vast terwijl de "
                 "publisher een nette slug maakte. Acht valse blokkerende alarmen, en "
                 "elke andere lezer van die kolom (dedupe, sitemap) leest hetzelfde mis.",
        severity=HYGIENE,
        stap="Geen directe actie: de URL werkt. Structureel hoort de publisher de "
             "gebruikte slug terug te schrijven naar de job.",
        check=_check_slug_kolom_wijkt_af,
    ),
    Invariant(
        key="zoekwoord_kannibalisatie",
        titel="Twee of meer live artikelen op hetzelfde zoekwoord",
        incident="23 jul 2026: twee artikelen over 'beste partners voor AI-oplossingen' "
                 "gingen allebei live op weareimpact.nl; de slug-dedupe zag ze niet "
                 "omdat de titels verschilden.",
        severity=BLOKKEREND,
        stap="Kies het sterkste artikel, haal de andere offline en zet er een 301 naartoe.",
        check=_check_zoekwoord_kannibalisatie,
    ),
    Invariant(
        key="cluster_kannibalisatie",
        titel="Meerdere eigen pagina's vertonen bij Google op één zoekwoord",
        incident="3 aug 2026: Bewaard voor Jou had 102 pagina's live en kreeg acht "
                 "'nieuwe' kansen aangeboden, terwijl acht eigen pagina's al op "
                 "'levensverhaal vastleggen' vertoonden — samen goed voor positie "
                 "25 en 15 klikken in 28 dagen. `zoekwoord_kannibalisatie` zweeg: "
                 "die leest `content_jobs.keyword` en kende maar twee van die acht, "
                 "want de rest was buiten Impact OS om gepubliceerd.",
        severity=BLOKKEREND,
        stap="Kies per zoekwoord één hoofdpagina, laat de andere daarheen linken of "
             "voeg ze samen met een 301 — en schrijf er géén nieuw artikel bij.",
        check=_check_cluster_kannibalisatie,
    ),
    Invariant(
        key="sitemap_dubbele_pagina",
        titel="Twee live pagina's over hetzelfde onderwerp, buiten GSC om gevonden",
        incident="7 aug 2026: zeven paren dubbele pagina's op steentjebijsteentje.nl "
                 "(vijf een letterlijke '-2'-kopie) en bewaardvoorjou.nl — geen van de "
                 "12 op dat moment openstaande cluster_kannibalisatie-bevindingen dekte "
                 "ze, want die toets leest gsc_history en ziet dus alleen duplicaten "
                 "die al vertoningen krijgen.",
        severity=BLOKKEREND,
        stap="Kies per paar één versie, haal de andere offline en zet een 301 naar de blijver.",
        check=_check_sitemap_dubbele_pagina,
    ),
    Invariant(
        key="weekrapport_niet_vastgelegd",
        titel="Weekrapport draaide, maar legde geen weekbeeld vast",
        incident="4 aug 2026: het weekrapport berekende elke maandag per project de "
                 "28-daagse zoekprestaties mét quick wins en CTR-gaten, en stuurde dat "
                 "naar de mail, Obsidian en een chat-sessie — drie plekken waar alleen "
                 "een mens kijkt. Geen agent kon het lezen, dus stuurde de beste "
                 "analyse van het systeem nul beslissingen aan en leerde Iris er niets "
                 "van. De job meldde al die tijd 'ok'.",
        severity=STIL,
        stap="Draai het weekrapport opnieuw (POST /api/analytics/weekly-report) en kijk "
             "in de log waarom er geen rijen in `weekly_insights` landden — meestal een "
             "site zonder gsc_property of een GSC-koppeling die geen data teruggeeft.",
        check=_check_weekrapport_niet_vastgelegd,
    ),
    Invariant(
        key="weekkans_blijft_liggen",
        titel="Dezelfde quick win staat al weken onopgepakt in het weekrapport",
        incident="4 aug 2026: ingevoerd samen met de vastlegging van het weekrapport. "
                 "Zonder deze toets is niet te zien of het rapport iets verandert — en "
                 "een advies dat zich week na week herhaalt terwijl de positie "
                 "stilstaat, is precies hoe 'activiteit is geen effect' er bij een "
                 "analyse uitziet.",
        severity=STIL,
        stap="Pak de kans op (artikel verversen of title/meta herschrijven) óf voer hem "
             "bewust af — maar laat hem niet elke maandag opnieuw aanbieden.",
        check=_check_weekkans_blijft_liggen,
    ),
    Invariant(
        key="artikel_zonder_eigen_bewijs",
        titel="Artikelen zonder eigen bewijs (information gain)",
        incident="5 aug 2026: `case_studies` bevatte 4 rijen, alle vier op één van de "
                 "twaalf sites; van de 138 artikelen met een QC-rapport hadden er 7 een "
                 "écht gekoppelde casestudy. De haak bestond al sinds de "
                 "Goldie-pipeline — `_make_outline` eist een bewijs-sectie — maar viel "
                 "stilzwijgend weg op een lege kennisbank. Ruwweg 95% van 102 "
                 "gepubliceerde artikelen is daardoor reproduceerbare AI-tekst, en de "
                 "kwaliteitsgate zag er niets van: die meet vorm, en generiek scoort 84.",
        severity=STIL,
        stap="Voeg per site 2-3 echte klantresultaten toe onder Kennisbank → Casestudies "
             "(cijfers, aanpak, uitkomst). Staan ze er wél en worden ze niet gebruikt, "
             "kijk dan in `qc_report.eigen_bewijs` van een recent artikel.",
        check=_check_artikel_zonder_eigen_bewijs,
    ),
    Invariant(
        key="contentleerlus_zonder_lessen",
        titel="Contentleerlus draait wekelijks en leert niets",
        incident="5 aug 2026: `agent_lessons` bevatte 2 rijen, allebei van de "
                 "beursagent. `content_learning_eval` draaide 3 augustus, meldde 'ok' "
                 "en leverde nul contentlessen — hetzelfde patroon als het weekrapport "
                 "dat alleen naar de mail ging: een schakel die slaagt en niets "
                 "voortbrengt. Zonder deze toets is 'nog niets te leren' niet te "
                 "onderscheiden van 'de meting werkt niet'.",
        severity=STIL,
        stap="Kijk in de detailtekst wat de gemeten oorzaak is. Te kleine cohorten = "
             "wachten op rijping (niets doen). Wél genoeg artikelen en toch niets: "
             "draai `run_content_learning_eval()` handmatig en controleer of de "
             "page-snapshots in `gsc_history` de slugs van de artikelen dekken.",
        check=_check_contentleerlus_zonder_lessen,
    ),
    Invariant(
        key="publicatie_onbewezen",
        titel="'Published' zonder vastgelegde URL",
        incident="25 jul 2026: 78 doelen sloten af als 'voltooid' op louter "
                 "concept-publicaties; dashboard-alerts werden erdoor gedempt.",
        severity=BLOKKEREND,
        stap="Controleer of dit artikel écht live staat; zo niet, zet de job terug op "
             "'pending_review' zodat hij opnieuw door de publicatieroute gaat.",
        check=_check_publicatie_onbewezen,
    ),
    Invariant(
        key="afgewezen_maar_live",
        titel="Afgewezen in de database, live op het web",
        incident="2 aug 2026: negen pagina's stonden op 'rejected' terwijl ze gewoon "
                 "live waren, waaronder 'Impact OS end-to-end publicatietest' op de site "
                 "van een klant. Afwijzen verandert alleen de rij, niet de wereld. "
                 "Diezelfde dag bleek de toets zélf de wereld niet te raadplegen: vijf "
                 "van de negen waren al offline en de kaart kon nooit dichtgaan.",
        severity=BLOKKEREND,
        stap="Haal deze pagina's offline in het CMS. Structureel: een afwijzing ná "
             "publicatie hoort een depublicatie in gang te zetten, niet alleen een "
             "statuswijziging.",
        check=_check_afgewezen_maar_live,
    ),
    Invariant(
        key="bevinding_blijft_liggen",
        titel="Blokkerende bevindingen staan al weken open",
        incident="4 aug 2026: 82 openstaande bevindingen over alle projecten, waarvan "
                 "54 blokkerend of stil, sommige twee weken oud — en géén enkel "
                 "reparatiepad in de codebase voor de drie grootste "
                 "(`metatitel_afgekapt`, `cluster_kannibalisatie`, "
                 "`afgewezen_maar_live`). Het systeem meldde trouw wat er stuk was en "
                 "dat melden veranderde niets; elke nieuwe invariant werd een to-do "
                 "voor een mens in plaats van werk voor een agent.",
        severity=STIL,
        stap="Kies per invariant: bouw een remedie (zie `publish/repair.py` en "
             "POST /api/iris/integrity/repair/{invariant}), of los de gevallen "
             "handmatig op. Blijft een blokkerende bevinding weken staan zonder dat "
             "één van beide gebeurt, dan is de kaart zelf het probleem geworden.",
        check=_check_bevinding_blijft_liggen,
    ),
    Invariant(
        key="linkedin_antwoord_niet_geplaatst",
        titel="Goedgekeurd LinkedIn-antwoord staat niet daadwerkelijk geplaatst",
        incident="20 aug 2026: LinkedIn heeft geen partner-API voor DM's/reacties, dus "
                 "plaatst een browserautomatisering (via /loop) het antwoord na "
                 "goedkeuring — niet de review-klik zelf. Vóór deze toets zette "
                 "'Plaats antwoord' een LinkedIn-concept meteen op 'sent' zonder dat er "
                 "ooit iets op LinkedIn was geplaatst, dezelfde fout als de "
                 "social-campagne vóór `mark_posted_manually` (7g). Bewust "
                 "BLOKKEREND en niet STIL: er wacht een echt persoon op antwoord, en "
                 "de standaard STIL-vertraging (3 dagen bovenop de 12 uur hierboven) "
                 "zou dat gesprek onnodig laten doodbloeden.",
        severity=BLOKKEREND,
        stap="Open Iris Remote of de Social-tab en plaats het antwoord handmatig op "
             "LinkedIn (kopieer-knop), of start de LinkedIn-loop opnieuw als die "
             "gestopt is.",
        check=_check_linkedin_antwoord_niet_geplaatst,
    ),
    Invariant(
        key="indexnow_keyfile_ontbreekt",
        titel="IndexNow-aanmeldingen worden genegeerd (keybestand onbereikbaar)",
        incident="4 aug 2026: 28 publicaties droegen `indexnow: {status: fout, "
                 "status_code: 403}` in hun publish_result en verder nergens — geen "
                 "kaart, geen logregel die iemand las. Bij nameting was het keybestand "
                 "op 7 van de 10 sites onbereikbaar: 5× een harde 404 en 2× de "
                 "HTML-schil van de site met HTTP 200 erboven. Bing, Yandex, Seznam en "
                 "Naver hebben daardoor maandenlang geen enkele URL doorgekregen, "
                 "terwijl de Wachtrij bij elke goedkeuring 'aangemeld' meldde.",
        severity=BLOKKEREND,
        stap="Plaats het keybestand op de site-root met exact de key als inhoud. Voor "
             "Netlify-sites deployt Impact OS het mee (publiceer één artikel); een "
             "extern gehoste site moet het bestand zelf serveren — let erop dat de "
             "catch-all route van een SPA het niet opslokt.",
        check=_check_indexnow_keyfile,
    ),
    Invariant(
        key="publicatiekanaal_dood",
        titel="Geen enkel artikel van deze site staat live",
        incident="17 jul – 3 aug 2026: alle 22 artikelen die Impact OS naar ictusgo.nl "
                 "publiceerde gaven 404. De publicatie-API antwoordde elke keer '201 "
                 "created' en de artikelen stonden gewoon in de database van de site; "
                 "de site zelf viel over een Date die hij als string behandelde en "
                 "slikte die fout in als 'artikel bestaat niet'. Het Actiecentrum "
                 "toonde 12 losse kaarten met de knop 'Opnieuw publiceren' — twaalf "
                 "keer een remedie voor iets dat niet kapot was.",
        severity=BLOKKEREND,
        stap="Publiceer niet opnieuw — dat lukte al. Controleer de ontvangende site "
             "zelf: haalt hij de artikelen wél uit zijn database op, en slikt hij "
             "daarbij fouten in als 'niet gevonden'? Kijk in de runtime-logs van de "
             "site, niet in die van Impact OS.",
        check=_check_publicatiekanaal_dood,
    ),
    Invariant(
        key="publicatiefout_zonder_kaart",
        titel="Goedgekeurd artikel niet live, zonder alarm",
        incident="24 jul – 2 aug 2026: 'publicatie_mislukt' werd met status 'ok' gelogd. "
                 "Ictusgo's 404 kwam drie ochtenden terug als 'les' in Iris' briefing "
                 "zonder één keer als beslissing in het Actiecentrum te staan.",
        severity=BLOKKEREND,
        stap="Bekijk de fout in de Wachtrij en publiceer opnieuw; blijft het misgaan, "
             "dan zit het defect in de publicatieroute van deze site.",
        check=_check_publicatiefout_zonder_kaart,
    ),
    Invariant(
        key="metatitel_afgekapt",
        titel="Live meta-titel is midden in een woord afgekapt",
        incident="2 aug 2026: 47 van 103 artikelen droegen een op exact 60 tekens "
                 "afgesneden meta-titel ('... Jouw teambeleving in de l'), 15 daarvan "
                 "al gepubliceerd. De harde [:60] stond op vier plekken; voor slugs was "
                 "die les al geleerd, voor de meta-titel niet.",
        severity=BLOKKEREND,
        stap="Publiceer deze artikelen opnieuw — de titel wordt nu op een woordgrens "
             "geknipt. Google toont tot die tijd de afgekapte versie.",
        check=_check_metatitel_afgekapt,
    ),
    Invariant(
        key="kwaliteitsscore_is_stopregel",
        titel="Kwaliteitsscores klonteren op de gate",
        incident="2 aug 2026: 39 van 76 Wachtrij-artikelen stonden op exact 82 en 4 op "
                 "exact 80. De verbeter-lus stopt bij de eerste meting boven de gate, en "
                 "de reviewer varieert 65-92 op identieke invoer — het cijfer waarop "
                 "gepubliceerd wordt, zegt dus wanneer de lus stopte.",
        severity=STIL,
        stap="Draai de opschoonronde (POST /api/content-queue/upgrade-all) — die meet "
             "twee keer onafhankelijk en bewaart de laagste, zodat de score een "
             "ondergrens wordt in plaats van een hoogtepunt.",
        check=_check_kwaliteitsscore_is_stopregel,
    ),
    Invariant(
        key="outreach_voorraad_onbenut",
        titel="Mailbare leads in voorraad, niets in review",
        incident="2 aug 2026: de batch meldde 'funnel-invoer is op' terwijl er zeven "
                 "mailbare leads stonden. `select_batch_leads` kapte af op `count` "
                 "vóór de adres-zeef, en de acht generieke info@-adressen bovenaan "
                 "blokkeerden het venster elke dag opnieuw.",
        severity=STIL,
        stap="Draai POST /api/leads/outreach-batch handmatig en lees wat er uitkomt; "
             "komt er niets, dan zeeft de selectie de voorraad weg.",
        check=_check_outreach_voorraad_onbenut,
    ),
    Invariant(
        key="trefkans_gevleid",
        titel="Te veel voorspellingen eindigen 'onbeslist'",
        incident="2 aug 2026: 9 van 23 uitslagen op 'unclear', waarvan 5 stilstanden bij "
                 "een voorspelling mét drempel. De gemelde trefkans van 42,9% was in "
                 "werkelijkheid 26%.",
        severity=STIL,
        stap="Controleer `_judge` in iris/predictions.py: komt de ruisdrempel vóór de "
             "doeltoets? Een gemiste drempel is een misser, geen onbeslist geval.",
        check=_check_trefkans_gevleid,
    ),
    Invariant(
        key="kans_vastgelopen",
        titel="Zoekwoord verbruikt zonder resultaat",
        incident="27 jul 2026: 62 kansen op 'in_progress' tegen 11 gepubliceerd — de "
                 "contentmotor droogde op met een volle tabel.",
        severity=STIL,
        stap="Draai de reconciliatie (POST /api/seo/reconcile of de weekscan van maandag); "
             "blijft het staan, dan loopt de reconciliatie zelf vast.",
        check=_check_kans_vastgelopen,
    ),
    Invariant(
        key="content_hoort_bij_andere_site",
        titel="Een stuk in de Wachtrij gaat over een ander project",
        incident="15 aug 2026: de terugval `_resolve_weareimpact_site_id()` liet elke "
                 "run zonder herleidbaar project bij WeAreImpact landen. Er stonden 25 "
                 "stukken over Bijeen, Pootgelukkig, Liefde voor Iedereen en "
                 "TeambuildingMetImpact in die Wachtrij ('De 10 beste cadeaus voor "
                 "koppels', 'Advies hond adopteren in Antwerpen'), en twee gingen "
                 "écht live op weareimpact.nl.",
        severity=BLOKKEREND,
        stap="Zet het stuk op de juiste site (of wijs het af) vóór je publiceert. "
             "Komen er nieuwe bij, dan raadt een aanroeper het project nog steeds "
             "in plaats van te stoppen — zie `OnbekendProject` in gauntlet/service.py.",
        check=_check_content_hoort_bij_andere_site,
    ),
    Invariant(
        key="leerlus_leeg",
        titel="Iris' lessen raken niet aan voorspellingen gekoppeld",
        incident="27 jul 2026: 51 actieve lessen, 2 koppelingen. De leerlus was gebouwd "
                 "maar draaide leeg; confidence bleef overal op de startwaarde 0,50.",
        severity=STIL,
        stap="Controleer `_match_lesson` in iris/service.py: parafraseert het model zó "
             "sterk dat zelfs de tolerante match onder de drempel blijft?",
        check=_check_leerlus_leeg,
    ),
    Invariant(
        key="triage_remedie_zonder_effect",
        titel="'Analyseer & fix' herhaalt een remedie die nog nooit iets oploste",
        incident="6 aug 2026: op de audit-kaart over cluster-kannibalisatie koos de "
                 "triage-LLM een contentronde — precies wat die invariant verbiedt. Er "
                 "kwam niets uit ('Geen uitvoering opgeleverd'), maar de keuze stond al "
                 "vast in `iris_error_fixes` en zou bij elke volgende klik zonder LLM "
                 "worden herhaald.",
        severity=STIL,
        stap="Zet de remedie op inactief (`UPDATE iris_error_fixes SET active = 0`) en "
             "klik opnieuw op 'Analyseer & fix' — dan stelt Iris een nieuwe diagnose. "
             "Blijft hij terugkomen, dan werkt `_verleer_bij_aanhoudend_falen` niet.",
        check=_check_triage_remedie_zonder_effect,
    ),
    Invariant(
        key="voorspelling_niet_afgerekend",
        titel="Verstreken voorspelling nooit afgerekend",
        incident="Structureel risico: een voorspelling die niet wordt afgerekend levert "
                 "geen bewijs, en dan meet de trefkans alleen de makkelijke gevallen.",
        severity=STIL,
        stap="Controleer of `evaluate_due` draait aan het begin van de briefing en of de "
             "metriek voor dit project meetbaar is.",
        check=_check_voorspelling_niet_afgerekend,
    ),
    Invariant(
        key="uitkomst_zonder_artefact",
        titel="Succes gemeld zonder aanwijsbaar resultaat",
        incident="CLAUDE.md-regel, uitvoerbaar gemaakt: 'elke taak/run die klaar claimt "
                 "hoort een artefact-link te hebben'. Zonder toets is het een goed voornemen.",
        severity=STIL,
        stap="Zoek de aanroeper op en geef `log_outcome` een artifact mee — of laat de "
             "actie eerlijk falen als er niets is opgeleverd.",
        check=_check_uitkomst_zonder_artefact,
    ),
    Invariant(
        key="agentctl_run_zonder_effect",
        titel="Agent Control-deploy nooit afgesloten",
        incident="13 aug 2026: 'Voer allemaal uit' spawnde 13 Gauntlet-runs zonder "
                 "tool-access; niets las het run_id ooit terug, dus landde er niets in "
                 "de Wachtrij of het Actiecentrum. agentctl_deploys + een poller per "
                 "pijler lossen dat op; deze toets vangt de regressie waarin de poller "
                 "zelf sterft (bv. een serverherstart tijdens het pollen) en een "
                 "'running'-rij voor altijd open blijft staan.",
        severity=STIL,
        stap="Bekijk de Gauntlet-run (indien van toepassing) in de Gauntlet-tab en "
             "sluit de rij handmatig af, of klik de suggestie opnieuw uit vanaf "
             "Agent Control.",
        check=_check_agentctl_run_zonder_effect,
    ),
    Invariant(
        key="content_job_meervoudig_herschreven",
        titel="Hetzelfde artikel meerdere keren tegelijk in bewerking",
        incident="14 aug 2026: `orchestrator.process_one_under_threshold` sloot een "
                 "succesvol herschreven bronrecord nooit af (geen mark_superseded), dus "
                 "vond de volgende aanroep hetzelfde 'rejected'-record terug en "
                 "herschreef het opnieuw — één Bijeen- en één WeAreImpact-artikel elk "
                 "10+ keer op één dag, wat de hele dagbudget (5M tokens) opsoupeerde en "
                 "tien+ bijna-identieke duplicaten in de Wachtrij achterliet.",
        severity=BLOKKEREND,
        stap="Bekijk de duplicaten in de Wachtrij, keur de beste versie goed en wijs de "
             "rest af; controleer of het aanmakende mechanisme (orchestrator, "
             "content_improver, een goal-taak) het bronrecord afsluit i.p.v. dupliceert.",
        check=_check_content_job_meervoudig_herschreven,
    ),
    Invariant(
        key="orchestrator_teller_teruggezet",
        titel="Pogingenteller liegt over hoe vaak dit al herschreven is",
        incident="15 aug 2026: `scripts/bijeen_worldclass_engine.py` POST'te "
                 "rechtstreeks naar `/api/gauntlet` — dus buiten "
                 "`process_one_under_threshold` en zijn cross-run cap om — en schreef "
                 "na elke escalatie `orchestrator_attempts=1, status='stuck'` terug op "
                 "het bronrecord. Daarmee zette het precies de twee velden terug "
                 "waarop de rem besluit. Eén WeAreImpact-artikel werd zo 17x "
                 "herschreven, met 128 bijna-identieke duplicaten in de Wachtrij en "
                 "6,2M tokens op één dag tot gevolg — genoeg om het dagbudget te "
                 "breken en alle andere autonome runs stil te leggen. De duplicaten "
                 "werden wél gemeld (`content_job_meervoudig_herschreven`); dat de "
                 "teller zélf onbetrouwbaar was, zag niemand. Tweede oorzaak, "
                 "dezelfde dag gevonden: `mark_superseded` gaf de telling niet door "
                 "aan de opvolger, dus begon de cap bij élke generatie opnieuw — alle "
                 "244 WeAreImpact-jobs stonden op 0 terwijl één artikel zestien "
                 "herschrijvingen had. Beide wegen worden nu getoetst.",
        severity=BLOKKEREND,
        stap="Zoek wat er in `content_jobs.orchestrator_attempts` schrijft buiten "
             "`content_pipeline.bump_orchestrator_attempts` om (scripts, handmatige "
             "SQL, een tweede pad naar de Gauntlet) en haal dat weg — er hoort maar "
             "één weg naar de Gauntlet te zijn.",
        check=_check_orchestrator_teller_teruggezet,
    ),
    Invariant(
        key="pijler_dubbel_ingezet",
        titel="Iris en Agent Control pakten dezelfde pijler dubbel op",
        incident="22 aug 2026: Iris' briefing (06:45) en de scheduler-job "
                 "iris_auto_deploy (07:00) beslisten allebei onafhankelijk welk project "
                 "welke pijler nodig had, zonder van elkaar te weten. Omdat de "
                 "content-score alleen 'published' meet en nooit 'pending_review', bleef "
                 "een project na Iris' contentrun voor Agent Control de zwakste pijler — "
                 "en kreeg het dezelfde ochtend een tweede, volledige Gauntlet-run. "
                 "`iris/pillar_guard.py` is de gedeelde toets die beide kanten nu vóór "
                 "het starten raadplegen.",
        severity=BLOKKEREND,
        stap="Controleer of `pillar_guard.pillar_handled_today` in beide callers "
             "(iris/actions.py:content_run/seo_refresh en "
             "agentctl/suggest.py:_today_has_deploy) nog daadwerkelijk wordt aangeroepen "
             "vóórdat er werk gestart wordt.",
        check=_check_pijler_dubbel_ingezet,
    ),
    Invariant(
        key="radar_signaal_verlopen",
        titel="Radarsignalen verlopen niet",
        incident="27 jul 2026: 2411 van 2656 signalen stonden nog op 'new'; oude trends "
                 "verdrongen de verse.",
        severity=HYGIENE,
        stap="De radar-scan ruimt sinds 2 aug zelf op; blijft dit staan, dan draait "
             "`prune_stale_signals` niet.",
        check=_check_radar_signaal_verlopen,
    ),
    Invariant(
        key="lead_geen_organisatie",
        titel="Geen-organisatie in de actieve leadvoorraad",
        incident="27 jul 2026: 60% van de voorraad was een paginatitel van een artikel of "
                 "vacature; de conversieratio's van de acquisitieformule maten niets.",
        severity=HYGIENE,
        stap="Draai POST /api/leads/cleanup-unmailable, of zet deze leads handmatig op 'lost'.",
        check=_check_lead_geen_organisatie,
    ),
    Invariant(
        key="bulk_in_behandeling",
        titel="Nieuwsbrief behandeld als vraag of afspraak",
        incident="1 aug 2026: vijf concept-antwoorden op nieuwsbrieven, plus een "
                 "afspraakvoorstel voor 30 mei 2027 uit een marketingmail.",
        severity=HYGIENE,
        stap="Controleer of de mailroute de headers meegeeft aan `classify` — de "
             "Graph-flow vroeg internetMessageHeaders eerder niet op.",
        check=_check_bulk_in_behandeling,
    ),
    Invariant(
        key="agenda_horizon",
        titel="Afspraakvoorstel buiten de geloofwaardige horizon",
        incident="1 aug 2026: een datum uit een lopende zin in een nieuwsbrief werd een "
                 "voorstel tien maanden vooruit.",
        severity=HYGIENE,
        stap="Wijs het voorstel af; de parser heeft een zin gelezen die geen afspraak was.",
        check=_check_agenda_horizon,
    ),
    Invariant(
        key="afspraak_dubbel_geboekt",
        titel="Twee voorstellen geboekt op hetzelfde moment",
        incident="11 aug 2026: 'blok alle dinsdagen tussen 09.00 en 10.00' werd twee "
                 "minuten na elkaar ingediend en allebei goedgekeurd, 11 seconden na "
                 "elkaar — een wekelijkse dinsdagblokkade staat sindsdien dubbel. "
                 "Zelfde dag: twee afspraakvoorstellen uit mail (een outreach-afwijzing "
                 "die door een CTA in de handtekening als 'appointment' classificeerde, "
                 "en een reply met 'hebben we' erin) landden allebei op 10:00–10:30 en "
                 "zijn allebei geboekt — de live freeBusy-check bij de tweede goedkeuring "
                 "vond de eerste, net geboekte, afspraak kennelijk nog niet.",
        severity=STIL,
        stap="Verwijder één van de twee geboekte afspraken uit Google Calendar en wijs "
             "het overbodige voorstel hier af. `agent.approve_proposal` toetst sinds "
             "11 aug ook tegen onze eigen tabel (geen API-race meer) — komt dit tóch "
             "terug, dan omzeilt iets die local-overlap-check.",
        check=_check_afspraak_dubbel_geboekt,
    ),
    Invariant(
        key="stilstand_dubbel_gemeld",
        titel="Eén gemiste taak, twee kaarten in het Actiecentrum",
        incident="2 aug 2026: 'biweekly_content' en 'linkbuilding_weekly' stonden dubbel "
                 "in de inbox — dezelfde zin via `activity_log` én via `scheduler_gaps`, "
                 "waarbij alleen de tweede de knop had die het werk terughaalt.",
        severity=STIL,
        stap="Er is één meldweg te veel. De inbox hoort per beslissing één kaart te "
             "tonen; verwijder de dubbele bron in plaats van de kaart weg te klikken.",
        check=_check_stilstand_dubbel_gemeld,
    ),
    Invariant(
        key="stilstand_ouder_dan_de_job",
        titel="Gemiste run van vóórdat de taak bestond",
        incident="3 aug 2026: bij het toevoegen van 'invest_daily_cycle' verscheen "
                 "meteen de kaart \"draaide 9× tussen 21-07 en 31-07 niet — stops en "
                 "koersdoelen van de open posities zijn niet getoetst\". Er waren op "
                 "21 juli geen posities, geen stops en geen job; de stilstand-teller "
                 "rekende het hele trigger-verleden toe aan een job van gisteren. Een "
                 "zelfverzekerde zin met een knop eronder over werk dat nooit bestond.",
        severity=STIL,
        stap="Verwijder deze gaten uit `scheduler_gaps`; ze horen niet te kunnen "
             "ontstaan sinds `scheduler_runs.first_seen_at` de ondergrens vormt.",
        check=_check_stilstand_ouder_dan_de_job,
    ),

    # ── Mission Radar ─────────────────────────────────────────────────────
    Invariant(
        key="radar_watch_dood",
        titel="Radarbron levert al scans lang niets op",
        incident="3 aug 2026: twaalf van de twintig RSS-feeds en negen site-watches "
                 "hadden sinds hun aanmaak geen enkel signaal opgeleverd. Niets meldde "
                 "dat, want een dode bron en een rustige bron zagen er identiek uit — "
                 "`last_scanned_at` werd alleen gezet als er íets werd opgeslagen.",
        severity=STIL,
        stap="Controleer het adres van deze bron in de Radar-tab, of zet hem uit — "
             "elke scan kost tijd en levert hier aantoonbaar niets op.",
        check=_check_radar_watch_dood,
    ),
    Invariant(
        key="radar_trendbrug_stil",
        titel="Trend-brug zet geen signalen meer om in kansen",
        incident="3 aug 2026: de brug leverde sinds 27 juli nul kansen terwijl er "
                 "dagelijks honderden signalen binnenkwamen. Het zoekwoord kwam uit de "
                 "watchlist in plaats van uit het signaal, dus na één conversie was elk "
                 "watchlist-woord door de dedupe voor altijd verbruikt. Alle 38 kansen "
                 "die de brug ooit maakte waren letterlijk een watchlist-regel.",
        severity=STIL,
        stap="Draai POST /api/demand/trend-sync en lees de uitkomst. Levert hij nog "
             "steeds niets, dan zit het gat tussen signaal en zoekwoord.",
        check=_check_radar_trendbrug_stil,
    ),
    Invariant(
        key="suggestie_pijler_zonder_agent",
        titel="Pijler in de Iris-cijfers waar geen agent bij hoort",
        incident="16 aug 2026: `metrics.project_scores` kreeg een vijfde pijler `geo` "
                 "in het pillars-blok met de aantekening 'niet meegeteld'. Dat gold voor "
                 "de optelsom, niet voor de code die erover itereert. De suggestie-engine "
                 "sorteert álle pijlers op score: bij een site zonder GEO-scan is die "
                 "None en viel de scheduler-job iris_auto_deploy een etmaal om; bij een "
                 "site mét een lage GEO-score (schaal 0-100 tussen pijlers van 0-25) "
                 "wordt geo de 'zwakste', vindt geen agent en verdwijnt dat project "
                 "stilzwijgend uit de suggesties.",
        severity=STIL,
        stap="Geef de pijler een agent in `_PILLAR_AGENT`, of zet hem in "
             "`_INFORMATIEVE_PIJLERS` als hij alleen inzicht is. Beide zijn een "
             "besluit; geen van beide is de huidige toestand.",
        check=_check_suggestie_pijler_zonder_agent,
    ),
    Invariant(
        key="kans_zonder_gemeten_vraag",
        titel="Alle kansen van een site zijn giswerk",
        incident="16 aug 2026: WeAreImpact bood 24 openstaande kansen aan waarvan er nul "
                 "één impressie in Search Console had. De Demand Engine leverde voor deze "
                 "site niets, dus was de voorraad volledig gevuld door de trend-brug met "
                 "koppen van andermans nieuwsberichten ('Inspiratiebijeenkomst Hybride "
                 "Zorg en AI in de ggz'). Het dashboard bood er onder één knop 22 tegelijk "
                 "aan. Elke afzonderlijke kans zag er normaal uit en de wekelijkse scan "
                 "meldde 'ok' — de vraag of er onder de héle voorraad één meting zat "
                 "stelde niemand.",
        severity=STIL,
        stap="Controleer de GSC-koppeling en draai de Demand-scan opnieuw. Levert die "
             "nog steeds geen striking-distance-kansen, dan is de site te jong voor "
             "gemeten vraag en is elk artikel eruit expliciet een gok — schrijf er "
             "weinig en meet wat ze doen.",
        check=_check_kans_zonder_gemeten_vraag,
    ),

    # ── Beursmeester ──────────────────────────────────────────────────────
    Invariant(
        key="positie_zonder_stop",
        titel="Open positie zonder stop",
        incident="2 aug 2026, bij de bouw: de risicomodule weigert een voorstel zonder "
                 "stop, maar elke rij die buiten die route ontstaat (migratie, handmatige "
                 "correctie, halve sluiting) zou onbeschermd meelopen zonder dat iets "
                 "erover klaagt. De bescherming die je denkt te hebben is de gevaarlijkste "
                 "die ontbreekt.",
        severity=BLOKKEREND,
        stap="Zet alsnog een stop op deze positie, of sluit hem. Zolang hij openstaat, "
             "is er geen bodem onder het verlies.",
        check=_check_positie_zonder_stop,
    ),
    Invariant(
        key="koers_verouderd",
        titel="Beleggingsvoorstel rust op een verouderde koers",
        incident="2 aug 2026, bij de bouw: een these van vorige week leest precies zo "
                 "overtuigend als een van vandaag. Dezelfde fout als 'HTTP 200 bewijst "
                 "niets bij een SPA' — het antwoord ziet er goed uit, de werkelijkheid "
                 "erachter is veranderd.",
        severity=BLOKKEREND,
        stap="Wijs het voorstel af en laat de ronde opnieuw draaien op verse koersen "
             "(POST /api/invest/sync-history, daarna POST /api/invest/run).",
        check=_check_koers_verouderd,
    ),
    Invariant(
        key="kas_wijkt_af_van_grootboek",
        titel="Kaspositie klopt niet met het grootboek",
        incident="2 aug 2026, bij de bouw: geleerd van 'afgewezen_maar_live'. Een saldo "
                 "is een bewering van het systeem over zichzelf; het grootboek is de "
                 "enige onafhankelijke bron. Wijken ze af, dan is er geld bewogen buiten "
                 "elke geregistreerde order om.",
        severity=BLOKKEREND,
        stap="Zoek de ontbrekende of dubbele fill in invest_trades. Corrigeer het "
             "grootboek, niet het saldo — het saldo is de afgeleide.",
        check=_check_kas_wijkt_af_van_grootboek,
    ),
    Invariant(
        key="belegging_niet_afgerekend",
        titel="Beleggingsvoorspelling voorbij de horizon, nooit beoordeeld",
        incident="2 aug 2026, bij de bouw: variant op de leerlus die leegdraaide (51 "
                 "lessen, 2 koppelingen). Zonder afrekening bestaat het trackrecord "
                 "alleen uit lopende posities, en dat is per definitie vleiend — de "
                 "verliezers zijn precies wat je zou willen sluiten.",
        severity=STIL,
        stap="Draai de beursronde (POST /api/invest/run); die rekent openstaande "
             "voorspellingen af vóórdat hij nieuwe maakt.",
        check=_check_belegging_niet_afgerekend,
    ),
    Invariant(
        key="rendement_zonder_benchmark",
        titel="Rendement zonder vergelijkbare benchmark",
        incident="2 aug 2026, bij de bouw: het bestaande finance-dagrapport adviseerde "
                 "een €10.000-portefeuille zonder ooit te meten of het iets opleverde. "
                 "+4% klinkt goed tot je weet dat de index +9% deed.",
        severity=STIL,
        stap="Zorg dat er koershistorie is voor de benchmark; de startkoers wordt dan "
             "bij de eerstvolgende ronde alsnog vastgelegd op de startdatum.",
        check=_check_rendement_zonder_benchmark,
    ),
    Invariant(
        key="datafeed_stil",
        titel="Koershistorie wordt niet meer bijgewerkt",
        incident="2 aug 2026, bij de bouw: dezelfde vorm als het Meta-token dat twaalf "
                 "dagen dood was. Er komt geen fout, de tabel is niet leeg, en elke "
                 "berekening blijft antwoorden — op de cijfers van vorige week.",
        severity=STIL,
        stap="Draai POST /api/invest/sync-history en kijk welke tickers falen; een "
             "hernoemd symbool hoort uit invest/universe.py te verdwijnen.",
        check=_check_datafeed_stil,
    ),
    Invariant(
        key="navreeks_incompleet",
        titel="Gaten in de koerslijn van de portefeuille",
        incident="4 aug 2026, bij de bouw van het Beursmeester-dashboard: een dag met een "
                 "onwaardeerbare positie wordt niet vastgelegd (terecht), maar alléén als "
                 "logregel gemeld. Elke risicomaat rekent daarna door over de dagen die "
                 "wél goed gingen — en valt dus stelselmatig te gunstig uit, zonder dat "
                 "iemand ziet dat de reeks lek is.",
        severity=STIL,
        stap="Kijk waarom de NAV op die dagen onvolledig was (meestal een ontbrekende "
             "wisselkoers of koers) en draai POST /api/invest/sync-history; de gaten in "
             "het verleden vullen zich niet vanzelf.",
        check=_check_navreeks_incompleet,
    ),
    Invariant(
        key="voorstel_zonder_backtest",
        titel="Beleggingsvoorstel zonder bewijsstuk",
        incident="2 aug 2026, bij de bouw: de eis dat een idee eerst getoetst wordt, is "
                 "alleen echt zolang iets hem handhaaft. Zonder deze toets zou de "
                 "backtest-eis stilletjes een regel in een document worden — precies "
                 "wat er met 'activiteit is geen effect' tien keer is gebeurd.",
        severity=HYGIENE,
        stap="Zoek de route die de validatie in analyst.valideer() omzeilt; het voorstel "
             "zelf is niet per se fout, de weg ernaartoe wel.",
        check=_check_voorstel_zonder_backtest,
    ),
    Invariant(
        key="effect_meervoudig_geclaimd",
        titel="Eén publicatie, meerdere taken die hem opeisen",
        incident="4 aug 2026: `_stage_to_wachtrij` pakte per publisher-taak de nieuwste "
                 "content-taak zonder bij te houden wat al gestaged was. Gemeten: 21 "
                 "voltooide taken claimden 6 Wachtrij-jobs, en één artikel stond 19× in "
                 "de Wachtrij. De voortgangstelling en de alert-demping steunen op dat "
                 "aantal, dus telde het doel negentien publicaties waar er één was.",
        severity=STIL,
        stap="Open het doel en kijk welke publisher-taken hetzelfde artikel opvoeren; "
             "verwijder de dubbele Wachtrij-jobs vóór ze afzonderlijk goedgekeurd worden.",
        check=_check_effect_meervoudig_geclaimd,
    ),
    Invariant(
        key="uitvoertaak_zonder_uitvoering",
        titel="Uitvoertaak voltooid zonder spoor van uitvoering",
        incident="4 aug 2026: 127 van 1143 voltooide goal-taken openden met plan- of "
                 "instructietaal — 'GSC-data ophalen via hermes-analytics' leverde "
                 "'# Instructie: GSC-data exporteren voor bewaardvoorjou.nl via …' op en "
                 "gold als uitgevoerd. `_find_alternative` zette een gefaalde taak alsnog "
                 "op 'completed' met LLM-proza; de anti-fabricatieregel schreef dat plan "
                 "bewust voor, de fout zat in de boekhouding eromheen.",
        severity=STIL,
        stap="Voer deze stap handmatig uit of laat de taak vervallen — een publisher- of "
             "outreach-taak zonder job-id heeft niets de wereld in gestuurd.",
        check=_check_uitvoertaak_zonder_uitvoering,
    ),
    Invariant(
        key="zelfde_actiepunt_opnieuw",
        titel="Hetzelfde doel keer op keer opnieuw aangemaakt",
        incident="15-17 jul 2026: 'Actiepunt: Verbeter de CTR van WeAreImpact' werd zeven "
                 "keer aangemaakt en strandde zeven keer op 'partial'. De alert dempt "
                 "terecht niet op 'partial', maar de knop eronder maakte elke klik een "
                 "nieuw doel in plaats van naar de vastloper te wijzen. Over de hele "
                 "tabel: 28 Actiepunt-doelen, 14 unieke titels.",
        severity=HYGIENE,
        stap="Open de oudste poging en zoek waaróm hij strandde; verwijder de dubbele "
             "doelen daarna, anders vertekenen ze de uitvoer-pijler.",
        check=_check_zelfde_actiepunt_opnieuw,
    ),
    Invariant(
        key="plan_dubbel_uitgevoerd",
        titel="Doelplanning tweemaal weggeschreven en uitgevoerd",
        incident="4 aug 2026: vijf doelen droegen hun volledige planning dubbel — vier "
                 "fases twee keer, elke taak twee keer, elk met een eigen uitvoering. "
                 "57 taakruns zijn zo twee keer betaald. Onzichtbaar omdat `task_count` "
                 "de plánwaarde bewaart: het doel meldde '26/14', wat als telfout in de "
                 "weergave las in plaats van als dubbel gedaan werk.",
        severity=STIL,
        stap="Open het doel en verwijder de tweede fasereeks; zoek daarna de route die "
             "de planning tweemaal wegschrijft (confirm/start achter elkaar aangeroepen).",
        check=_check_plan_dubbel_uitgevoerd,
    ),
    Invariant(
        key="job_stil_terwijl_de_rest_draait",
        titel="Scheduler-taak vuurt niet meer terwijl de rest doorloopt",
        incident="4 aug 2026: `radar_sky_scan` (elke 4 uur) draaide voor het laatst op "
                 "24 juli — elf dagen eerder — terwijl bridge_sync, calendar_sync en "
                 "goal_autoheal diezelfde ochtend nog vuurden. Geen bestaande toets zag "
                 "het: `scheduler_gaps` meldt alleen jobs met een gevulde `gap_cost` "
                 "(radar veroudert per dag en hoort níét als gemiste run gemeld), en "
                 "'nog nooit geslaagd' vergt een lege `last_ok_at` — die was gevuld. "
                 "Daartussen valt het duurste geval: een taak die ooit werkte en "
                 "stilletjes ophield.",
        severity=STIL,
        stap="Kijk of de job nog in `_SPECS` staat en of zijn trigger nog inplant; een "
             "IntervalTrigger die bij een uitzondering wegvalt komt na een herstart terug.",
        check=_check_job_stil_terwijl_de_rest_draait,
    ),
    Invariant(
        key="doel_voltooid_zonder_taken",
        titel="Doel voltooid zonder ook maar één taak",
        incident="4 aug 2026: twee doelen van Bewaard voor Jou staan op 'completed' met "
                 "nul fases en nul taken ('SEO-blitz: gap-keyword content + "
                 "kennisbank-herstel', 8 jul). Er is niets gepland en niets uitgevoerd, "
                 "en tóch telt het als afgerond werk — inclusief het dempen van de "
                 "dashboard-alerts, want `_goal_addresses` slaat alleen 'partial' en "
                 "'failed' over.",
        severity=STIL,
        stap="Zet het doel terug op 'draft' en laat het opnieuw plannen, of verwijder "
             "het — zolang het op 'completed' staat verbergt het de alert die het zou "
             "moeten oplossen.",
        check=_check_doel_voltooid_zonder_taken,
    ),
    Invariant(
        key="postvak_eigen_verzonden_als_inkomend",
        titel="Eigen verzonden mail staat als binnengekomen post in het postvak",
        incident="11 aug 2026: `sync_inbox` haalde `/me/messages` op (de héle mailbox, "
                 "incl. Verzonden items) en schreef alles weg met folder='inbox'. Vijf "
                 "van de zeven mails onder 'wacht op jouw antwoord' waren door Vincent "
                 "zélf verstuurde linkbuilding-outreach; op één ervan schreef het "
                 "systeem een conceptantwoord op zijn eigen mail.",
        severity=BLOKKEREND,
        stap="Zet deze rijen op folder='sent' (de migratie _migrate_postvak doet dat "
             "voor bestaande mail) en controleer dat de sync `/me/mailFolders/inbox/"
             "messages` gebruikt — een andere scope haalt de verzonden map weer binnen.",
        check=_check_postvak_eigen_verzonden,
    ),
    Invariant(
        key="postvak_regel_zonder_effect",
        titel="Afzenderregel bestaat, maar de mail staat er nog",
        incident="11 aug 2026: de filtering zat in `triage_single`, dus een regel raakte "
                 "alleen mail die daarná binnenkwam. De veertien mails die er al stonden "
                 "bleven staan — precies het moment waarop een filter zijn belofte "
                 "breekt. `rules.add_rule` past nu met terugwerkende kracht toe.",
        severity=STIL,
        stap="Draai de regels opnieuw over het postvak (rules.apply_all()) en zoek uit "
             "welk pad mail binnenhaalt zonder de regels te raadplegen.",
        check=_check_postvak_regel_zonder_effect,
    ),
    Invariant(
        key="postvak_beantwoord_niet_waargenomen",
        titel="Postvak met verkeer waarin nooit een antwoord is waargenomen",
        incident="11 aug 2026: `is_replied` werd alleen gezet door de verstuurknop in "
                 "Impact OS. Alles wat in Outlook zelf beantwoord werd telde nooit mee, "
                 "dus stond er permanent '0% beantwoord (7d)' en kon de achterstand "
                 "alleen groeien — een cijfer dat nooit iets anders kón worden.",
        severity=STIL,
        stap="Controleer of `_sync_sent_items` draait en of de Mail.Read-scope nog geldt; "
             "zonder Verzonden items is elk doorlooptijd-cijfer over mail onbetrouwbaar.",
        check=_check_postvak_beantwoord_niet_waargenomen,
    ),
    Invariant(
        key="postvak_sync_stil",
        titel="Postvak is al uren niet opgehaald",
        incident="11 aug 2026: de laatste sync was van de dag ervóór. Er bestond geen "
                 "scheduler-job voor het eigen postvak (alleen voor de helpdesk-"
                 "mailboxen), dus werd er alleen opgehaald als een mens erom vroeg. Een "
                 "stilstaand postvak ziet er van buiten uit als een rustige dag.",
        severity=STIL,
        stap="Draai POST /api/outlook/sync en controleer de job `outlook_sync` in de "
             "scheduler; blijft hij falen, log dan opnieuw in via de Postvak-tab.",
        check=_check_postvak_sync_stil,
    ),
    Invariant(
        key="postvak_triage_achterstand",
        titel="Triage-achterstand in het postvak loopt niet meer weg",
        incident="11 aug 2026: 106 ongetrieerde mails, en het commando vanaf de telefoon "
                 "triageerde er 15 per keer. De waarschuwing stond daardoor permanent "
                 "aan — en een waarschuwing die altijd aan staat leert een mens hem te "
                 "negeren, juist ook op de dag dat er wél iets is.",
        severity=STIL,
        stap="Draai de triage tot de achterstand leeg is (POST /api/outlook/triage/batch) "
             "en controleer of de LLM-quota-rem actief staat.",
        check=_check_postvak_triage_achterstand,
    ),
    Invariant(
        key="onboarding_onvolledig_maar_actief",
        titel="Site draait al, maar de Iris-intake is niet compleet",
        incident="Preventief (11 aug 2026, Iris-onboarding) — nog geen incident. Dezelfde "
                 "faalvorm bestaat al elders: `artikel_zonder_eigen_bewijs` mat dat 95% van "
                 "de gepubliceerde content generiek was omdat de kwaliteitsgate vorm meet, "
                 "niet herkomst. Een site zonder ingevuld profiel/schrijfstijl heeft "
                 "hetzelfde lot, alleen via een leeg intake-formulier i.p.v. een lege "
                 "kennisbank — deze toets vangt het vóór de eerste publicatie i.p.v. erna.",
        severity=STIL,
        stap="Doorloop de onboarding-wizard voor deze site (bedrijfsdoel + schrijfstijl); "
             "zonder profiel schrijft de contentmotor generieke tekst die niemand als de "
             "stem van dit bedrijf herkent.",
        check=_check_onboarding_onvolledig_maar_actief,
    ),
]


def invariant(key: str) -> Optional[Invariant]:
    return next((i for i in INVARIANTEN if i.key == key), None)


# ── De ronde ───────────────────────────────────────────────────────────────

def _upsert(conn, inv: Invariant, b: Bevinding) -> bool:
    """Werk een bevinding bij of maak hem aan. True = dit is nieuw."""
    bestaand = conn.execute(
        "SELECT id, resolved_at FROM integrity_findings "
        "WHERE invariant = ? AND subject = ?",
        (inv.key, b.subject),
    ).fetchone()
    if bestaand and not bestaand["resolved_at"]:
        conn.execute(
            "UPDATE integrity_findings SET last_seen = datetime('now'), detail = ? "
            "WHERE id = ?",
            (b.detail, bestaand["id"]),
        )
        return False
    if bestaand:
        # Teruggekeerd na herstel: dezelfde rij heropenen zou de geschiedenis
        # ('dit was drie weken weg') uitwissen. Een nieuwe rij, dus zichtbaar
        # als terugval.
        conn.execute("DELETE FROM integrity_findings WHERE id = ?", (bestaand["id"],))
    conn.execute(
        "INSERT INTO integrity_findings "
        "(id, invariant, subject, project, detail, severity, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (str(uuid.uuid4()), inv.key, b.subject, b.project, b.detail, inv.severity),
    )
    return True


def _sluit_verdwenen(conn, inv_key: str, huidige: List[str]) -> List[Dict[str, Any]]:
    """Bevindingen die deze ronde niet meer voorkomen: opgelost.

    Dit is het bewijs-mechanisme. Een bevinding sluit niet omdat iemand zegt dat
    het gefikst is, maar omdat de toets hem niet meer vindt.
    """
    rijen = conn.execute(
        "SELECT id, subject, detail, project, escalated_id, first_seen "
        "FROM integrity_findings WHERE invariant = ? AND resolved_at IS NULL",
        (inv_key,),
    ).fetchall()
    gesloten = []
    for r in rijen:
        if r["subject"] in huidige:
            continue
        conn.execute(
            "UPDATE integrity_findings SET resolved_at = datetime('now') WHERE id = ?",
            (r["id"],),
        )
        gesloten.append(dict(r))
    return gesloten


def _escaleer(inv: Invariant, rijen: List[Dict[str, Any]], dagen_open: int) -> str:
    """Eén kaart voor deze invariant — niet één per geval.

    Negen dode pagina's zijn negen keer hetzelfde besluit ("ruim deze klasse
    op"), geen negen besluiten. Het Actiecentrum is een inbox van beslissingen,
    geen bugtracker; `selfheal` hanteert dezelfde regel als het duplicaten
    opvouwt. De volledige lijst staat op /api/iris/integrity.
    """
    # Eén project noemen als ze allemaal bij hetzelfde horen; anders 'Systeem'.
    projecten = {r.get("project") or "" for r in rijen} - {""}
    project = projecten.pop() if len(projecten) == 1 else "Systeem"
    return log_outcome(
        project=project,
        action="waarheidsaudit",
        detail=_kaarttekst(inv, rijen),
        artifact="/api/iris/integrity",
        next_step=_stap_met_ouderdom(inv, dagen_open),
        status="error",
    )


def _kaarttekst(inv: Invariant, rijen: List[Dict[str, Any]]) -> str:
    voorbeelden = "; ".join(r["detail"][:90] for r in rijen[:3])
    meer = f" (+{len(rijen) - 3} meer)" if len(rijen) > 3 else ""
    return f"{inv.titel} — {len(rijen)} geval(len): {voorbeelden}{meer}"


def _ververs_kaart(kaart_id: Optional[str], inv: Invariant,
                   open_rijen: List[Dict[str, Any]], dagen_open: int) -> None:
    """Laat een openstaande kaart zeggen wat er nú nog open staat.

    De kaart gaat over de klasse en blijft dus staan zolang er één geval over
    is — maar zijn tekst was een momentopname van de dag dat hij ontstond. Zakt
    het aantal van negen naar vier omdat er vijf pagina's offline zijn gehaald,
    dan hoort dat op de kaart te staan; anders vraagt hij om werk dat al gedaan
    is, en dat is precies hoe een inbox zijn geloofwaardigheid verliest.
    """
    if not kaart_id or not open_rijen:
        return
    tekst = _kaarttekst(inv, open_rijen)
    stap = _stap_met_ouderdom(inv, dagen_open)
    with get_conn() as conn:
        rij = conn.execute("SELECT detail, next_step FROM activity_log WHERE id = ?",
                           (kaart_id,)).fetchone()
        if not rij or (rij["detail"] == tekst and rij["next_step"] == stap):
            return
        conn.execute("UPDATE activity_log SET detail = ?, next_step = ? WHERE id = ?",
                     (tekst, stap, kaart_id))


def _stap_met_ouderdom(inv: Invariant, dagen_open: int) -> str:
    if dagen_open < _VERGRIJSD_DAGEN:
        return inv.stap
    return (f"[staat al {dagen_open} dagen open] {inv.stap} "
            f"Los het op óf wijs de bevinding af — een kaart die blijft staan, "
            f"leest niemand nog.")


def _sluit_kaarten(resultaat: Dict[str, Any]) -> None:
    """Sluit kaarten waarvan élke onderliggende bevinding is opgelost.

    Zolang er nog één geval openstaat blijft de kaart staan: hij gaat over de
    klasse, niet over het individuele geval. Pas als de toets er geen enkele
    meer vindt, is de klasse aantoonbaar weg — en dán mag de kaart dicht, met
    een uitkomstkaart die vertelt wat er is opgelost.
    """
    with get_conn() as conn:
        kaarten = [dict(r) for r in conn.execute(
            "SELECT escalated_id, invariant, COUNT(*) AS opgelost "
            "FROM integrity_findings "
            "WHERE escalated_id IS NOT NULL AND resolved_at IS NOT NULL "
            "GROUP BY escalated_id, invariant"
        )]
    for k in kaarten:
        with get_conn() as conn:
            nog_open = conn.execute(
                "SELECT COUNT(*) FROM integrity_findings "
                "WHERE escalated_id = ? AND resolved_at IS NULL",
                (k["escalated_id"],),
            ).fetchone()[0]
            al_gesloten = conn.execute(
                "SELECT 1 FROM inbox_dismissals WHERE kind = 'error' AND ref_id = ?",
                (k["escalated_id"],),
            ).fetchone()
        if nog_open or al_gesloten:
            continue
        inv = invariant(k["invariant"])
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO inbox_dismissals (kind, ref_id, dismissed_at) "
                "VALUES ('error', ?, datetime('now'))",
                (k["escalated_id"],),
            )
        log_outcome(
            project="Systeem", action="waarheidsaudit",
            detail=(f"Opgelost: {inv.titel if inv else k['invariant']} — "
                    f"{k['opgelost']} geval(len) weg, de toets vindt er geen meer"),
            artifact="/api/iris/integrity",
            status="ok",
        )
        resultaat["kaarten_gesloten"] = resultaat.get("kaarten_gesloten", 0) + 1


def _dagen_sinds(iso: str) -> int:
    if not iso:
        return 0
    with get_conn() as conn:
        rij = conn.execute(
            "SELECT CAST(julianday('now') - julianday(?) AS INTEGER) AS d", (iso,)
        ).fetchone()
    return int(rij["d"] or 0)


def run_audit(*, source: str = "scheduler") -> Dict[str, Any]:
    """Draai alle invarianten, werk de levensloop bij, escaleer wat moet.

    Een invariant die zélf stukgaat mag de audit niet platleggen — dan zou één
    kapotte check alle andere onzichtbaar maken, en dat is exact het probleem
    dat dit bestand bestrijdt. Hij wordt geteld als 'mislukt' en gemeld.
    """
    resultaat: Dict[str, Any] = {
        "source": source, "nieuw": 0, "opgelost": 0, "open": 0,
        "geescaleerd": 0, "kaarten_gesloten": 0, "mislukt": [], "per_invariant": {},
    }

    for inv in INVARIANTEN:
        try:
            bevindingen = inv.check() or []
        except sqlite3.OperationalError as e:
            # "no such table" op een verse installatie betekent: dit domein is
            # nog nooit gebruikt, dus er valt niets te toetsen. Dát is geen
            # storing. Elke ándere SQL-fout wél — een kolom die niet meer
            # bestaat maakt een invariant blind, en een blinde toets die zwijgt
            # is precies waar dit bestand tegen bestaat.
            if "no such table" in str(e).lower():
                logger.debug("[waarheidsaudit] '%s' overgeslagen: %s", inv.key, e)
                continue
            logger.exception("[waarheidsaudit] Invariant '%s' faalde", inv.key)
            resultaat["mislukt"].append({"invariant": inv.key, "fout": f"OperationalError: {e}"})
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception("[waarheidsaudit] Invariant '%s' faalde", inv.key)
            resultaat["mislukt"].append({"invariant": inv.key, "fout": f"{type(e).__name__}: {e}"})
            continue

        subjects = [b.subject for b in bevindingen]
        with get_conn() as conn:
            for b in bevindingen[:_MAX_GEVALLEN_PER_INVARIANT]:
                if _upsert(conn, inv, b):
                    resultaat["nieuw"] += 1
            gesloten = _sluit_verdwenen(conn, inv.key, subjects)

        resultaat["opgelost"] += len(gesloten)

        resultaat["per_invariant"][inv.key] = {
            "titel": inv.titel, "severity": inv.severity,
            "gevonden": len(bevindingen), "getoond": min(len(bevindingen),
                                                         _MAX_GEVALLEN_PER_INVARIANT),
        }

    # Escalatie in één pas over alles wat openstaat, gegroepeerd per invariant:
    # zo geldt dezelfde regel voor elke invariant, kan een check hem niet per
    # ongeluk overslaan, en wordt één klasse nooit meer dan één kaart.
    for inv in INVARIANTEN:
        if inv.severity == HYGIENE:
            continue
        with get_conn() as conn:
            open_rijen = [dict(r) for r in conn.execute(
                "SELECT * FROM integrity_findings "
                "WHERE invariant = ? AND resolved_at IS NULL ORDER BY first_seen",
                (inv.key,),
            )]
        if not open_rijen:
            continue
        nieuw = [r for r in open_rijen if not r["escalated_id"]]
        # Staat er al een kaart voor deze klasse? Hang de nieuwe gevallen daar
        # onder in plaats van een tweede kaart te maken voor hetzelfde probleem.
        bestaand = next((r["escalated_id"] for r in open_rijen if r["escalated_id"]), None)
        dagen = _dagen_sinds(open_rijen[0]["first_seen"])
        if not nieuw:
            # Alles wat openstaat hangt al aan een kaart — maar die kaart is
            # geschreven op de dag dat hij ontstond en telt sindsdien mee wat er
            # tóen open stond. Op 2 aug 2026 bleef 'afgewezen_maar_live' om negen
            # pagina's vragen nadat er vijf waren opgelost. Een kaart die om werk
            # vraagt dat al gedaan is, leert de lezer om kaarten te wantrouwen.
            _ververs_kaart(bestaand, inv, open_rijen, dagen)
            continue

        if bestaand is None and inv.severity == STIL and dagen < _STIL_ESCALATIE_DAGEN:
            # Een mechanisme dat morgen vanzelf weer aanslaat (de weekscan draait
            # maandag) is geen storing maar een moment in de cyclus.
            continue

        kaart_id = bestaand or _escaleer(inv, open_rijen, dagen)
        with get_conn() as conn:
            for r in nieuw:
                conn.execute(
                    "UPDATE integrity_findings SET escalated_id = ? WHERE id = ?",
                    (kaart_id, r["id"]))
        if bestaand is None:
            resultaat["geescaleerd"] += 1

    _sluit_kaarten(resultaat)

    with get_conn() as conn:
        resultaat["open"] = conn.execute(
            "SELECT COUNT(*) FROM integrity_findings WHERE resolved_at IS NULL"
        ).fetchone()[0]

    if resultaat["mislukt"]:
        log_outcome(
            project="Systeem", action="waarheidsaudit",
            detail=(f"{len(resultaat['mislukt'])} invariant(en) konden niet draaien: "
                    + ", ".join(m["invariant"] for m in resultaat["mislukt"])),
            artifact="/api/iris/integrity",
            next_step="Een toets die zelf stuk is, meet niets — bekijk de fout in logs/impactos.log.",
            status="error",
        )

    logger.info("[waarheidsaudit] %d nieuw, %d opgelost, %d open, %d geëscaleerd",
                resultaat["nieuw"], resultaat["opgelost"], resultaat["open"],
                resultaat["geescaleerd"])
    return resultaat


# ── Uitlezen ───────────────────────────────────────────────────────────────

def open_findings(severity: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    sql = ("SELECT * FROM integrity_findings WHERE resolved_at IS NULL")
    params: List[Any] = []
    if severity:
        sql += " AND severity = ?"
        params.append(severity)
    sql += " ORDER BY CASE severity WHEN 'blokkerend' THEN 0 WHEN 'stil' THEN 1 " \
           "ELSE 2 END, first_seen LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def audit_summary() -> Dict[str, Any]:
    """Compacte stand voor de UI en voor Iris' prompt."""
    with get_conn() as conn:
        per = [dict(r) for r in conn.execute(
            "SELECT invariant, severity, COUNT(*) AS n, MIN(first_seen) AS oudste "
            "FROM integrity_findings WHERE resolved_at IS NULL "
            "GROUP BY invariant, severity"
        )]
        opgelost_7d = conn.execute(
            "SELECT COUNT(*) FROM integrity_findings "
            "WHERE resolved_at >= datetime('now', '-7 day')"
        ).fetchone()[0]
    for p in per:
        inv = invariant(p["invariant"])
        p["titel"] = inv.titel if inv else p["invariant"]
        p["dagen_open"] = _dagen_sinds(p["oudste"])
    per.sort(key=lambda p: ({BLOKKEREND: 0, STIL: 1}.get(p["severity"], 2),
                            -p["dagen_open"]))
    return {
        "open_totaal": sum(p["n"] for p in per),
        "blokkerend": sum(p["n"] for p in per if p["severity"] == BLOKKEREND),
        "stil": sum(p["n"] for p in per if p["severity"] == STIL),
        "hygiene": sum(p["n"] for p in per if p["severity"] == HYGIENE),
        "opgelost_7d": opgelost_7d,
        "per_invariant": per,
        "invarianten_totaal": len(INVARIANTEN),
    }


def invariant_voor_kaart(kaart_id: str, detail: str = "") -> Optional[Invariant]:
    """Welke invariant hoort bij deze `waarheidsaudit`-kaart in het Actiecentrum?

    Nodig omdat de kaart zelf alleen tekst draagt: `action='waarheidsaudit'` en
    een samenvatting. Wie er iets mee wil doen (de "Analyseer & fix"-knop, een
    remedie) moet weten wélke toets hem maakte — en dat mag geen gok zijn.

    De harde koppeling is `integrity_findings.escalated_id`: die wijst naar de
    kaart die deze klasse escaleerde. Alleen als die weg is (opgeruimde
    bevindingen, oude kaart) valt hij terug op de titel, want `_kaarttekst`
    begint altijd met `inv.titel` — een terugval, geen tweede waarheid.
    """
    if kaart_id:
        try:
            with get_conn() as conn:
                rij = conn.execute(
                    "SELECT invariant FROM integrity_findings WHERE escalated_id = ? "
                    "ORDER BY resolved_at IS NOT NULL, last_seen DESC LIMIT 1",
                    (kaart_id,),
                ).fetchone()
            if rij and rij["invariant"]:
                for inv in INVARIANTEN:
                    if inv.key == rij["invariant"]:
                        return inv
        except sqlite3.OperationalError:
            pass
    tekst = (detail or "").strip().lower()
    if tekst:
        for inv in INVARIANTEN:
            if tekst.startswith(inv.titel.lower()[:60]):
                return inv
    return None


def prompt_block() -> str:
    """Wat Iris in haar briefing te zien krijgt.

    Bewust géén kale JSON-dump: de ernst-klasse en de leeftijd zijn de twee
    dingen waar haar oordeel op moet steunen, en die moeten in woorden staan.
    """
    s = audit_summary()
    if not s["open_totaal"]:
        return (f"Waarheidsaudit: alle {s['invarianten_totaal']} invarianten schoon. "
                f"({s['opgelost_7d']} bevinding(en) opgelost in de afgelopen 7 dagen.)")
    regels = [
        f"Waarheidsaudit over {s['invarianten_totaal']} invarianten — "
        f"{s['open_totaal']} openstaande bevinding(en): "
        f"{s['blokkerend']} blokkerend, {s['stil']} stil, {s['hygiene']} hygiëne.",
        "",
        "Een 'blokkerende' bevinding betekent dat er NU iets verkeerds naar buiten staat "
        "(een dode URL, twee artikelen op één zoekwoord). Die weegt zwaarder dan welke "
        "optimalisatie ook: eerst stoppen met schade doen, dan pas groeien. Een 'stille' "
        "bevinding betekent dat een mechanisme dat hoort te werken niets doet — dat is "
        "geen cosmetiek, want alle cijfers die erop steunen zijn dan onbetrouwbaar.",
        "",
    ]
    for p in s["per_invariant"]:
        leeftijd = f", oudste staat {p['dagen_open']} dag(en) open" if p["dagen_open"] else ""
        regels.append(f"- [{p['severity']}] {p['titel']}: {p['n']} geval(len){leeftijd}")
    return "\n".join(regels)
