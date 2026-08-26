"""Eenmalig demo-data-script voor De Sparringpartner (7 t/m 25 aug 2026).

Vult alleen de dagen aan die nog leeg zijn — de echte ochtend-rijen
(18,19,20,21,24,25 aug) en avond-rijen (18,20,24 aug) worden nooit
overschreven. Bouwt een herkenbaar verloop (een dip van drie dagen, dan
herstel) zodat de technieken en het proactieve signaal iets te laten zien
hebben in een demo.

Draai eenmalig: .venv/Scripts/python.exe scripts/seed_coach_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.shared.database import get_conn
from backend.domains.rituals.service import get_service as get_rituals_service
from backend.domains.coach import service as coach_service

rit = get_rituals_service()

with get_conn() as conn:
    existing_morning = {r["date"] for r in conn.execute("SELECT date FROM ritual_morning")}
    existing_evening = {r["date"] for r in conn.execute("SELECT date FROM ritual_evening")}

print("Bestaande ochtend-dagen (blijven ongewijzigd):", sorted(existing_morning))
print("Bestaande avond-dagen (blijven ongewijzigd):", sorted(existing_evening))

# (datum, energie, slaap, intentie, focus1, dagsverloop-notitie)
DAGEN = [
    ("2026-08-07", 6, 6, "Rustig de week afsluiten", "Voorbereiding workshop", "goed"),
    ("2026-08-08", 7, 7, "Weekend, opladen", "", "goed"),
    ("2026-08-09", 7, 7, "Tijd voor mezelf", "", "goed"),
    ("2026-08-10", 5, 6, "Scherp de week in", "Klantgesprek voorbereiden", "matig"),
    ("2026-08-11", 4, 5, "Volhouden vandaag", "Leverancier bellen", "dip"),
    ("2026-08-12", 3, 4, "Overleven tot de avond", "Issue oplossen", "dip"),
    ("2026-08-13", 3, 5, "Kleine stappen vandaag", "Mail wegwerken", "dip"),
    ("2026-08-14", 4, 5, "Adem, dan verder", "Team bijpraten", "herstel-start"),
    ("2026-08-15", 6, 6, "Weekend, even niets moeten", "", "herstel"),
    ("2026-08-16", 6, 7, "Rustig aan doen", "", "herstel"),
    ("2026-08-17", 7, 7, "Fris de week in", "Workshop voorbereiden", "goed"),
    ("2026-08-22", 7, 6, "Weekend, buiten zijn", "", "goed"),
    ("2026-08-23", 6, 7, "Ontspannen zondag", "", "goed"),
]

GAINS_COSTS = {
    "goed": (["Goed gesprek met een klant", "Een uur geconcentreerd gewerkt"], []),
    "matig": (["Workshop voorbereid"], ["Vergadering die uitliep"]),
    "dip": ([], ["Issue met een leverancier", "Te veel schakelen tussen projecten"]),
    "herstel-start": (["Kort gewandeld tussen taken door"], ["Achterstand mail wegwerken"]),
    "herstel": (["Weekend zonder schema"], []),
}

created_morning = created_evening = created_energy = 0

for date, energy, sleep, intentie, focus1, verloop in DAGEN:
    if date not in existing_morning:
        rit.save_morning(date, {
            "intentie": intentie,
            "affirmatie": "Ik doe wat vandaag nodig is.",
            "dankbaarheid": ["Gezondheid", "Het team", "Een goed gesprek"],
            "energyLevel": energy,
            "sleepQuality": sleep,
            "wakeTime": "06:45",
            "focusBlok1": {"onderwerp": focus1, "doel": ""} if focus1 else {},
            "focusBlok2": {},
        })
        created_morning += 1

    if date not in existing_evening:
        rit.save_evening(date, {
            "whatWentWell": "De dag liep zoals verwacht." if verloop != "dip" else "Ondanks alles toch iets afgerond.",
            "biggestWin": "Focus vastgehouden" if verloop != "dip" else "Niet opgegeven",
            "whatLearned": "Kleine stappen tellen ook.",
            "challenges": "" if verloop == "goed" else "Energie liep terug richting de avond.",
            "energyLevel": max(1, energy - 1),
            "tomorrowTop3": ["", "", ""],
            "gratitude": "Dat er morgen weer een kans is.",
        })
        created_evening += 1

    gains, costs = GAINS_COSTS.get(verloop, ([], []))
    entries = [{"activity": g, "direction": "gain"} for g in gains] + \
              [{"activity": c, "direction": "cost"} for c in costs]
    if entries:
        created_energy += coach_service.save_energy_log(date, entries)

# Nog wat energie-attributie op de bestaande echte dagen, want die hadden dat
# veld nog niet toen ze werden ingevuld (dit is nieuw sinds vandaag).
for date, gains, costs in [
    ("2026-08-19", ["Diepe werksessie"], ["Lange reistijd"]),
    ("2026-08-21", ["Goed teamoverleg"], []),
]:
    entries = [{"activity": g, "direction": "gain"} for g in gains] + \
              [{"activity": c, "direction": "cost"} for c in costs]
    created_energy += coach_service.save_energy_log(date, entries)

# Twee extra geleerde patronen naast de live gegenereerde, voor een rijker paneel.
coach_service.remember_lesson(
    "cgt:energie-drie-dagen-laag-na-leveranciersissue",
    "cgt",
    "Een leveranciersprobleem midden in de week trekt je energie drie dagen mee naar beneden — dat patroon herhaalt zich.",
)
coach_service.remember_lesson(
    "strengths:weekend-herstel-werkt",
    "strengths",
    "Een weekend zonder vast schema (15-16 aug) herstelde je energie sneller dan een normaal weekend — dat patroon mag je vaker inzetten.",
)

print(f"\nAangemaakt: {created_morning} ochtend, {created_evening} avond, {created_energy} energie-log-rijen.")
print("Klaar. Ververs de Control Room om het resultaat te zien.")
