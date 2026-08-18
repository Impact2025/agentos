# Actiecentrum — wat je ziet en wat elke knop doet

Korte gids voor het dashboard (localhost:1250). Doel: in één oogopslag snappen
wat een taak is en wat er gebeurt als je klikt. Geen verrassingen meer.

## De tag boven elke kaart

De gekleurde tag zegt wat het is. Kleur = betekenis, niet decoratie.

| Tag | Kleur | Betekent | Publiceren? |
|-----|-------|----------|-------------|
| `Artikel · wordt gepubliceerd` | blauw | Een echt blogartikel | JA — "Publiceer" zet 'm live op de site |
| `SEO-hook · géén artikel` | geel | Losse hook/snippet, geen pagina | NEE — nooit als pagina |
| `LinkedIn · géén site-pagina` | geel | LinkedIn-outreach, geen site | NEE — jij plakt op LinkedIn |
| `Plan wacht op akkoord` | geel | Een doel/plan van een agent | NEE — start pas na jouw klik |
| `Vastgelopen doel` | rood | Iets liep vast | NEE — herstellen |
| `Fout` | rood | Echte fout die jouw actie vraagt | NEE |

**Gouden regel:** een gele of rode tag = er gebeurt niets op je site tot jij
bewust "Publiceer" of "Start" kiest bij een blauwe *Artikel*-kaart.

## Knoppen per soort taak

### Artikel (blauwe tag)
- `Bekijk in Wachtrij` — opent de Wachtrij-tab, alleen kijken.
- `Publiceer` — **zet het artikel echt live op je site** (incl. Google-index).
  Eén klik = online. Weet dus wat je doet.
- `Wijs af` — gooit het weg, niets gebeurt.

### SEO-hook / snippet (gele tag)
- `Bekijk in Wachtrij` — bekijken.
- `Gebruik in artikel` — opent de Wachtrij; bedoeld om 'm in een echt
  artikel te verwerken, niet om los te publiceren.
- `Wijs af` — weg.
- *Geen "Publiceer"-knop.* Mocht je 'm toch via de API proberen te
  publiceren, dan weigert het systeem hard (foutmelding, niets online).

### LinkedIn-outreach (gele tag)
- `Bekijk in Wachtrij` — bekijken.
- `Klaar voor LinkedIn` — markeert dat je de berichten zelf op LinkedIn mag
  plakken. **Publiceert niets op je site.**
- `Wijs af` — weg.

### Doel / plan (gele/blauwe tag)
- `Bevestig & start` / `Start nu` — laat de agent het uitvoeren.
- `Verwijder` — gooi het plan weg.

### Fout (rode tag)
- `Analyseer & fix` — laat Iris de fout diagnosticeren en herstellen.
- `Gezien, verberg` — verberg de kaart uit je inbox.

## Veiligheid die nu ingebouwd zit

- Een hook/snippet/outreach kan **nooit** als pagina op je site komen, ook
  niet via een directe API-call (harde weigering, HTTP 400).
- De generator herkent intussen hook/snippet-taken en labelt ze correct,
  dus ze belanden niet meer als `blog` in de wachtrij.
- Publiceren van een artikel gebeurt **alleen** via de groene "Publiceer"-
  knop bij een blauwe *Artikel*-kaart. Niets anders publiceert.

## Kort

Blauw + "Publiceer" = gaat live op je site.
Geel/rood = niets live tot jij een expliciete actie kiest.
Twijfel je? De tag zegt het.
