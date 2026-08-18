"""Seed Week 2-4 van het DA-activatieplan (24 aug - 13 sep) als social_posts.

Week 2 (24-30 aug): Blog 'Dating Burnout' + 2 TikToks + FB-cross-posts + OogvoorLiefde
Week 3 (31 aug - 6 sep): 2 TikToks + 2e blog + quiz-gerichte FB-post per pagina
Week 4 (7-13 sep): Review (geen posts, analyse) + voorbereiding Kickstart-aanbod

FB-posts krijgen de wereldklasse-template. TikTok/Blog placeholders krijgen status
'pending_review' met inhoudelijke copy + productie-instructie (beeld/video van gebruiker).

Alle DA-pagina's: DatingAssistent (hoofd), 30+, 40+, 50+.
"""
import sqlite3, json
from datetime import datetime, timedelta

DB = "data/agentos.db"
SITES = ["DatingAssistent", "DatingAssistent 40+", "DatingAssistent 50+"]
QUIZ = {
    "DatingAssistent": "https://datingassistent.nl/registreren",
    "DatingAssistent 40+": "https://datingassistent.nl/dating-voor-40-plussers",
    "DatingAssistent 50+": "https://datingassistent.nl/dating-voor-50-plussers",
}

def _add(c, pid, proj, camp, cpost, theme, fb, angle, sched, post_type="image", note=""):
    cj = json.dumps({"facebook": fb, "instagram": fb}, ensure_ascii=False)
    ib = json.dumps({"template_type": "quote-card", "image_source": "midjourney_pending",
                     "production_note": note}, ensure_ascii=False)
    c.execute(
        "INSERT OR IGNORE INTO social_posts "
        "(id, project, campaign, campaign_post, theme, angle, copy_json, image_brief_json, "
        " status, scheduled_for, idea_query, post_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, proj, camp, cpost, theme, angle, cj, ib, "pending_review", sched, "activatie",
         post_type, datetime.now().isoformat()),
    )
    return c.execute("SELECT changes()").fetchone()[0]

def main():
    c = sqlite3.connect(DB)
    n = 0
    # ===== WEEK 2 =====
    # Ma 24/8 Blog schrijven (placeholder, jij schrijft/ik genereer)
    for proj in SITES:
        _add(c, f"sp_da_{proj.split()[-1] if ' ' in proj else 'main'}_w2_blog",
             proj, "da-week2", "BLOG", "Dating Burnout",
             "Blog live: 'Dating Burnout' — hoe je stopt met eindeloos swipen en weer "
             "met mildheid naar daten kijkt. Link in comments.",
             "blog dating burnout", "2026-08-25T09:00:00", "link",
             "JIJ: schrijf of LAAT SCHRIJVEN. Koppel interne link naar quiz. E-E-A-T auteursprofiel.")
        n += 1
    # Wo 26/8 TikTok #2 educatief
    for proj in SITES:
        _add(c, f"sp_da_{proj.split()[-1] if ' ' in proj else 'main'}_w2_tt2",
             proj, "da-week2", "TT2", "3 profielfoto fouten",
             "TikTok #2: '3 profielfoto fouten die je matches kosten' (educatief). "
             "Export zonder watermark -> cross-post naar FB.",
             "tiktok educatief", "2026-08-26T17:00:00", "video",
             "JIJ: neem op (Iris/HeyGen of zelf). 15-20s. Export naar data/uploads/da_mj/tt2_<pagina>.mp4")
        n += 1
    # Do 27/8 Blog hergebruikt als FB-post op 40+ en 50+
    for proj in ["DatingAssistent 40+", "DatingAssistent 50+"]:
        _add(c, f"sp_da_{proj.split()[-1]}_w2_blogfb",
             proj, "da-week2", "BLOGFB", "Dating Burnout op jouw leeftijd",
             "Waarom daten boven de 40/50 uitputtender voelt — en de 3 dingen die het keren. "
             "Volledige gids via de link.",
             "blog hergebruik fb", "2026-08-27T11:00:00")
        n += 1
    # Vr 28/8 TikTok #2 publiceren + cross-post
    # Za 29/8 OogvoorLiefde subtiele link
    _add(c, "sp_da_main_w2_ovl", "DatingAssistent", "da-week2", "OVL",
         "OogvoorLiefde x DatingAssistent",
         "Samen met OogvoorLiefde: echte verbinding boven de 50. Geen harde pitch — "
         "wel een eerlijk gesprek over wat écht werkt.",
         "niche cross-post", "2026-08-29T14:00:00")
    n += 1
    # Zo 30/8 Search Console check (geen post, analyse)
    # ===== WEEK 3 =====
    # TikTok #3 en #4 (2x/week)
    for i, d in enumerate(["2026-08-31T17:00:00", "2026-09-03T17:00:00"]):
        for proj in SITES:
            _add(c, f"sp_da_{proj.split()[-1] if ' ' in proj else 'main'}_w3_tt{i+3}",
                 proj, "da-week3", f"TT{i+3}", "TikTok educatief #%d" % (i+3),
                 f"TikTok #{i+3}: educatief dating-tip. Export -> cross-post FB.",
                 "tiktok", d, "video",
                 "JIJ: neem op, export naar data/uploads/da_mj/tt%d_<pagina>.mp4" % (i+3))
            n += 1
    # 2e blog (Dating na Scheiding / Attachment Styles)
    for proj in SITES:
        _add(c, f"sp_da_{proj.split()[-1] if ' ' in proj else 'main'}_w3_blog2",
             proj, "da-week3", "BLOG2", "Dating na scheiding",
             "Blog #2: 'Dating na scheiding — hoe je openstaat zonder je geschiedenis te negeren.' "
             "Koppelt direct aan de quiz.",
             "blog 2", "2026-09-02T09:00:00", "link",
             "JIJ: schrijf/laat schrijven. Koppel aan quiz-resultaat.")
        n += 1
    # Elke pagina minstens 1x quiz-gerichte post
    for proj in SITES:
        _add(c, f"sp_da_{proj.split()[-1] if ' ' in proj else 'main'}_w3_quiz",
             proj, "da-week3", "QUIZ", "De 2-minutenquiz",
             "Weet jij welk daten-patroon je tegenhoudt? De gratis quiz van 2 minuten "
             "geeft je een eerlijk antwoord. " + QUIZ[proj],
             "quiz funnel", "2026-09-04T19:00:00")
        n += 1
    # ===== WEEK 4 =====
    # Review + Kickstart-aanbod (geen posts, wel een interne note-post)
    _add(c, "sp_da_main_w4_review", "DatingAssistent", "da-week4", "REVIEW",
         "Week-4 review",
         "INTERN: analyse welke pagina + contenttype het meeste converteerde. "
         "Voorbereiding Kickstart-aanbod €47.",
         "review", "2026-09-07T10:00:00", "text", "Geen publieke post — analyse-mijlpaal.")
    n += 1
    c.commit(); c.close()
    print(f"Week 2-4 seed klaar: {n} posts aangemaakt (campagnes da-week2/3/4).")

if __name__ == "__main__":
    main()
