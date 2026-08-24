import json, sys, os
sys.path.insert(0, r"D:/APPS/impactos")
from backend.shared import social_content as sc
from backend.shared.social_style import load_style

proj = "Liefde voor Iedereen"
st = load_style(proj, refresh=True)
print("style bron:", st.bron)
print("aspect:", st.aspect)

pack = sc.generate_content_pack(
    project=proj,
    theme="Gezonde grenzen stellen in het daten",
    angle=("Tip van de Maandag: hoe zeg je eerlijk 'nee' of 'even niet' op een date "
           "zonder de ander weg te duwen? Een kleine grens is geen muur - het is de "
           "voorwaarde voor echte verbinding. Sluit aan bij campagne-beeld P08-grenzen."),
    platforms=["facebook", "instagram"],
    with_image=True,
    with_video=False,
    post_type="tip_maandag",
    idea_source="vault",
    idea_query="gezonde grenzen daten",
    idea_evidence="campagne P08-grenzen.png",
    idea_url="https://liefdevooriedereen.nl",
)
print("\nPACK ID:", pack.id)
print("STATUS:", pack.status, "| concept:", pack.concept)
print("\n=== FACEBOOK ===\n", pack.copy.get("facebook", ""))
print("\n=== INSTAGRAM ===\n", pack.copy.get("instagram", ""))
print("\n=== IMAGE BRIEF ===")
print(json.dumps(pack.image_brief, ensure_ascii=False, indent=2)[:1100])
# dump full pack to a json for later review
with open(r"D:/APPS/impactos/scripts/lvi_tip1_out.json", "w", encoding="utf-8") as f:
    json.dump({
        "id": pack.id, "status": pack.status, "concept": pack.concept,
        "copy": pack.copy, "image_brief": pack.image_brief,
    }, f, ensure_ascii=False, indent=2)
print("\nWROTE scripts/lvi_tip1_out.json")
