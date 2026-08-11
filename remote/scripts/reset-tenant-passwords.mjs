// Rotatie van tenant-wachtwoorden voor Iris Remote (lokaal dev).
// Gebruik: node scripts/reset-tenant-passwords.mjs '<vincent>' '<nicole>'
// Leest DATABASE_URL uit ../.env.dev.local en gebruikt dezelfde hashPassword
// als de productie-app, zodat de login-check (checkPassword) straks matched.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');

// .env.dev.local inladen (DATABASE_URL, APP_PASSWORD, VAPID)
const envPath = path.join(root, '.env.dev.local');
for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
  const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
  if (m) process.env[m[1]] = m[2].trim();
}

const vinPw = process.argv[2];
const nicPw = process.argv[3];
if (!vinPw || !nicPw) {
  console.error('Gebruik: node scripts/reset-tenant-passwords.mjs "<vincent>" "<nicole>"');
  process.exit(1);
}

// _lib.js voert `neon(process.env.DATABASE_URL)` uit bij import → env eerst zetten.
const { hashPassword, sql } = await import('../api/_lib.js');

// Lokale kopie van verifyPassword (niet geëxporteerd uit _lib.js) om de hash
// achteraf te controleren zonder de plaintext opnieuw door de login te jagen.
function verify(pw, stored) {
  const [salt, hash] = String(stored || '').split(':');
  if (!salt || !hash) return false;
  const check = crypto.scryptSync(String(pw || ''), salt, 64);
  const buf = Buffer.from(hash, 'hex');
  if (check.length !== buf.length) return false;
  return crypto.timingSafeEqual(check, buf);
}

console.log('Wachtwoorden updaten…');
await sql`UPDATE tenants SET password_hash = ${hashPassword(vinPw)} WHERE slug = 'weareimpact'`;
await sql`UPDATE tenants SET password_hash = ${hashPassword(nicPw)} WHERE slug = 'nicole'`;

// Verificatie tegen de opgeslagen hash.
const v = await sql`SELECT password_hash FROM tenants WHERE slug = 'weareimpact'`;
const n = await sql`SELECT password_hash FROM tenants WHERE slug = 'nicole'`;
console.log('vincent hash match :', verify(vinPw, v[0].password_hash));
console.log('nicole  hash match :', verify(nicPw, n[0].password_hash));

// Eventuele brute-force-lock (eigen IP) wissen zodat inloggen meteen lukt.
await sql`DELETE FROM login_attempts`;
console.log('login_attempts gewist');

// Echte login-test tegen de draaiende dev-server (localhost valt terug op
// tenant weareimpact, dus alleen Vincent logt hier lokaal in).
try {
  const r = await fetch('http://localhost:8632/api/ui?op=login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: vinPw }),
  });
  const j = await r.json().catch(() => ({}));
  console.log('live login Vincent:', r.status, JSON.stringify(j).slice(0, 120));
} catch (e) {
  console.log('live login Vincent: kon server niet bereiken (', e.message, ')');
}
