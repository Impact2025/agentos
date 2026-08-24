# DatingAssistent — Wereldklasse Social System (MASTERPLAN)

Status: 17 aug 2026 — actief draaiend systeem.
Pagina's: 4 (DatingAssistent.nl hoofd + 30+ + 40+ + 50+).

────────────────────────────────────────────────────────
## 1. ARCHITECTUUR (wat er draait)
────────────────────────────────────────────────────────
- Cron "DA post-engine (2u)": elke 2 uur →
  1. Verfrist FB-page-tokens (kortlevend ~1u, opgehaald uit /me/accounts)
  2. Plaatst alle geplande posts waarvan scheduled_for <= nu (post_type=image)
  3. Monteert MJ-beeld + wereldklasse-template (echt LogoDA + leeftijd-badge + merknaam)
  4. Auto-comment direct eronder (leeftijd-specifieke CTA-link + hashtags)
  5. IG-post (zodra gekoppeld) via publieke host
  6. Auto-reply op nieuwe FB-comments (welkom + gids-link)
- Idempotent: slaat over wat al gepost is. Veilig voor cron.
- FB-posts alleen (IG wacht op Meta-koppeling, zie §4).

Bestanden:
  backend/shared/facebook.py ............ post_update, comment_on_post, refresh_page_tokens
  backend/shared/social_auto_comment.py  auto-comment (CTA-link + hashtags)
  backend/shared/public_host.py ......... Imgur + catbox fallback (voor IG/Reels)
  backend/shared/instagram.py .......... IG-post via publieke url (klaar, wacht op koppeling)
  scripts/da_template.py .............. wereldklasse kaart (echt logo)
  scripts/da_post_engine.py ........... cron-engine (alle campagnes)
  scripts/run_da_engine.sh ........... cron-wrapper
  scripts/da_auto_reply.py ........... auto-reply op comments
  scripts/seed_da_week1.py / seed_da_week2_4.py ... planning-aanmaak
  scripts/add_da_question_hooks.py ... vraag-hooks in posts

────────────────────────────────────────────────────────
## 2. BESCHIKBARE HEVELNEN (prioriteit)
────────────────────────────────────────────────────────
1. INSTAGRAM — grootste gemiste kans. Nog NIET gekoppeld als IG-Business.
2. VIDEO/REELS — FB geeft veel meer bereik. 3 Reels/week klaar (zie §6).
3. ECHTE VRAAG IN POST — reacties = bereik. Reeds in alle posts ingebouwd.
4. SNEL REAGEREN OP COMMENTS — auto-reply draait mee in cron.
5. FACEBOOK GROEPEN — jij deelt als lid (geen spam).
6. GEPINDE POST — na 12 posts zet ik 1 post vast per pagina.
7. BETAALDE BOOST — kleine boost op beste post (fb_history markeert 'beste').
8. TIMING UIT EIGEN DATA — fb_history best_posting_hour na 12 posts.

────────────────────────────────────────────────────────
## 3. 6-WEKEN-PLANNING (actueel in DB)
────────────────────────────────────────────────────────
Totaal gepland: 30 posts (campagnes da-doelgroepen-2026 + da-week1/2/3/4).

DA-DOELGROEPEN-2026 (12 posts, 17-26 aug): 30.1-4 / 40.1-4 / 50.1-4
  - 30.1/40.1/50.1 = LIVE (openings, vanmiddag)
  - 30.2-4, 40.2-4, 50.2-4 = wachten op jouw MJ-beelden (da40_2..4, da50_2..4)
  - 30+-reeks beelden staan er (da30_2/3/4) → posten automatisch

DA-WEEK1 (Reactivering & Founder Story, 17-23 aug):
  17/8  Comeback-post (CB) — ALLE 4 pagina's LIVE vanavond
  18/8  Founder teaser (FT) — 3 sub-pagina's
  19/8  Founder story (FS) — hoofdpagina
  20/8  TikTok #1 (Iris/HeyGen) — JIJ levert
  21/8  TikTok publish + cross-post FB
  22/8  Engagement/vraag-post (ENG) — 50+
  23/8  Week-1 review (handmatig)

DA-WEEK2 (Blog + Ritme, 24-30 aug):
  25/8  Blog "Dating Burnout" (BLOG) — alle 4 pagina's
  26/8  TikTok #2 educatief (TT2)
  27/8  Blog hergebruikt als FB-post (40+ & 50+)
  28/8  TikTok #2 publish + cross-post
  29/8  OogvoorLiefde cross-post (externe niche, jij post zelf)
  30/8  Search Console check

DA-WEEK3 (Consistentie + funneldata, 31 aug - 6 sep):
  TikTok #3 & #4 (2x/week)
  2e Blog "Dating na Scheiding" / "Attachment Styles"
  Elke pagina >=1 quiz-gerichte post
  Check quiz-completions + email open rates

DA-WEEK4 (Review & volgende stap, 7-13 sep):
  Analyse: welke pagina/type leverde traffic/leads?
  Beslissing: opschalen naar 1.000 TikTok-volgers (cross-post vanaf FB)
  Voorbereiding Kickstart-aanbod €47

────────────────────────────────────────────────────────
## 4. INSTAGRAM — wat jij moet doen
────────────────────────────────────────────────────────
Graph API ziet nog GEEN ig_business_account bij de pagina's. Oorzaak: DA-IG
is niet gekoppeld als IG-Business aan een FB-pagina.

JIJ:
  1. DA-IG omzetten naar Business/Creator (Instellingen → Accounttype → Bedrijf)
  2. Business Manager → DA-pagina → Instagram → Verbinden (met IG-Business)
  3. Zeg het → ik haal ig_id op, sla op, engine post automatisch ook naar IG.

Code staat klaar (instagram.py + public_host.py met Imgur/catbox fallback).

────────────────────────────────────────────────────────
## 5. BEELDEN JIJ MOET LEVEREN (da_mj map)
────────────────────────────────────────────────────────
D:/apps/impactos/data/uploads/da_mj/
  da30_2.png, da30_3.png, da30_4.png  ✓ GEDAAN (30+-reeks post automatisch)
  da40_2.png, da40_3.png, da40_4.png  ⏳ WACHT
  da50_2.png, da50_3.png, da50_4.png  ⏳ WACHT
  (TikToks: tt2/tt3/tt4_<pagina>.mp4 — JIJ levert)

────────────────────────────────────────────────────────
## 6. REELS-STRATEGIE (3/week, klaar in DA_REELS_STRATEGIE.md)
────────────────────────────────────────────────────────
30+: "De 14e eerste date" — maandag
40+: "Opnieuw beginnen na je 40e" — woensdag
50+: "Nooit te laat voor een nieuw hoofdstuk" — vrijdag
Elk: 15-20s, MJ-beeld of FLUX-video, caption + auto-comment CTA.

────────────────────────────────────────────────────────
## 7. FOUNDER STORY (geschreven, DA_FOUNDER_STORY.md)
────────────────────────────────────────────────────────
Hook: "Van 2013 tot nu: mijn eigen daten liep vast, dus ik bouwde er iets voor.
Toen stopte ik. Nu weet ik waarom ik terug ben."
Body: 2013 start → hielp duizenden 30+ → stopte (opgeraakt) → terug met Iris
(AI, 24/7, geen oordeel). CTA in auto-comment.
Live wo 19/8 op hoofdpagina.

────────────────────────────────────────────────────────
## 8. VOLGORDE UITVOERING
────────────────────────────────────────────────────────
DONE  FB-systeem + cron + auto-comment + auto-reply + 30 posts gepland
DONE  3 openings + comeback live
JIJ   9 MJ-beelden (40+/50+) + IG-Business-koppeling + TikToks/blog-beeld
IK    na IG-koppeling: ig_id ophalen → IG draait mee
IK    na 12 posts: fb_history evaluatie → best_hour + boost-keuze + gepinde post
