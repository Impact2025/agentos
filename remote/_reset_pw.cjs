// Forceer Iris inlog met bekend wachtwoord — voor video-demo only.
// Draai vanaf remote/ ; laadt .env.dev.local handmatig + ../.env
const fs = require('fs');
const path = require('path');

// Handmatig .env.dev.local laden
const envPath = path.join(__dirname, '.env.dev.local');
const lines = fs.readFileSync(envPath, 'utf8').split('\n');
for (const line of lines) {
  const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
}

const { neon } = require('@neondatabase/serverless');
const crypto = require('crypto');

if (!process.env.DATABASE_URL) {
  console.error('Geen DATABASE_URL gevonden');
  process.exit(1);
}

const sql = neon(process.env.DATABASE_URL);

function hashPassword(pw) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(String(pw), salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

async function main() {
  const pw = process.argv[2] || 'WeAreImpact!Iris2026';
  console.log('Zet wachtwoord voor Vincent (weareimpact)…');

  await sql`UPDATE tenants SET password_hash = ${hashPassword(pw)} WHERE slug = 'weareimpact'`;
  console.log('Hash geüpdatet');

  // brute-force lock wissen
  await sql`DELETE FROM login_attempts`;
  console.log('login_attempts gewist');

  // Direct testen tegen de draaiende dev-server op :3470
  const res = await fetch('http://localhost:3470/api/ui?op=login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  });
  const body = await res.json().catch(() => ({}));
  console.log('Live login test:', res.status, JSON.stringify(body).slice(0, 120));
}

main().catch(e => { console.error(e); process.exit(1); });
