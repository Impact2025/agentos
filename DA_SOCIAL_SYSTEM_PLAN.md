# DatingAssistent — Wereldklasse Social System (integratieplan)

## Status nu (17 aug 2026)
- 3 DA-pagina's (30+/40+/50+) als aparte AgentOS-sites, eigen page-tokens in DB.
- 3 openingsposts LIVE (MJ-beeld + template, echt logo, leeftijd-badge, merknaam).
- Auto-comment module: na elke FB-post plaatst AgentOS een first-comment met
  leeftijd-specifieke CTA-link + hashtags (link-in-comment trick, bereik-vriendelijk).
- Cron-engine (elke 2u): plaatst de 9 geplande posts automatisch op hun tijd,
  met token-refresh + auto-comment. Idempotent.
- Reels/IG: code klaargezet, wacht op (a) volledige IG-Business-koppeling in Meta
  en (b) publieke image-host.

## De 8 hefbomen — concreet plan
1. INSTAGRAM  (grootste gemiste kans)
   - Nu: FB-pagina's hebben NOG GEEN ig_business_account (Graph geeft 'GEEN').
     Oorzaak: koppeling in Meta is niet volledig (wel cross-post, geen IG-Business).
   - JIJ doet: DA-IG-accounts → Business/Creator maken + in Business Manager
     koppelen aan de FB-pagina (Page settings → Instagram → "Connect").
   - IK doe (klaar zodra jij koppelt):
     * ig_id ophalen + opslaan in sites.instagram_business_id
     * engine post FB + IG in één run (zelfde beeld + caption)
     * vereist publieke image_url → zie punt 9 (host)
2. VIDEO / REELS  (veel meer bereik dan statisch)
   - Wekelijks 1 Reel per doelgroep (3/week): 15s tip-film.
   - IK: script + MJ-prompt + productie-handleiding; JIJ: beeld (of ik FLUX-video).
   - FB Reels via dezelfde /photos-upload (video ondersteunt FB native).
   - IG Reels via /media + /media_publish (vereist publieke video-url).
3. ECHTE VRAAG IN POST  (reacties = bereik, géén engagement-bait)
   - IK: elke post-kop krijgt een natuurlijke vraag-hook
     ("Herken jij de 14e eerste date?" / "Wat is jouw grootste dating-ergernis?").
   - Geen "Tag 3 vrienden!!" — dat onderdrukt FB.
4. SNEL REAGEREN OP COMMENTS  (responstijd → feed-ranking)
   - IK: auto-reply draadje op comments (welkom + link naar gids),
     aangestuurd vanuit social_inbox. Binnen 1u reactie.
5. FACEBOOK GROEPEN  (gratis extra bereik, geen spam)
   - IK: lijst relevante NL dating/relatie-groepen; JIJ deelt als lid.
   - Regel: nooit dezelfde link >1x; altijd waarde eerst.
6. GEPINDE POST  (vaste CTA)
   - IK: na 12 posts zet ik 1 post vast per pagina met de registratie-CTA.
7. KLEINE BETAALDE BOOST  (voorspelbaar, legaal)
   - Optie: €5-10/post op beste post via Business Manager.
   - IK: markeer 'beste' posts uit fb_history; JIJ beslist boost-budget.
8. TIMING UIT EIGEN DATA  (fb_history best_posting_hour)
   - Nu: posts op gevarieerde uren (09-21u) om data te verzamelen.
   - IK: na 12 posts → best_hour per pagina → volgende reeks post op die uren.
9. PUBLIEKE HOST (enabler voor IG/Reels)
   - IG vereist publieke image_url. IK zet een upload-naar-host functie klaar
     (Netlify Drop of Imgur API) → asset wordt publiek vóór IG-post.

## Volgorde van uitvoering (mijn kant, hands-off na groen licht)
A. [NU] Host-module bouwen (Imgur/Netlify) + IG-post integratie in engine.
B. [JIJ] IG-Business-koppeling in Meta afronden → ik haal ids + zet IG aan.
C. [NU] Reel-strategie schrijven (3 scripts + MJ-prompts per week).
D. [NU] Vraag-hooks in post-copy; auto-reply draadje op comments.
E. [NA 12 POSTS] fb_history evaluatie → best_hour + boost-keuze.

## Bestanden
- backend/shared/facebook.py ............ comment_on_post + refresh_page_tokens
- backend/shared/social_auto_comment.py  auto-comment (CTA-link + hashtags)
- backend/shared/instagram.py ........... (bestaand) IG-post via publieke url
- scripts/da_template.py ................ wereldklasse kaart (echt logo)
- scripts/da_post_engine.py ............. cron-engine (FB + binnenkort IG)
- scripts/run_da_engine.sh ............. cron-wrapper
- cronjob 'DA post-engine (2u)' ......... elke 2u
