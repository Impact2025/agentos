// Koppelt een WhatsApp-nummer (Meta phone_number_id) aan een tenant, en zet
// welke afzenders er met dat nummer mogen praten. Draait lokaal, nooit in
// Vercel.
//
// Gebruik:
//   node scripts/add-whatsapp.mjs <slug> <phone_number_id> <afzender1,afzender2,...>
//
// Voorbeeld (Vincent zelf, weareimpact-tenant, één toegestane afzender):
//   node scripts/add-whatsapp.mjs weareimpact 109876543210123 31612345678
//
// Afzenders in E.164 zónder '+' (zoals Meta ze aanlevert in het webhook-
// bericht, bv. "31612345678" voor een Nederlands 06-nummer). Opnieuw draaien
// voor dezelfde slug overschrijft de lijst — dus ook om iemand te ontkoppelen:
// laat het derde argument leeg om alles op dat nummer dicht te zetten.
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

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

const { sql } = await import('../api/_lib.js');

const [slug, phoneNumberId, allowedFromRaw] = process.argv.slice(2);
if (!slug || !phoneNumberId) {
  console.error('Gebruik: node scripts/add-whatsapp.mjs <slug> <phone_number_id> [afzender1,afzender2,...]');
  process.exit(1);
}

const allowed = String(allowedFromRaw || '')
  .split(',').map((s) => s.trim()).filter(Boolean);
for (const nr of allowed) {
  if (!/^\d{8,15}$/.test(nr)) {
    console.error(`'${nr}' ziet er niet uit als een E.164-nummer zonder '+' (verwacht 8-15 cijfers).`);
    process.exit(1);
  }
}

const tenant = (await sql`SELECT slug FROM tenants WHERE slug = ${slug}`)[0];
if (!tenant) {
  console.error(`Tenant '${slug}' bestaat niet — provisioneer 'm eerst met scripts/add-tenant.mjs.`);
  process.exit(1);
}

const clash = (await sql`
  SELECT slug FROM tenants WHERE whatsapp_phone_number_id = ${phoneNumberId} AND slug != ${slug}`)[0];
if (clash) {
  console.error(`Dit phone_number_id hoort al bij tenant '${clash.slug}' — één nummer kan maar bij één klant.`);
  process.exit(1);
}

await sql`
  UPDATE tenants
  SET whatsapp_phone_number_id = ${phoneNumberId}, whatsapp_allowed_from = ${allowed.join(',')}
  WHERE slug = ${slug}`;

console.log(`\nWhatsApp gekoppeld aan tenant '${slug}':`);
console.log(`  phone_number_id: ${phoneNumberId}`);
console.log(`  toegestane afzenders: ${allowed.length ? allowed.join(', ') : '(geen — het nummer antwoordt op niemand)'}`);
console.log('\nVergeet niet WHATSAPP_TOKEN, WHATSAPP_APP_SECRET en WHATSAPP_VERIFY_TOKEN in de Vercel-env');
console.log('te zetten (gedeeld over alle tenants — zie remote/README.md) en de webhook-URL in het Meta');
console.log(`App Dashboard te bevestigen: https://<jouw-domein>/api/whatsapp`);
