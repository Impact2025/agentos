// Draait schema.sql tegen de database uit DATABASE_URL. Alles in dat bestand is
// IF NOT EXISTS, dus dit is veilig te herhalen en raakt bestaande data niet aan.
//   node migrate.mjs
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { neon } from '@neondatabase/serverless';

const HERE = fileURLToPath(new URL('.', import.meta.url));

for (const file of ['.env.dev.local', '.env']) {
  const path = join(HERE, file);
  if (!existsSync(path)) continue;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
}
if (!process.env.DATABASE_URL) {
  console.error('DATABASE_URL ontbreekt');
  process.exit(1);
}

const sql = neon(process.env.DATABASE_URL);
const raw = readFileSync(join(HERE, 'schema.sql'), 'utf8');

// Commentaarregels eruit, dan splitsen op ';'. Het schema bevat geen functies
// of dollar-quoting, dus dat is hier genoeg.
const statements = raw
  .split('\n').filter((l) => !l.trim().startsWith('--')).join('\n')
  .split(';').map((s) => s.trim()).filter(Boolean);

for (const stmt of statements) {
  const label = stmt.replace(/\s+/g, ' ').slice(0, 60);
  try {
    // De neon-client kent alleen tagged templates, geen sql.query(). Een
    // template-object met één vast deel en nul waarden is precies dat.
    await sql(Object.assign([stmt], { raw: [stmt] }));
    console.log(`  ok  ${label}…`);
  } catch (e) {
    console.error(`FOUT  ${label}…\n      ${e.message}`);
    process.exit(1);
  }
}
console.log(`\n${statements.length} statements toegepast.`);
