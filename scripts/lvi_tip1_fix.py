import json, sys
sys.path.insert(0, r"D:/APPS/impactos")
from backend.shared.database import get_conn

pack_id = "sp_ae5b40c0b607"
p08 = r"D:/APPS/Hermes Brein/Hermes Breind/10_Projects/LiefdeVoorIedereen/posts/campagne/P08-grenzen.png"

with get_conn() as conn:
    row = conn.execute(
        "SELECT image_brief_json FROM social_posts WHERE id=?", (pack_id,)
    ).fetchone()
    brief = json.loads(row["image_brief_json"] or "{}")
    brief["image_path"] = p08
    brief["image_url"] = ""
    brief["image_source"] = "lvi_campagne_p08"
    brief["color_palette"] = ["#D6516C", "#0E6171", "#FAF8F5", "#0F1E29"]
    brief["headline"] = "Een grens is geen muur"
    brief["subtext"] = "Tip van de Maandag - gezonde grenzen in het daten"
    brief["font"] = "Poppins (Bold) voor kop, DM Sans voor body"
    brief["layout"] = ("Kop in Poppins Bold op een egaal Koraalrood (#D6516C) of Diepteal (#0E6171) "
                       "vlak in het onderste derde, subtext eronder in Soft Cream (#FAF8F5), "
                       "'www.liefdevooriedereen.nl' onderaan. Minimaal 32pt voor leesbaarheid (LVB/neurodivers).")
    brief["canva_note"] = ("Open Canva > Templates > zoek een 4:5 'quote' of 'social post'-template, "
                           "vervang tekst door headline/subtext, zet de merkkleur op Koraalrood (#D6516C) "
                           "of Diepteal (#0E6171) en de tekst op Soft Cream (#FAF8F5).")
    brief["midjourney_prompt"] = ("Echt, divers stel dat samen ontspannen een grens aangeeft in een "
                                  "veilig gesprek, warm natuurlijk daglicht, koraal (#D6516C) en teal "
                                  "(#0E6171) tinten, 30-40% negative space, geen overpose, --ar 4:5")
    conn.execute(
        "UPDATE social_posts SET image_brief_json=? WHERE id=?",
        (json.dumps(brief, ensure_ascii=False), pack_id),
    )
print("Pack", pack_id, "updated with LVI brand image + colors.")
print("Linked image:", brief["image_path"])
print("Palette:", brief["color_palette"])
