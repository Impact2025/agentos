# Canva Autofill — agents vullen je vaste template automatisch

Doel: elke keer dat Agent OS een "content pack" genereert, kopieert de agent je
bestaande **'Insta/FB advertenties Bewaardvoorjou'**-template in Canva en vult de
tekstvelden (kop / onderschrift) automatisch in — dus niet meer handmatig
overtypen. De code staat klaar in `backend/shared/canva.py` +
`backend/shared/social_content.py`. Dit document beschrijft de eenmalige
handmatige stappen die jij moet zetten voordat het écht loopt.

## Wat de agent NIET voor je kan doen
Canva's Autofill-api kan alléén tekst invullen in velden die je **van tevoren in
Canva hebt gemarkeerd als data-veld** in een **Brand Template**. Dus: jij zet de
template één keer klaar; daarna doet de agent de rest.

## Stap 1 — Canva Connect-app + credentials (één keer)
1. Ga naar https://www.canva.com/developers/console/apps en maak een app van
   type **Canva Connect API**.
2. Noteer **Client ID** en **Client Secret**; zet ze in `.env`:
   - `CANVA_CLIENT_ID=...`
   - `CANVA_CLIENT_SECRET=...`
3. Bij "OAuth Redirect" zet je dezelfde waarde als `CANVA_REDIRECT_URI` in `.env`
   (default `https://www.canva.com/design/`).
4. Vraag deze scopes aan: `design:content:write`, `design:meta:read`,
   `design:permission:read`, `brandtemplate:content:write`,
   `brandtemplate:meta:read`, `folder:write`, `asset:read`.

## Stap 2 — Refresh-token verkrijgen / vernieuwen
Je huidige refresh-token is verlopen (Canva gaf `invalid_grant`). Draai:
```
cd D:/APPS/agentos
.venv/Scripts/python.exe canva_reauth.py
```
Volg de geprinte URL, log in, plak de `code`, en het script schrijft de nieuwe
`CANVA_REFRESH_TOKEN` terug naar `.env`. (Daarna haalt de agent automatisch een
vers access-token bij elke generate.)

## Stap 3 — Je template omzetten naar een Brand Template met data-velden
1. Open je design `Insta/FB advertenties Bewaardvoorjou 3-7`
   (canva.com/design/DAHOTwTyKQo/…).
2. Markeer de tekstelementen die per post moeten wisselen:
   - Selecteer het tekstvak met de kop (bijv. "Ik heb niets nodig.")
     → rechtermuisknop / menu → **Als data-veld markeren** → noem het `Headline`.
   - Selecteer het onderschrift ("Behalve een manier om mijn verhalen te delen >>")
     → markeer als data-veld → noem het `Subtext`.
   - (Het veldnaampje moet exact matchen met `CANVA_TEMPLATE_FIELDS` in `.env`;
     default `headline=Headline,subtext=Subtext` → dus Canva-veld = `Headline` /
     `Subtext`.)
3. **Sla op als Brand Template**: Bestand → Opslaan als template (of
   "Publish as Brand Template" als je een Brand Kit hebt).
4. Het **design-id** staat in de URL: `canva.com/design/<ID>/edit`. Zet dat in
   `.env`:
   - `CANVA_BRAND_TEMPLATE_ID=DAHOTwTyKQo`  (jouw echte id)

Optioneel: `CANVA_FOLDER_ID=<folder-id>` zodat nieuwe designs netjes in één map
belanden. `CANVA_TEMPLATE_FIELDS` pas je alleen aan als je andere veldnamen gebruikt.

## Stap 4 — Testen
1. Herstart Agent OS (kill PID op :1250, start uvicorn opnieuw).
2. Ga naar de tab **Social Creatie**, kies project `Bewaardvoorjou`, typ een thema,
   klik **Genereer content pack**.
3. Open het pack. Bij de Beeld-brief zie je nu:
   - ✅ "Automatisch ingevuld uit template" + een **Open in Canva ↗**-knop naar het
     verse, ingevulde design.
   - En een **Open basis-template ↗**-knop naar je vaste template.
4. Klik **Open in Canva** → het nieuwe design heeft de juiste tekst uit de brief.

## Fallback-gedrag (als iets ontbreekt)
- Geen `CANVA_BRAND_TEMPLATE_ID` maar wél credentials → agent maakt een **leeg
  poster-design** (je typt zelf de tekst). Brief toont "⚠️ Leeg design aangemaakt".
- Geen credentials → agent maakt **geen** Canva-design; de vertrouwde
  Canva-ready **brief + Midjourney-prompt** blijft leidend (mens doet de rest).

## Endpoint-referentie (Canva Connect v1)
- Token: `POST https://api.canva.com/rest/v1/oauth/token`
- Autofill: `POST https://api.canva.com/rest/v1/brand-templates/{id}/autofill`
  body: `{"brand_template_id": id, "data": {"Headline": {"type":"text","text":"…"}}, "title": "…"}`
- Lege design: `POST https://api.canva.com/rest/v1/designs`
- Export PNG: `POST https://api.canva.com/rest/v1/designs/{id}/exports`
(Verificatie van exacte veldnamen tegen de actuele Canva-docs moet nog gebeuren
zodra web-toegang beschikbaar is — de code is geschreven tegen de gedocumenteerde
v1-structuur.)
