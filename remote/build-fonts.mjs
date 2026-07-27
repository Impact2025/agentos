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
// ("warning") in plaats van een pictogram. De lijst die dit script afdrukt is
// dus het controlemiddel — loop hem na als je iconen toevoegt.
function usedIcons() {
  const src = ['index.html', 'app.js']
    .map((f) => readFileSync(join(HERE, f), 'utf8')).join('\n');
  const names = new Set();
  for (const m of src.matchAll(/material-symbols-outlined[^>]*>\s*([a-z_]+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/icon:\s*'([a-z_]+)'/g)) names.add(m[1]);
  for (const m of src.matchAll(/ICON\s*=\s*\{([^}]*)\}/g)) {
    for (const v of m[1].matchAll(/'([a-z_]+)'/g)) names.add(v[1]);
  }
  return [...names].sort();
}

const ICONS = usedIcons();
console.log(`${ICONS.length} iconen: ${ICONS.join(' ')}\n`);

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
  const sheet = await (await fetch(src, { headers: { 'User-Agent': UA } })).text();

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
console.log(`\nTotaal ${(total / 1024).toFixed(0)} KB in fonts/, stylesheet in fonts.css`);
