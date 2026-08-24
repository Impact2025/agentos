// Provisioneert (of roteert) één klant in de multi-tenant Neon-database.
// Draait lokaal, nooit in Vercel — het wachtwoord dat je hier intikt gaat
// nooit over het netwerk in platte tekst en wordt alleen gehasht opgeslagen.
//
// Gebruik:
//   node scripts/add-tenant.mjs <slug> "<weergavenaam>"
//
// Voorbeeld:
//   node scripts/add-tenant.mjs nicole "WE SHAPE THE FUTURE"
//
// Print aan het eind het BRIDGE_TOKEN dat je in de lokale ImpactOS-.env van
// die klant moet zetten (agentos_service_<slug>.cmd) — dat token is daarna
// nergens in leesbare vorm meer op te vragen, alleen zijn hash staat in de
// database. Opnieuw draaien voor een bestaande slug roteert wachtwoord én
// token (de oude gaan dan meteen ongeldig).
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import crypto from 'node:crypto';
import readline from 'node:readline';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(HERE, '..');

for (const file of ['.env.dev.local', '.env']) {
  const path = join(ROOT, file);
  if (!existsSync(path)) continue;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
}
if (!process.env.DATABASE_URL) {
  console.error('DATABASE_URL ontbreekt (zet hem in .env.dev.local of .env in remote/).');
  process.exit(1);
}

// Hergebruikt dezelfde hash-functies als de live API (_lib.js) — twee
// implementaties van "hoe hashen we een wachtwoord" is precies hoe zoiets
// uit elkaar loopt.
const { sql, hashPassword, weakPassword, MIN_PASSWORD_LENGTH } = await import('../api/_lib.js');

const [slug, name] = process.argv.slice(2);
if (!slug || !name) {
  console.error('Gebruik: node scripts/add-tenant.mjs <slug> "<weergavenaam>"');
  process.exit(1);
}
if (!/^[a-z0-9-]+$/.test(slug)) {
  console.error(`Ongeldige slug '${slug}' — alleen a-z, 0-9 en '-' (dit wordt ook het subdomein).`);
  process.exit(1);
}

function hashToken(t) {
  return crypto.createHash('sha256').update(String(t)).digest('hex');
}

// Wachtwoord maskeren tijdens het typen — Node heeft daar geen kant-en-klare
// functie voor; dit onderdrukt de terminal-echo tot Enter.
function askHidden(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const onData = (char) => {
      char = char.toString();
      if (char === '\n' || char === '\r' || char === '') return;
      process.stdout.write('\x1b[2K\x1b[200D' + question + '*'.repeat(rl.line.length));
    };
    process.stdin.on('data', onData);
    rl.question(question, (answer) => {
      process.stdin.removeListener('data', onData);
      rl.close();
      process.stdout.write('\n');
      resolve(answer);
    });
  });
}

async function main() {
  let password = await askHidden(`Wachtwoord voor '${slug}' (voor inloggen op de telefoon, min. ${MIN_PASSWORD_LENGTH} tekens): `);
  if (weakPassword(password)) {
    console.error(`Wachtwoord is te kort (${password.length}, minimaal ${MIN_PASSWORD_LENGTH}).`);
    process.exit(1);
  }

  const token = crypto.randomBytes(32).toString('hex');

  await sql`
    INSERT INTO tenants (slug, name, token_hash, password_hash)
    VALUES (${slug}, ${name}, ${hashToken(token)}, ${hashPassword(password)})
    ON CONFLICT (slug) DO UPDATE SET
      name = EXCLUDED.name,
      token_hash = EXCLUDED.token_hash,
      password_hash = EXCLUDED.password_hash`;

  console.log(`\nTenant '${slug}' (${name}) staat klaar.\n`);
  console.log('Zet dit in de lokale ImpactOS-.env van deze klant (agentos_service_' + slug + '.cmd):');
  console.log(`  BRIDGE_TOKEN=${token}`);
  console.log(`  BRIDGE_REMOTE_URL=<jouw Vercel-URL, bv. https://${slug}.<BASE_DOMAIN>>`);
  console.log('\nZonder BASE_DOMAIN in de Vercel-env draait alles nog op DEFAULT_TENANT — pas');
  console.log('wanneer een eigen domein + wildcard-subdomein staat, opent het subdomein deze klant.');
  console.log('\nDit token is nu de enige keer dat het in leesbare vorm verschijnt — bewaar het veilig.');
}

main().catch((e) => { console.error(e); process.exit(1); });
