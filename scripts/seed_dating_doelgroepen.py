"""Seed DatingAssistent 30+/40+/50+ als aparte sites + doelgroep-specifieke
social_posts met gevarieerde plaats­tijden.

- Bestaande site 'DatingAssistent' = de 30+ pagina (id 107835799327006).
- Voeg 'DatingAssistent 40+' (174410412641281) en 'DatingAssistent 50+'
  (123632714408933) toe als aparte sites, met gekopieerd merkprofiel.
- Schrijf per doelgroep 4 berichten (emotie / quiz / praktijk / zelfvertrouwen)
  met een eigen stem + bewust andere plaats­tijden (09/10/11/12/13/15/17/19/20/21u)
  zodat fb_history.best_posting_hour straks vergelijkbaar wordt.
- Alles status=pending_review, auto_post UIT: de mens keurt goed via Social Creatie.

Idempotent: (project, campaign, campaign_post) is uniek per post; sites insert
slaat over als de naam al bestaat.
"""
import sqlite3, json, uuid
from datetime import datetime, timedelta

DB = "data/agentos.db"
CAMPAGNE = "da-doelgroepen-2026"

# ── 1. Sites ──────────────────────────────────────────────────────────────
PAGES = {
    "DatingAssistent":      "107835799327006",  # bestaat al (30+)
    "DatingAssistent 40+":  "174410412641281",
    "DatingAssistent 50+":  "123632714408933",
}

# Verschuiving in dagen v.a. vandaag om een bepaalde weekdag te raken
# (vandaag = ma 17 aug 2026). (offset_dagen, uur, minuut)
TIMING = {
    "DatingAssistent":     [(1,12,30),(3,20,0),(5,10,0),(8,17,30)],   # 30+
    "DatingAssistent 40+": [(2,13,0),(4,19,0),(6,11,0),(8,21,0)],     # 40+
    "DatingAssistent 50+": [(7,10,0),(3,15,0),(5,9,0),(9,20,30)],     # 50+
}

def dt(offset, h, m):
    base = datetime.now().replace(minute=0, second=0, microsecond=0)
    return (base + timedelta(days=offset)).replace(hour=h, minute=m).isoformat()

# ── Copy per doelgroep ─────────────────────────────────────────────────────
# Elke post: (campaign_post, post_type, theme, angle, fb, ig, li, tt_hook,
#             img_headline, img_subtext)
CONTENT = {
"DatingAssistent": [
 ("30.1","emotie","De 14e eerste date",
  "herkenning swipe-moeheid",
  "Je weet wel: de 14e 'eerste date' dit jaar. Dezelfde koffietent, dezelfde 'wat doe jij voor werk?', dezelfde stilte na de eerste slok. Daten voelt soms als een bijbaan zonder loonstrook. Maar die ene keer dat het klikt — dát is waar je het voor doet. Wat was jouw meest gedenkwaardige eerste date ooit? 👇",
  "Dé 14e eerste date van dit jaar ☕️ Herkenbaar? Wie herkent dezelfde koffietent-ellende… en die ene keer dat het wél klikte? 👇",
  "De meeste 30-plussers die ik spreek, zien daten als een afvinklijst in plaats van een ontmoeting. De shift van 'weer zo'n date' naar 'wie weet' is klein maar bepalend. Hoe houd jij de moed erin?",
  "Daten is geen sollicitatie — stop met je antwoord al klaar hebben",
  "De 14e eerste date",
  "herkenning voor de swipe-moe"),
 ("30.2","activatie","De 2-minutenquiz",
  "welk daten-patroon zit jóu in de weg",
  "Welk daten-patroon zit jóu in de weg? De meeste singles maken dezelfde fout zonder het te weten — en blijven daardoor hangen in match-droogte. Doe de gratis quiz van 2 minuten, krijg je resultaat per mail en zie meteen wat je anders kunt doen. Geen account nodig. 👉 datingassistent.nl",
  "Welk patroon zit jóu in de weg? 🤔 Gratis quiz van 2 min, resultaat in je inbox. Geen account. 👉 datingassistent.nl",
  "Pattern recognition is de snelste weg uit match-droogte. Onze quiz kijkt niet naar wie je zoekt, maar naar het gedrag dat je telkens herhaalt. 2 minuten, gratis, geen registratie.",
  "Stop met raden — doe de 2-minutenquiz en zie je daten-patroon",
  "Ontdek je daten-patroon",
  "gratis quiz van 2 minuten"),
 ("30.3","praktijk","Veilig daten is zelfvertrouwen",
  "grenzen stellen filteren twijfelgevallen",
  "Veilig daten is niet onhandig, het is zelfvertrouwen. Een videocall vóór het afspreken, een openbare plek, je eigen weg terug — dat filtert de twijfelgevallen eruit zónder dat het de spontaniteit doodt. Daten is geen geluk, het is een patroon. En jij bepaalt welk patroon je traint.",
  "Veilig daten = zelfvertrouwen, niet onhandig 💪 Een call eerst, openbare plek, eigen weg terug. Filtert de twijfelgevallen eruit.",
  "Veiligheid en spontaniteit sluiten elkaar niet uit. De singles die het langst volhouden, hebben simpele checks ingebouwd vóórdat ze afspreken. Lage moeite, hoge winst in rust.",
  "Veilig daten is geen rem, het is een filter",
  "Veilig daten is zelfvertrouwen",
  "grenzen die filteren"),
 ("30.4","emotie","De app is geen relatie",
  "daten als vaardigheid",
  "Je matcht, je kletst, je belt — en dan? De app is een opstapje, geen bestemming. De singles die écht vooruitkomen, behandelen daten als een vaardigheid: vragen stellen die ertoe doen, luisteren zonder je antwoord al klaar te hebben. Kleine shift, groot verschil.",
  "Match → praat → bel → en dan? De app is een opstapje, geen bestemming. Behandel daten als een vaardigheid 💡",
  "Dating apps zijn distributie, geen development. Wat je ín het gesprek brengt — nieuwsgierigheid, aanwezigheid — bepaalt of het iets wordt. Dat is trainbaar.",
  "De app is het begin, niet het eind — train de vaardigheid",
  "De app is geen relatie",
  "daten als vaardigheid"),
],
"DatingAssistent 40+": [
 ("40.1","emotie","Opnieuw beginnen na je 40e",
  "wijzer = geen onzin meer pikken",
  "Iedereen zegt 'op je 40e ben je wijzer'. Klopt. Maar niemand zegt erbij dat dat wijzer-zijn ook betekent: je pikt geen onzin meer. Je weet wat je wilt, en je hebt geen zin meer in spelletjes. Daten na je 40e is niet moeilijker — het is eerlijker. En ergens ook een stuk spannender. ❤️",
  "Op je 40e pik je geen onzin meer. Daten is niet moeilijker, het is eerlijker 💛",
  "De 40+ datermarkt is ondergewaardeerd: mensen weten wat ze willen, hebben hun baggage opgeruimd en hebben geen tijd voor spelletjes. Dat maakt het eerlijker én sneller als het klikt.",
  "daten op je 40e is niet eng, het is eerlijk",
  "Opnieuw beginnen na je 40e",
  "wijzer, eerlijker, spannender"),
 ("40.2","activatie","De quiz voor wie het al eerder deed",
  "patroon uit eerdere relaties",
  "Je hebt al een heel leven achter je — inclusief een of twee relaties die je iets leerden. Waarom zou je dan weer dezelfde fout maken? Onze quiz kijkt niet naar wie je zoekt, maar naar het patroon dat je telkens herhaalt. 2 minuten, gratis, resultaat in je inbox. 👉 datingassistent.nl",
  "Al een heel leven achter je 💭 Onze quiz kijkt naar je patroon, niet naar je wensenlijst. Gratis, 2 min. 👉 datingassistent.nl",
  "Jarenlange relatie-ervaring is een dataset, geen ballast. Onze quiz vertaalt die naar het patroon dat je telkens herhaalt — zodat je het deze keer doorbreekt.",
  "Je levenservaring is geen ballast, het is data — doe de quiz",
  "Je patroon, geen wensenlijst",
  "quiz voor de 40+ herstart"),
 ("40.3","praktijk","Kinderen, ex'en en een nieuwe start",
  "daten als vast ritme",
  "De 40+ realiteit: een date plannen tussen zaakjes bij de rugbyschool en een appje van je ex over de belastingaangifte. Het is geen romcom. Maar het kan wél. De singles die het lukt, zijn niet degenen met de meeste tijd — het zijn degenen die daten een vaste plek geven, net als de sportschool. Klein ritme, groot effect.",
  "Date plannen tussen rugby en de belastingaangifte 🏉 Herkenbaar? Geef daten een vast ritme, net als de sportschool.",
  "Tijd is niet het probleem, prioriteit wel. De 40+'ers die daten inbouwen als vast blok — niet als restpost — zien het snelst resultaat. Kleine consistentie wint van grote uitbarstingen.",
  "Geen tijd? Geef daten een vast ritme, net als de sportschool",
  "Vast ritme, groot effect",
  "daten tussen het drukke leven"),
 ("40.4","emotie","Je staat er beter voor dan je denkt",
  "rust i.p.v. urgentie",
  "Op je 25e dacht je dat liefde 'moest' komen. Op je 40e weet je dat het mag. Dat verschil maakt je aantrekkelijker dan welke filter dan ook. Daten vanuit rust in plaats van urgentie — probeer het eens.",
  "Op je 25e móest het. Op je 40e mág het 💛 Daten vanuit rust, niet urgentie.",
  "Urgentie is de vijand van een goede eerste indruk. Rust uitstraalt omdat je weet dat het mag — niet móét — is de ondergewaardeerde 40+ superkracht.",
  "Liefde moest komen op je 25e, het mág op je 40e",
  "Je staat er beter voor dan je denkt",
  "rust in plaats van urgentie"),
],
"DatingAssistent 50+": [
 ("50.1","emotie","Het is nooit te laat voor een nieuw hoofdstuk",
  "liefde heeft geen leeftijd",
  "Sommigen kijken op van 'dating' boven de 50. Alsof liefde een leeftijd heeft. Maar een wandeling met iemand die je verhaal kent, een gezamenlijke zondagmiddag, een telefoontje dat je dag maakt — dat gun je toch iedereen? En ja, ook jezelf. ❤️",
  "Liefde een leeftijd hebben? 🚶 Een wandeling, een zondagmiddag, een belletje dat je dag maakt. Ook voor jou.",
  "Companionship na je 50e is geen luxe maar een basisbehoefte. De mensen die het aandurven, winnen aan energie, gezondheid en zin. Kleine stappen, grote terugkeer.",
  "liefde op je 50e bestaat echt — een nieuw hoofdstuk",
  "Nooit te laat voor een nieuw hoofdstuk",
  "gezelschap op latere leeftijd"),
 ("50.2","activatie","Nieuwsgierig, maar geen zin in gedoe?",
  "laagdrempelige quiz",
  "De gratis quiz van 2 minuten is er ook voor wie digitaal wat voorzichtiger is. Geen ingewikkelde app, geen profiel-fotomarathon. Tien korte vragen, een eerlijk antwoord per mail over wat jóu helpt. Klik, lees, klaar. 👉 datingassistent.nl",
  "Geen zin in gedoe? 📝 10 korte vragen, antwoord in je inbox. Geen app, geen fotomarathon. 👉 datingassistent.nl",
  "Digitale drempelvrees is geen reden om binnen te blijven. Onze quiz is bewust laagdrempelig: geen account, geen foto-zooi, gewoon tien vragen en een bruikbaar antwoord.",
  "Geen zin in gedoe? De quiz is laagdrempelig — klik, lees, klaar",
  "Nieuwsgierig, geen zin in gedoe?",
  "laagdrempelige 2-minutenquiz"),
 ("50.3","praktijk","Rustig aan, op je eigen tempo",
  "daten hoeft geen tweede baan",
  "Boven de 50 heb je geen zin meer in haast. En dat hoeft ook niet. Daten kan klein beginnen: een kop koffie, een museum, een whatsappje dat je aan iemand dacht. Wij helpen je het overzicht te houden en de valkuilen te herkennen — zonder dat het een tweede baan wordt.",
  "Geen zin in haast ☕️ Een kop koffie, een museum, een appje. Daten mag klein beginnen.",
  "Tempo is een keuze. De 50+ daters die het volhouden, bouwen geen druk op maar een rustig ritme. Wij houden het overzicht zodat jij aan het gesprek toekomt.",
  "Daten hoeft geen tweede baan — rustig aan, op je tempo",
  "Rustig aan, op je eigen tempo",
  "daten zonder druk"),
 ("50.4","emotie","Alleen zijn is niet hetzelfde als eenzaam blijven",
  "gemeenschap van gelijken",
  "Er zijn meer mensen in jouw situatie dan je denkt. Weduwen, gescheidenen, mensen die gewoon weer iemand wilden ontmoeten. Het begint vaak met één klein gesprek. Wij maken dat gesprek een stukje makkelijker.",
  "Weduwe, gescheiden, nieuwsgierig 💬 Er zijn meer mensen zoals jij dan je denkt. Het begint met één gesprek.",
  "De stilte na een verlies of scheiding is begrijpelijk, maar niet permanent. Eén laagdrempelig gesprek is vaak de eerste stap terug naar verbinding. Dat verdien je.",
  "Alleen zijn is niet hetzelfde als eenzaam blijven — één gesprek",
  "Alleen is niet eenzaam willen blijven",
  "verbinding na verlies of scheiding"),
],
}

def main():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.cursor()

    # ── Sites aanmaken (40+ / 50+) ──
    base = cur.execute("SELECT * FROM sites WHERE name='DatingAssistent'").fetchone()
    base_profile = dict(base)["profile"] if base else ""
    created = []
    for name, pid in PAGES.items():
        exists = cur.execute("SELECT id FROM sites WHERE name=?", (name,)).fetchone()
        if exists:
            created.append(f"{name} (bestond al)")
            continue
        sid = f"dating{name.split()[-1].replace('+','')}"  # dating40 / dating50
        cur.execute(
            "INSERT INTO sites (id,name,base_url,gsc_property,profile,facebook_page_id,"
            "auto_social_enabled,auto_social_platforms,created_at) VALUES (?,?,?,?,?,?,0,'',?)",
            (sid, name, "https://datingassistent.nl", "",
             base_profile, pid, datetime.now().isoformat()),
        )
        created.append(f"{name} -> {sid} ({pid})")
    c.commit()
    print("SITES:", *created, sep="\n  ")

    # ── Social posts per doelgroep ──
    total = 0
    for project, posts in CONTENT.items():
        timings = TIMING[project]
        for i, (cp, ptype, theme, angle, fb, ig, li, tt, ih, isub) in enumerate(posts):
            off, h, m = timings[i]
            sched = dt(off, h, m)
            pid_col = f"sp_da{project.split()[-1].replace('+','')}_{i+1:02d}"
            # idempotent: overslaan als (project,campaign,campaign_post) bestaat
            dup = cur.execute(
                "SELECT id FROM social_posts WHERE project=? AND campaign=? AND campaign_post=?",
                (project, CAMPAGNE, cp)).fetchone()
            if dup:
                print(f"  skip {project} {cp} (bestaat)")
                continue
            copy = {"facebook": fb, "instagram": ig, "linkedin": li,
                    "tiktok": tt, "twitter": ""}
            img = {
                "template_type": "quote-card",
                "dimensions": "1080x1350",
                "headline": ih[:60],
                "subtext": isub[:120],
                "color_palette": ["#e5a500", "#1f2937", "#ffffff"],
                "font": "Inter / Montserrat, vet voor headline",
                "layout": "Gouden serif-titel op donker transparant vlak, onderschrift eronder, amber-accent rechtsonder.",
                "midjourney_prompt": f"{ih}, warm amber accent (#e5a500), clean minimal typography, soft neutral background, professional Dutch dating brand style, high contrast --ar 4:5 --style raw --v 6",
                "image_url": "", "image_path": "", "image_source": "",
                "canva_note": "Open Canva > Templates > quote/post, vervang tekst, zet merkkleur op amber (#e5a500).",
            }
            tiktok = {"hook": tt, "script": tt + ". " + angle,
                      "shotlist": [], "voiceover_cues": "", "captions": theme,
                      "hashtags": ["#daten", "#dating", "#relatie", "#liefde"],
                      "duration_sec": 30, "music_cue": "Warm, rustig, niet te druk."}
            cur.execute(
                "INSERT INTO social_posts (id,project,theme,angle,brand_context,copy_json,"
                "image_brief_json,tiktok_pack_json,status,concept,created_at,origin,"
                "idea_source,idea_url,campaign,campaign_post,scheduled_for,post_type) "
                "VALUES (?,?,?,?,?,?,?,?, 'pending_review',0,?, 'campagne','',"
                "'https://datingassistent.nl',?,?,?,?)",
                (pid_col, project, theme, angle, project,
                 json.dumps(copy, ensure_ascii=False),
                 json.dumps(img, ensure_ascii=False),
                 json.dumps(tiktok, ensure_ascii=False),
                 datetime.now().isoformat(), CAMPAGNE, cp, sched, ptype))
            total += 1
            print(f"  + {project} {cp} @ {sched} [{ptype}]")
    c.commit()

    # ── Verificatie ──
    print("\nVERIFICATIE:")
    for r in cur.execute(
        "SELECT project, COUNT(*) n, MIN(scheduled_for) eerste, MAX(scheduled_for) laatste "
        "FROM social_posts WHERE campaign=? GROUP BY project ORDER BY project", (CAMPAGNE,)):
        print(f"  {r['project']:22} posts={r['n']}  {r['eerste']} .. {r['laatste']}")
    # unieke uren
    uren = [r[0] for r in cur.execute(
        "SELECT DISTINCT substr(scheduled_for,12,2) FROM social_posts WHERE campaign=?", (CAMPAGNE,))]
    print("  plaats-uren getest:", ", ".join(sorted(uren)))
    c.close()

if __name__ == "__main__":
    main()
