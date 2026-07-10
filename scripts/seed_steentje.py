"""Configureer de SteentjeAPP-agent in Agent OS als een pro.

- Vul de `sites`-rij (Steentjebij Steentje) met profile + ctas, zet auto_content aan.
- Zaai de Mission Radar watchlist (keywords + concurrenten + RSS).
- Zaai case_studies (wetenschappelijke bewijslast uit de onderzoeks-PDF's).
- (Optioneel) draai één radar-scan om signalen te seeden.

Veilig: publiceren blijft achter de Wachtrij-gate; publish_api_url is leeg,
dus er komt niets live zonder Vincent's goedkeuring.
"""
from __future__ import annotations
import asyncio, json, sqlite3, sys
from pathlib import Path

AGENTOS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTOS))
from backend.shared.database import get_conn  # noqa

SITE_ID = "686629e7-ab3e-45ed-9a6a-d10753d66fb6"
PROJECT = "steentjebijsteentje"

PROFILE = """SteentjeAPP (steentjebijsteentje.nl) - relatieverdieping voor koppels via de LEGO Serious Play-methode, thuis. Kernwaarde: "Bouwen wat woorden niet kunnen zeggen." Product: de Ritual Box (EUR 279 of 4x EUR 69,75) - fysiek bouwpakket + begeleide app/methode.

Doelgroep: koppels in een langdurige relatie die communicatie willen verdiepen, vaste praatpatronen willen doorbreken, of extra belasting ervaren (mentale last, transitie naar ouderschap).

Tone of voice: warm, toegankelijk, optimistisch met Nederlandse directheid. Lichter dan therapie, wel met psychologische diepgang. Poetisch waar het kan, praktisch waar het moet.

Belangrijke grens: coaching/zelfhulp, GEEN therapie. Nooit klinisch of medisch als behandeling framen.

Wetenschappelijke fundering (gebruik als E-E-A-T-bewijs): Daminger 4-fasenmodel mentale last (Anticipatie, Identificatie, Besluitvorming, Monitorisatie); Gottman/oxytocine-postpartum-data (67-75% daling relatietevredenheid, 40% stijging echtscheidingsrisico in 4 jaar); Nyman-Salonen 97% non-verbale synchronie in relatietherapie; Harn & Peabody LEGO-stress-pilot (t=2,65, p<0,05 angst-daling); Brauer/Proyer/Chick 2021 adult playfulness.

Schrijf vanuit de eerste persoon als Vincent van Munster (oprichter WeAreImpact, 25+ jaar bestuur/innovatie, ex-directeur Stichting de Baan - altijd verleden tijd). Bruggenbouwer tussen zorg/welzijn en innovatie/tech. Altijd actief taalgebruik. Sentence case tussenkoppen."""

CTAS = [
    "Plan een gratis verkenningssessie op steentjebijsteentje.nl/plan-sessie",
    "Ontdek de Ritual Box op steentjebijsteentje.nl/de-ritual-box",
    "Bekijk het traject op steentjebijsteentje.nl/het-traject",
    "Vraag een strategische verkenning aan via weareimpact.nl",
]

WATCH = [
    ("keyword", "Mentale last verdelen", "mentale last verdelen"),
    ("keyword", "Relatie verdiepen", "relatie verdiepen"),
    ("keyword", "Communicatie in relatie verbeteren", "communicatie in relatie verbeteren"),
    ("keyword", "Praten helpt niet meer", "praten helpt niet meer"),
    ("keyword", "Ouderschap en relatie", "ouderschap en relatie"),
    ("keyword", "LEGO Serious Play koppels", "LEGO Serious Play koppels"),
    ("keyword", "Speelsheid relatie", "speelsheid relatie"),
    ("keyword", "Phubbing relatie", "phubbing relatie"),
    ("keyword", "Vastgeroeste patronen relatie", "vastgeroeste patronen relatie"),
    ("keyword", "Relatietherapie alternatief", "relatietherapie alternatief"),
    ("keyword", "Quality time als koppel", "quality time als koppel"),
    ("keyword", "Samen bouwen relatie", "samen bouwen relatie"),
    ("competitor", "Concurrent: Gottman", "gottman.com"),
    ("competitor", "Concurrent: The School of Life", "theschooloflife.com"),
    ("rss", "RSS: NEMO Kennislink", "https://www.nemokennislink.nl/feed/"),
]

CASE_STUDIES = [
    ("De 4 fasen van mentale last (Daminger-model)",
     "Allison Damingers kader voor cognitieve huishoudelijke arbeid: Anticipatie, Identificatie, Besluitvorming, Monitorisatie. Verklaart waarom 'vragen om hulp' geen oplossing is (de Manager-Trap).",
     "Socioloog Allison Daminger onderscheidt vier fasen waarin mentale last zich manifesteert: het vooruitkijken (anticipatie), het onderzoeken van opties (identificatie), het kiezen (besluitvorming) en het blijven volgen (monitorisatie). De 'Manager-Trap' ontstaat wanneer de ene partner de default-manager wordt en de ander reactief assisteert. Delegeren is zelf ook cognitieve arbeid.",
     "mentale last, daminger, cognitieve arbeid, manager-trap, huishouden",
     "https://www.allisondaminger.com/"),
    ("De roze wolk is een mythe (Gottman / postpartum-data)",
     "Longitudinaal onderzoek: 67-75% van de koppels rapporteert daling relatietevredenheid binnen 3 jaar na de baby; 40% stijging echtscheidingsrisico in 4 jaar; seksuele frequentie daalt gemiddeld 50% in jaar 1 (oxytocine-paradox).",
     "De transitie naar ouderschap is een 'identiteits-aardbeving'. Gottman-data en meta-analyses (Bogdan et al., 2022) tonen dat slechts 33% van de koppels de dyadische stabiliteit behoudt. De oxytocine-paradox: borstvoeding/hechting richten oxytocine op de baby, waardoor de partner onbewust wordt geexcludeerd.",
     "ouderschap, postpartum, relatietevredenheid, gottman, oxytocine",
     "https://www.gottman.com/"),
    ("Bouwen verlaagt stress (Harn & Peabody LEGO-pilot)",
     "Pilotstudie LEGO-based Workplace Stress Reduction (150 min, 7 deelnemers): deelnemers bouwden hun 'stressfiguur'. Angst-subscale daalde significant (t=2,65, p<0,05); deelnemers rapporteerden 'healing power' en diepere reflectie.",
     "Harn en Peabody vinden in LEGO SERIOUS PLAY-toepassingen dat het bouwen van metaforische modellen groepscohesie vergroot en een taal biedt voor emotionele inhoud. Een stress-schaal van DUPLO-stenen maakt een vaag gevoel tastbaar.",
     "lego serious play, stress, embodied cognition, pilotstudie",
     ""),
    ("Speelsheid is een relatiekracht (Brauer/Proyer/Chick 2021)",
     "Review naar adult playfulness: speelsheid hangt samen met relatietevredenheid, vertrouwen en samen leuke ervaringen. In dyadische APIM-analyses is zowel eigen als partners speelsheid positief gerelateerd aan tevredenheid.",
     "Brauer, Proyer & Chick (2021) beschrijven adult playfulness als stabiele eigenschap: de neiging situaties te framen als interessant/vermakelijk. Speelse interacties helpen spanning oplossen en vertrouwen herstellen na conflict.",
     "speelsheid, playfulness, relatietevredenheid, brauer proyer chick",
     ""),
]


def main():
    with get_conn() as conn:
        # 1. site config
        conn.execute(
            "UPDATE sites SET profile=?, ctas=?, auto_content_enabled=? WHERE id=?",
            (PROFILE, json.dumps(CTAS, ensure_ascii=False), 1, SITE_ID),
        )
        # 2. watchlist (clear old steentje first, idempotent)
        conn.execute("DELETE FROM radar_watchlist WHERE project=?", (PROJECT,))
        for wtype, label, value in WATCH:
            conn.execute(
                "INSERT INTO radar_watchlist (id, project, label, type, value, active, last_scanned_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, '', ?)",
                (__import__("uuid").uuid4().hex, PROJECT, label, wtype, value,
                 __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
            )
        # 3. case_studies (clear old first)
        conn.execute("DELETE FROM case_studies WHERE site_id=?", (SITE_ID,))
        for title, summary, body, tags, url in CASE_STUDIES:
            conn.execute(
                "INSERT INTO case_studies (id, site_id, title, summary, body, tags, source_url, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (__import__("uuid").uuid4().hex, SITE_ID, title, summary, body, tags, url,
                 __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                 __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
            )

    # verify
    c = sqlite3.connect(str(AGENTOS / "data" / "agentos.db"))
    row = c.execute("SELECT profile, ctas, auto_content_enabled FROM sites WHERE id=?", (SITE_ID,)).fetchone()
    nw = c.execute("SELECT COUNT(*) FROM radar_watchlist WHERE project=?", (PROJECT,)).fetchone()[0]
    ncs = c.execute("SELECT COUNT(*) FROM case_studies WHERE site_id=?", (SITE_ID,)).fetchone()[0]
    print("profile set:", bool(row[0]), "| ctas:", len(json.loads(row[1])), "| auto_content:", row[2])
    print("watchlist rows:", nw, "| case_studies:", ncs)


if __name__ == "__main__":
    main()
