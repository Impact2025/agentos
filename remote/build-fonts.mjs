// Haalt de webfonts één keer op en zet ze naast de app, zodat de browser van
// je telefoon bij het openen van de review-gates niets bij Google hoeft op te
// halen. Draaien na een font- of icoonwijziging: `npm run build:fonts`.
//
// Twee besparingen zitten hierin:
//  - van Inter/JetBrains alleen de latin-subsets; de andere (cyrillisch,
//    grieks, vietnamees) komen in een Nederlandse app nooit in beeld;
//  - van Material Symbols alleen de iconen die de app echt gebruikt — de
//    volledige set is 1,1 MB voor een handvol pictogrammen.
import { writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import crypto from 'node:crypto';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const KEEP_SUBSET = /^(latin|latin-ext)$/;

// Een icoon dat hier niet uit komt rollen, rendert straks als losse tekst
// ("WARNING") in plaats van een pictogram. Dat is precies wat er gebeurde:
// `wb_twilight` (het Vandaag-tabje) en `cloud_off` stonden wél in de bron maar
// niet in de gebouwde subset — fonts.css was ouder dan de code — en
// `inbox`/`pending_actions` (de filterchips) waren zelfs onvindbaar, want die
// staan als derde element in een array-literal die geen van de patronen kende.
//
// De console-lijst als "controlemiddel" was daarmee geen controle: hij vraagt
// dat een mens hem naleest op het moment dat hij per ongeluk niet draait. Dus
// staat de gebruikte lijst nu in fonts/icons.txt en toetst `npm run
// assets:check` of die nog klopt met de bron — een verouderde subset valt dan
// door de mand in plaats van pas op het beginscherm van een telefoon.
function usedIcons() {
  const src = ['index.html', 'app.js']
    .map((f) => readFileSync(join(HERE, f), 'utf8')).join('\n');
  const names = new Set();
  for (const m of src.matchAll(/material-symbols-outlined[^>]*>\s*([a-z_]+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/icon:\s*'([a-z_]+)'/g)) names.add(m[1]);
  for (const m of src.matchAll(/ICON\s*=\s*\{([^}]*)\}/g)) {
    for (const v of m[1].matchAll(/'([a-z_]+)'/g)) names.add(v[1]);
  }
  // Tuples van de vorm ['sleutel', 'Label', 'icoonnaam'] — zo staan de
  // filterchips in het Actiecentrum erin.
  for (const m of src.matchAll(/\[\s*'[a-z_]+'\s*,\s*'[^']*'\s*,\s*'([a-z_]+)'\s*\]/g)) names.add(m[1]);
  // sectionOff(icoonnaam, ...) geeft het icoon als los stringargument mee, niet
  // als `material-symbols-outlined>naam` — dat patroon hierboven matcht dan
  // niet, en zo'n icoon rendert stil als tekst ("EVENT_BUSY") zodra een sectie
  // uitstaat, precies de plek waar niemand tijdens de bouw op let.
  for (const m of src.matchAll(/sectionOff\(\s*'([a-z_]+)'/g)) names.add(m[1]);
  return [...names].sort();
}

const ICONS = usedIcons();
console.log(`${ICONS.length} iconen: ${ICONS.join(' ')}\n`);

// Alleen de lijst nakijken (npm run assets:check), zonder iets te downloaden.
if (process.argv.includes('--check')) {
  const listFile = join(HERE, 'fonts', 'icons.txt');
  const built = readFileSync(listFile, 'utf8').trim().split(/\s+/).filter(Boolean);
  const missing = ICONS.filter((n) => !built.includes(n));
  if (missing.length) {
    console.error(`\nfonts.css is verouderd: ${missing.length} icoon(en) uit de bron zitten `
      + `niet in de gebouwde subset en renderen als tekst:\n  ${missing.join(' ')}\n`
      + 'Draai `npm run assets:fonts`.');
    process.exit(1);
  }
  console.log('De gebouwde subset dekt alle iconen in de bron.');
  process.exit(0);
}

const SOURCES = [
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    + '&family=JetBrains+Mono:wght@400;500&display=swap',
  // icon_names is de officiële subset-parameter voor Material Symbols; met het
  // generieke text= verlies je de ligatuur-tabel die de namen tot iconen maakt.
  'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1'
    + `&icon_names=${ICONS.join(',')}&display=swap`,
];

const FONT_DIR = join(HERE, 'fonts');
mkdirSync(FONT_DIR, { recursive: true });

let css = '/* Gegenereerd door build-fonts.mjs — niet met de hand bijwerken. */\n\n';
let total = 0;
const seen = new Map();

// Subset-gesneden URL's zien er uit als /l/font?kit=… zonder extensie, dus de
// bestandsnaam leiden we af uit de inhoud in plaats van uit het pad.
function fileNameFor(url, bytes) {
  const tail = url.split('/').pop().replace(/\?.*/, '');
  const digest = crypto.createHash('sha1').update(bytes).digest('hex').slice(0, 10);
  return /\.woff2$/.test(tail) ? `${digest}-${tail}` : `${digest}.woff2`;
}

for (const src of SOURCES) {
  const res = await fetch(src, { headers: { 'User-Agent': UA } });
  // Google geeft 400 op een onbekende icoonnaam. Zonder deze stop schreven we
  // een foutpagina weg als stylesheet en verdwenen álle iconen tegelijk.
  if (!res.ok) {
    console.error(`\nOphalen mislukt (HTTP ${res.status}) voor:\n  ${src}\n`
      + 'Bij de icoon-sheet betekent dit meestal een naam die niet bestaat.');
    process.exit(1);
  }
  const sheet = await res.text();

  for (const match of sheet.matchAll(/@font-face\s*\{[^}]*\}/g)) {
    const face = match[0];
    // Google zet boven elke @font-face een commentaar met de subsetnaam — maar
    // alleen bij de niet-gesneden sheets. Geen commentaar = niets te filteren.
    const before = sheet.slice(0, match.index);
    const comment = before.match(/\/\*\s*([\w-]+)\s*\*\/\s*$/);
    if (comment && !KEEP_SUBSET.test(comment[1])) continue;

    const url = (face.match(/url\((https:\/\/[^)]+)\)/) || [])[1];
    if (!url) continue;

    let name = seen.get(url);
    if (!name) {
      // De vier Inter-gewichten verwijzen naar dezelfde subset-bestanden;
      // zonder deze cache haal je elk bestand vier keer op.
      const bytes = Buffer.from(await (await fetch(url)).arrayBuffer());
      name = fileNameFor(url, bytes);
      writeFileSync(join(FONT_DIR, name), bytes);
      seen.set(url, name);
      total += bytes.length;
      console.log(`${(bytes.length / 1024).toFixed(0).padStart(5)} KB  ${name}`);
    }
    css += `${face.replace(url, `/fonts/${name}`)}\n\n`;
  }

  // De icoon-sheet levert ook de .material-symbols-outlined-klasse mee; zonder
  // die regels staat elk pictogram in de verkeerde grootte en richting.
  const rest = sheet.replace(/@font-face\s*\{[^}]*\}/g, '')
    .replace(/\/\*[^*]*\*\//g, '').trim();
  if (rest) css += `${rest}\n\n`;
}

writeFileSync(join(HERE, 'fonts.css'), css);
// Wat er écht in de subset zit, naast de subset zelf: dit is waar
// `assets:check` de bron tegenaan houdt.
writeFileSync(join(FONT_DIR, 'icons.txt'), `${ICONS.join('\n')}\n`);
console.log(`\nTotaal ${(total / 1024).toFixed(0)} KB in fonts/, stylesheet in fonts.css`);
