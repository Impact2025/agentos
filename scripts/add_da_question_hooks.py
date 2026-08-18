"""Voeg natuurlijke vraag-hooks toe aan de FB-copy van de 9 geplande DA-posts.

Hefboom #3: een echte vraag in de post lokt reacties uit, en reacties = bereik.
Geen manipulatief engagement-bait ('Tag 3 vrienden!') — wel een oprechte vraag
die de doelgroep herkent. Idempotent: slaat over als er al een '?' in staat.
"""
import sqlite3, json

HOOKS = {
    "sp_daDatingAssistent_02": "Herken jij dat: telkens dezelfde soort date, telkens hetzelfde einde? Wat loopt bij jou steeds mis?",
    "sp_daDatingAssistent_03": "Waar trek jij de grens op een eerste date? Wat is voor jou meteen een red flag?",
    "sp_daDatingAssistent_04": "Denk je dat de app je dichter bij iemand brengt, of houdt 'ie je juist bezig?",
    "sp_da40_02": "Welke 'regel' uit je vorige relatie neem je nu wél of juist niet mee?",
    "sp_da40_03": "Hoe combineer jij daten met een vol leven? Plan je het strak of laat je het op je afkomen?",
    "sp_da40_04": "Wat zou je tegen je 25-jarige zelf zeggen over liefde? Voor wie had het eerder 'gemoeten'?",
    "sp_da50_02": "Ben je nieuwsgierig naar daten, maar heb je geen zin in gedoe? Wat maakt het voor jou laagdrempelig?",
    "sp_da50_03": "Daten op je eigen tempo — hoe ziet dat er voor jou uit? Waar heb je geen zin meer in?",
    "sp_da50_04": "Alleen zijn is niet hetzelfde als eenzaam zijn. Wat maakt voor jou het verschil?",
}

c = sqlite3.connect("data/agentos.db")
for pid, hook in HOOKS.items():
    row = c.execute("SELECT copy_json FROM social_posts WHERE id=?", (pid,)).fetchone()
    if not row or not row[0]:
        print(f"  [SKIP] {pid}: geen copy"); continue
    cj = json.loads(row[0])
    fb = cj.get("facebook", "")
    if "?" in fb:
        print(f"  [SKIP] {pid}: heeft al een vraag"); continue
    fb = fb.rstrip() + "\n\n" + hook
    cj["facebook"] = fb
    # ook IG-copy gelijk trekken als die er is
    if "instagram" not in cj:
        cj["instagram"] = fb
    c.execute("UPDATE social_posts SET copy_json=? WHERE id=?", (json.dumps(cj, ensure_ascii=False), pid))
    print(f"  [OK] {pid}: vraag-hook toegevoegd")
c.commit(); c.close()
print("\nKlaar — vraag-hooks toegepast op de 9 geplande posts.")
