"""Seed Week-1 van het DA-activatieplan (17-23 aug) als social_posts.

Maakt posts aan voor:
  - Comeback-post (ma 17/8) op ALLE 5 pagina's: unieke opener per pagina +
    gedeelde body, quiz-link in auto-comment (link-in-comment trick).
  - Founder story (wo 19/8) op DatingAssistent.nl (hoofdpagina) + cross-post
    launch-teaser op de 4 sub-pagina's (di 18/8 voorbereid, wo live).
  - Za 22/8: engagement/vraag-post op Dating-50plus (grootste audience).

TikToks (do/vr) en Blog (week 2) worden NIET hier aangemaakt — die
vereisen beeld/video van de gebruiker; wel worden er lege placeholders
met status 'pending' klaargezet zodat de planning compleet is.

Elke post krijgt de wereldklasse-template (MJ-beeld + logo) bij het posten.
"""
import sys
print("GEDEACTIVEERD: geen aparte 40+/50+ DatingAssistent-sites meer. Gebruik site_id 'datingassistent' (datingassistent.nl).")
sys.exit(1)
import sqlite3, json, os
from datetime import datetime, timedelta

DB = "data/impactos.db"
SITES = {
    "DatingAssistent":      ("datingassistent", "30+", "DatingAssistent"),
    "DatingAssistent 40+":  ("dating40",        "40+", "DatingAssistent voor 40-plussers"),
    "DatingAssistent 50+":  ("dating50",        "50+", "DatingAssistent voor 50-plussers"),
}
AGE_BADGE = {"DatingAssistent": "", "DatingAssistent 40+": "40+", "DatingAssistent 50+": "50+"}
BRAND = {
    "DatingAssistent": "DatingAssistent",
    "DatingAssistent 40+": "DatingAssistent voor 40-plussers",
    "DatingAssistent 50+": "DatingAssistent voor 50-plussers",
}

COMEBACK_BODY = (
    "We zijn terug. Na een stilte van jaren lanceert DatingAssistent opnieuw — "
    "met Iris, een persoonlijke gids die 24/7 met je meedenkt. "
    "Geen eindeloos swipen, wél eerlijke gesprekken die ergens heen leiden. "
    "Wat is voor jou het belangrijkste als je nu weer openstaat voor een nieuwe ontmoeting? Laat het hieronder weten!"
)
OPENERS = {
    "DatingAssistent": "Daten is geen geluk. Het is een patroon — en dat patroon kun je veranderen.",
    "DatingAssistent 40+": "Op je 40e weet je wat je niet meer wil. Nu de rest.",
    "DatingAssistent 50+": "Alleen zijn is niet hetzelfde als eenzaam zijn. Tijd voor een nieuw hoofdstuk.",
}
QUIZ = {
    "DatingAssistent": "https://datingassistent.nl/quiz",
    "DatingAssistent 40+": "https://datingassistent.nl/40-plus",
    "DatingAssistent 50+": "https://datingassistent.nl/50-plus",
}

def _add(c, pack_id, project, campaign, post_no, title, fb_copy, theme, angle, sched):
    cj = json.dumps({"facebook": fb_copy, "instagram": fb_copy}, ensure_ascii=False)
    ib = json.dumps({"template_type": "quote-card", "image_source": "midjourney_pending"}, ensure_ascii=False)
    from datetime import datetime as _dt
    cur = c.execute(
        "INSERT OR IGNORE INTO social_posts "
        "(id, project, campaign, campaign_post, theme, angle, copy_json, image_brief_json, "
        " status, scheduled_for, idea_query, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pack_id, project, campaign, post_no, title, angle, cj, ib, "pending_review", sched, "reactivatie",
         _dt.now().isoformat()),
    )
    return cur.rowcount

def main():
    c = sqlite3.connect(DB)
    base = datetime(2026, 8, 17, 12, 0)  # ma 17/8 12:00
    n = 0
    # --- Comeback-post ma 17/8 op alle 3 bestaande DA-sites ---
    for proj, (sid, age, brand) in SITES.items():
        sched = "2026-08-17T12:00:00"
        fb = f"{OPENERS[proj]}\n\n{COMEBACK_BODY}"
        pid = f"sp_da_{sid}_cb"
        if _add(c, pid, proj, "da-week1", "CB", OPENERS[proj], fb, OPENERS[proj],
                "reactivatie alle pagina's", sched):
            n += 1
    # --- Founder story wo 19/8 op hoofdpagina + teaser di 18/8 op subs ---
    founder = ("Van 2013 tot nu: mijn eigen daten liep vast, dus ik bouwde er iets voor. "
               "Toen stopte ik. Nu weet ik waarom ik terug ben.\n\n"
               "In 2013 startte ik DatingAssistent omdat ik zelf vastliep op dezelfde patronen. "
               "We hielpen duizenden singles boven de 30 eerlijker kijken naar wat ze zochten. "
               "Toen stopte het — eerlijk: niet omdat het mislukte, maar omdat ik opraakte. "
               "De mensen werden ouder, de apps harder, en ik kon het niet meer alleen volhouden "
               "op de menselijke manier die ik beloofd had.\n\n"
               "Nu ben ik terug. De brug die ik toen niet had, bestaat nu wél: AI die 24/7 met je "
               "meedenkt, zonder oordeel, zonder haast. Iris is geen chatbot — ze is de ervaring "
               "van al die jaren, beschikbaar op het moment dat jij twijfelt.\n\n"
               "Daten is geen geluk. Het is een patroon. En dat patroon kun je veranderen.")
    # teaser di 18/8 op de 3 subs
    teaser = ("Morgen het echte verhaal. Waarom DatingAssistent stopte, en waarom we nu terug zijn "
              "— met Iris, je persoonlijke gids. Zet 'm vast. ☕")
    for proj, (sid, age, brand) in SITES.items():
        if _add(c, f"sp_da_{sid}_ft", proj, "da-week1", "FT", teaser, teaser,
                "founder teaser", "cross-post teaser", "2026-08-18T10:00:00"):
            n += 1
    # founder live wo 19/8 op hoofdpagina
    if _add(c, "sp_da_datingassistent_fs", "DatingAssistent", "da-week1", "FS", founder, founder,
            "founder story 2013-pauze-terugkeer", "founder story", "2026-08-19T10:00:00"):
        n += 1
    # --- Za 22/8 engagement/vraag-post op 50+ (grootste audience) ---
    eng = ("Je bent boven de 50 en datet weer. Wat is de grootste mythe die je tegenkwam? "
           "Wij horen vaak: 'boven de 50 is de pool te klein'. Onze leden bewijzen het tegendeel. "
           "Deel jouw ervaring — wat werkte wél?")
    if _add(c, "sp_da_dating50_eng", "DatingAssistent 50+", "da-week1", "ENG", eng, eng,
            "engagement vraag 50+", "vraag-post", "2026-08-22T15:00:00"):
        n += 1
    c.commit(); c.close()
    print(f"Week-1 seed klaar: {n} nieuwe posts aangemaakt in campagne 'da-week1'.")

if __name__ == "__main__":
    main()
