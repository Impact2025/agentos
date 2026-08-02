"""Vul lege `sites.profile`-velden met een feitelijk concept uit de site zelf.

Achtergrond (27 juli 2026): 7 van de 12 sites hadden een leeg profiel. Dat is
niet cosmetisch — `cold_start_opportunities` weigert bij een profiel korter dan
40 tekens te draaien ("zonder profiel wordt keyword-onderzoek giswerk"), dus een
site zónder GSC-rankings én zonder profiel komt nooit aan content toe.

Waarom deze profielen kort en feitelijk zijn: de bestaande profielen (Steentje,
Skillkaart, Daar) bevatten Vincents eigen strategie — prijzen, merkgrenzen,
wetenschappelijke onderbouwing. Dat valt niet af te leiden uit een homepage, en
het verzinnen ervan zou de contentmotor met valse stelligheid voeden. Wat hier
staat komt uit de eigen title/meta-description/homepage van elke site: wát het
product is, voor wie, en welke belofte de site zelf doet. Genoeg om
keyword-onderzoek te gronden, expliciet niet genoeg om als merkstrategie door te
gaan — vandaar de CONCEPT-markering in elke tekst.

Gebruik:
    .venv/Scripts/python.exe scripts/seed_site_profiles.py         # tonen
    .venv/Scripts/python.exe scripts/seed_site_profiles.py --fix   # wegschrijven
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.shared.database import get_conn  # noqa: E402

_MARKER = ("[CONCEPT — samengevat uit de eigen sitetekst op 27-07-2026. Vul aan "
           "met positionering, prijzen, merkgrenzen en tone of voice; "
           "cold-start keyword-onderzoek gebruikt deze tekst als bron.]")

PROFIELEN: dict[str, str] = {
    "Bewaard voor Jou": (
        "BewaardVoorJou (bewaardvoorjou.nl) — je levensverhaal vastleggen met "
        "een empathische AI-interviewer. Geen schrijfervaring nodig; het "
        "resultaat is veilig te delen met familie. Gratis te starten.\n\n"
        "Doelgroep: mensen die hun eigen of andermans levensverhaal willen "
        "bewaren (ouderen, kinderen en kleinkinderen van ouderen), plus de "
        "zorg- en welzijnssector (reminiscentie als activiteit).\n\n"
        "Sitestructuur: /kennisbank (achtergrondartikelen, de best presterende "
        "sectie), /blog, en losse landingspagina's per gelegenheid "
        "(kraamcadeau, mijlpaal-cadeau, levensverhaal-opschrijven)."
    ),
    "Bijeen": (
        "Bijeen (bijeen.app) — het eerste eventplatform gebouwd voor de "
        "welzijnssector, samen met welzijnsprofessionals ontwikkeld. Van "
        "aanmelding tot WMO-rapportage: organiseren, deelnemers beheren en "
        "meten wat je bereikt.\n\n"
        "Doelgroep: welzijnsorganisaties, gemeenten en buurtinitiatieven die "
        "evenementen organiseren (vrijwilligersdag, buurtfestival) en hun "
        "impact moeten verantwoorden.\n\n"
        "Belofte: 'evenementen die écht verbinding maken' — organiseren met "
        "impact, meten wat je bereikt. Gratis proefperiode, geen creditcard."
    ),
    "DatingAssistent": (
        "DatingAssistent (datingassistent.nl) — AI-gedreven datingcoaching: "
        "profieloptimalisatie, communicatievaardigheden en emotionele groei. "
        "Nederlands product, 10+ jaar ervaring, privacy als kernbelofte.\n\n"
        "Kernboodschap: 'Daten is geen geluk. Het is een patroon.' Instap via "
        "een gratis quiz van 2 minuten (10 vragen, resultaat per e-mail).\n\n"
        "Doelgroep: moderne singles die vastlopen in match-droogte of steeds "
        "op hetzelfde type partner uitkomen.\n\n"
        "Sitestructuur: /blog en /kennisbank naast prijzen en over-ons."
    ),
    "Ictusgo": (
        "IctusGo (ictusgo.nl) — GPS-gestuurde outdoor teambuilding met echte "
        "sociale impact. Combineert een GPS-avontuur met sociale missies in de "
        "buurt, in 5 varianten.\n\n"
        "Doelgroep: bedrijven, gezinnen, scholen en voetbalclubs.\n\n"
        "Onderscheidend: de Geluksmomenten Score — verbinding, betekenis, "
        "plezier en groei worden gemeten in plaats van aangenomen.\n\n"
        "Sterke lokale component: aparte landingspagina's voor Hoofddorp, "
        "Haarlemmermeer en Schiphol. Sitestructuur: /tochten, /impact, "
        "/kennisbank, /blog."
    ),
    "Pootgelukkig": (
        "PootGelukkig (pootgelukkig.nl) — slimme matching voor asieldieren: "
        "koppelt mens en dier op basis van leefstijl in plaats van toeval. "
        "Gratis voor adoptanten; een initiatief van WeAreImpact.\n\n"
        "Belangrijke grens: het asiel beslist, altijd. PootGelukkig adviseert "
        "en versnelt, het neemt de beslissing niet over.\n\n"
        "Twee doelgroepen met eigen taal: asiels (sneller een goede match, "
        "minder mismatches en retouren) en adoptanten (een dier dat bij je "
        "leven past).\n\n"
        "Sitestructuur: /werkwijze, /voor-asiels, /kennisbank, /blog, plus "
        "'Dr. Poot'."
    ),
    "TeambuildingMetImpact": (
        "Teambuilding met Impact (teambuildingmetimpact.nl) — betekenisvolle "
        "teambuilding met LEGO® Serious Play: teamontwikkeling gecombineerd "
        "met maatschappelijke betekenis.\n\n"
        "Belofte: 'Samen bouwen aan sterke teams én een betere wereld.' Geen "
        "standaard uitje, maar een teamdag die iets nalaat.\n\n"
        "Doelgroep: organisaties die een teamdag willen die verder gaat dan "
        "gezelligheid.\n\n"
        "Verwant aan IctusGo (zusterinitiatief, wordt op de site genoemd) — "
        "let bij content op dat de twee elkaar aanvullen en niet op dezelfde "
        "zoekwoorden concurreren."
    ),
    "Vrijwilligersmatch": (
        "Vrijwilligersmatch (vrijwilligersmatch.nl) — matchingplatform dat "
        "vrijwilligers en organisaties verbindt via swipen: gekoppeld op "
        "vaardigheden, interesses en beschikbaarheid.\n\n"
        "Belofte: 'Swipe je naar impact' — vrijwilligerswerk dat bij je past, "
        "bij jou in de buurt.\n\n"
        "Twee doelgroepen: vrijwilligers (een plek die past bij wie je bent) "
        "en organisaties (sneller de juiste vrijwilliger vinden en behouden).\n\n"
        "Sitestructuur: /hoe-het-werkt, /organisaties, /impact, "
        "/vrijwilligerswerk, /kennisbank, /blog."
    ),
    "WeAreImpact": (
        "WeAreImpact (weareimpact.nl) — Vincent van Munster, AI-consultant "
        "voor het sociaal domein: welzijnsorganisaties, gemeenten en sociaal "
        "ondernemers. Beschikbaar voor interim in Amsterdam / Haarlem / "
        "Leiden.\n\n"
        "Positionering: 'Ik verbind mensen, teams en technologie.' "
        "Onderscheidend: géén rapport-en-wegwezen, 15+ jaar ervaring in het "
        "sociaal domein, LEGO® Serious Play-facilitator.\n\n"
        "Conversiepunt: gratis kennismakingsgesprek; daarnaast een AI-scan en "
        "een impactcalculator als instapinstrumenten.\n\n"
        "Dit is een persoonlijk merk — schrijf vanuit Vincent als vakmens, "
        "niet als anoniem bureau."
    ),
}


def main(fix: bool = False) -> int:
    geraakt = 0
    with get_conn() as conn:
        rijen = {r["name"]: dict(r) for r in conn.execute(
            "SELECT id, name, profile FROM sites")}

    for naam, tekst in PROFIELEN.items():
        site = rijen.get(naam)
        if not site:
            print(f"  ?  {naam}: niet in de database")
            continue
        huidig = (site.get("profile") or "").strip()
        if len(huidig) >= 40:
            print(f"  =  {naam}: heeft al een profiel ({len(huidig)} tekens) — overgeslagen")
            continue
        volledig = f"{tekst}\n\n{_MARKER}"
        geraakt += 1
        print(f"  +  {naam}: {len(volledig)} tekens")
        if fix:
            with get_conn() as conn:
                conn.execute("UPDATE sites SET profile = ? WHERE id = ?",
                             (volledig, site["id"]))

    print(f"\n{geraakt} profielen {'weggeschreven' if fix else 'klaar om weg te schrijven'}.")
    if not fix:
        print("(draai met --fix om ze op te slaan)")
    return geraakt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    main(args.fix)
