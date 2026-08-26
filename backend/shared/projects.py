"""Eén antwoord op de vraag "is dit hetzelfde project?".

Aanleiding (4 aug 2026): de tabel `goals` bevatte zeventien projectwaarden voor
twaalf projecten — `WeAreImpact` (32 doelen) naast `weareimpact` (1),
`Bewaard voor Jou` (11) naast `Bewaardvoorjou` (9), `TeambuildingMetImpact` (4)
naast `teambuildingmetimpact` (5). Dat is exact dezelfde storing die
`radar/models.py` in juli al voor de watchlist opruimde: een lijst die exact op
tekst matcht laat de helft van de historie onbereikbaar achter. Zolang de
Doelen-tab zijn filter niet meestuurde viel dat niet op; zodra hij dat wél doet
(zelfde datum) wordt het acuut — dan kiest de filter één van beide spellingen en
verdwijnt de andere helft uit beeld.

De techniek is dezelfde als `seo/opportunity_quality.squash`, de vráág is een
andere: daar "is dit hetzelfde zoekwoord", hier "is dit hetzelfde project". Ze
delen daarom bewust geen code — een toekomstige afstelling van de
zoekwoord-normalisatie (stopwoorden, samenstellingen) mag geen projecten
samenvoegen of uit elkaar trekken.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def squash_project(name: str) -> str:
    """Kleinletter, accentloos, zonder spaties en leestekens.

    'Bewaard voor Jou', 'Bewaardvoorjou' en 'bewaard-voor-jou' zijn hetzelfde
    project; alleen zó is dat te zien. Spaties weglaten is hier geen detail maar
    de kern: Nederlandse merknamen worden even vaak aaneen als los geschreven,
    en beide vormen zijn ooit ergens ingetypt.
    """
    decomposed = unicodedata.normalize("NFKD", (name or "").strip().lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped)


def canonical_project(name: str, known: Optional[Iterable[str]] = None) -> str:
    """Geef de spelling terug die dit project in `sites` draagt.

    De sites-tabel is de bron van waarheid voor hoe een project heet — dat is de
    naam die op het dashboard staat. Kent `sites` het project niet (interne
    projecten als 'Systeem', losse experimenten), dan blijft de naam ongemoeid:
    raden is hier erger dan niets doen.
    """
    schoon = (name or "").strip()
    if not schoon:
        return schoon
    kaart = _site_namen() if known is None else {
        squash_project(k): k for k in known if (k or "").strip()
    }
    return kaart.get(squash_project(schoon), schoon)


def _site_namen() -> Dict[str, str]:
    """Squash → officiële spelling, uit de sites-tabel.

    Bewust niet gecachet: sites veranderen zelden maar wél, en deze functie
    draait op menselijke schaal (een tabweergave, een migratie), niet in een lus.
    """
    from .database import get_conn
    try:
        with get_conn() as conn:
            rijen = conn.execute("SELECT name FROM sites").fetchall()
    except Exception:  # verse installatie of migratie-in-uitvoering
        return {}
    return {squash_project(r["name"]): r["name"] for r in rijen if (r["name"] or "").strip()}


def merge_project_column(table: str, column: str = "project") -> int:
    """Trek de projectkolom van `table` recht naar de sites-spelling.

    Idempotent: een tweede run raakt niets meer aan. Retourneert het aantal
    bijgewerkte rijen, zodat een migratie kan loggen wát hij heeft opgeruimd in
    plaats van stil te slagen.
    """
    from .database import get_conn
    kaart = _site_namen()
    if not kaart:
        return 0
    bijgewerkt = 0
    with get_conn() as conn:
        try:
            waarden = [r[0] for r in conn.execute(
                f"SELECT DISTINCT {column} FROM {table} WHERE COALESCE({column}, '') != ''"
            ).fetchall()]
        except Exception:
            return 0
        for waarde in waarden:
            juist = kaart.get(squash_project(waarde))
            if not juist or juist == waarde:
                continue
            cur = conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (juist, waarde)
            )
            bijgewerkt += cur.rowcount
            logger.info("projectnaam rechtgetrokken in %s: %r → %r (%d rijen)",
                        table, waarde, juist, cur.rowcount)
        conn.commit()
    return bijgewerkt


def visible_projects_filter() -> Optional[set]:
    """Squash-vormen uit BRIDGE_VISIBLE_PROJECTS, of None (= alles tonen).

    Filtert wat Iris Remote toont (projectpaneel, inboxkaarten, SEO-pulse) —
    bewust NIET voor WhatsApp-klantgesprekken (Vincent bedient elke klant via
    hetzelfde nummer) en niet voor de onboardingwizard (die moet een nieuw
    project juist kunnen tonen)."""
    from .config import BRIDGE_VISIBLE_PROJECTS
    ruw = [p.strip() for p in BRIDGE_VISIBLE_PROJECTS.split(",") if p.strip()]
    return {squash_project(p) for p in ruw} if ruw else None


def project_visible(name: Optional[str]) -> bool:
    """True als `name` in BRIDGE_VISIBLE_PROJECTS staat (of die leeg is)."""
    toegestaan = visible_projects_filter()
    if toegestaan is None:
        return True
    if not name:
        # Items zonder project (scheduler-fouten, systeembrede audits) zijn
        # geen projectkeuze — die horen hoe dan ook in de inbox.
        return True
    return squash_project(name) in toegestaan


def filter_cross_project_mentions(
    items: Iterable[Dict], text_keys: Iterable[str] = ("actie", "waarom")
) -> List[Dict]:
    """Filtert Iris' knelpunten/advies (systeembreed berekend, over alle
    projecten heen) op wat `BRIDGE_VISIBLE_PROJECTS` toestaat.

    Deze items dragen zelf vaak geen `project`-veld (een scheduler-storing of
    een lijst vastgelopen doelen noemt meerdere projecten in de tekst) — een
    filter die alleen op een `project`-kolom let laat die dus door. Bij een
    expliciete scope (`project` of `suggestion.scope`/`.target`) telt die;
    anders wordt de tekst afgezet tegen de namen van de projecten die niet
    zichtbaar mogen zijn, en sneuvelt het item zodra zo'n naam erin voorkomt."""
    allowed = visible_projects_filter()
    if allowed is None:
        return list(items)
    other_names = [n for n in _site_namen().values() if squash_project(n) not in allowed]
    out: List[Dict] = []
    for it in items:
        suggestion = it.get("suggestion") or {}
        scope = it.get("project") or suggestion.get("scope") or suggestion.get("target")
        if scope:
            if squash_project(str(scope)) in allowed:
                out.append(it)
            continue
        text = " ".join(str(it.get(k) or "") for k in text_keys).lower()
        if any(n and n.lower() in text for n in other_names):
            continue
        out.append(it)
    return out


def project_varianten(name: str) -> List[str]:
    """Alle spellingen waaronder dit project ooit is opgeslagen.

    Voor lees-queries die de historie compleet moeten tonen zonder dat de
    migratie al gedraaid hoeft te zijn.
    """
    from .database import get_conn
    doel = squash_project(name)
    if not doel:
        return []
    gevonden = {name}
    for tabel, kolom in (("goals", "project"), ("sites", "name")):
        try:
            with get_conn() as conn:
                rijen = conn.execute(
                    f"SELECT DISTINCT {kolom} AS v FROM {tabel} "
                    f"WHERE COALESCE({kolom}, '') != ''"
                ).fetchall()
        except Exception:
            continue
        gevonden.update(r["v"] for r in rijen if squash_project(r["v"]) == doel)
    return sorted(gevonden)
