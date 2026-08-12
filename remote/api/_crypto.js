// Versleuteling voor klantcredentials in Neon (per-tenant Google-service-
// account, zie schema.sql). AES-256-GCM: authenticated encryption — een
// gemanipuleerde ciphertext faalt decrypt() hard i.p.v. stil corrupte data
// terug te geven.
//
// TENANT_SECRET_KEY is Vercel-only (nooit lokaal nodig): de lokale kant
// stuurt de rauwe sleutel over het al-geauthenticeerde bridge-kanaal
// (BRIDGE_TOKEN), Vercel versleutelt 'm pas bij ontvangst voor opslag.
import crypto from 'node:crypto';

const ALGO = 'aes-256-gcm';

function key() {
  const raw = process.env.TENANT_SECRET_KEY;
  if (!raw) throw new Error('TENANT_SECRET_KEY ontbreekt — live Google-koppeling staat uit.');
  const buf = Buffer.from(raw, 'base64');
  if (buf.length !== 32) {
    throw new Error('TENANT_SECRET_KEY moet 32 bytes zijn (base64) — genereer met `openssl rand -base64 32`.');
  }
  return buf;
}

// Formaat: "<iv-hex>:<tag-hex>:<ciphertext-hex>" — één string, makkelijk als
// TEXT-kolom op te slaan zonder een tweede kolom voor de auth-tag.
export function encrypt(plaintext) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGO, key(), iv);
  const enc = Buffer.concat([cipher.update(String(plaintext), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${iv.toString('hex')}:${tag.toString('hex')}:${enc.toString('hex')}`;
}

export function decrypt(stored) {
  const [ivHex, tagHex, encHex] = String(stored || '').split(':');
  if (!ivHex || !tagHex || !encHex) throw new Error('Ongeldig versleuteld formaat.');
  const decipher = crypto.createDecipheriv(ALGO, key(), Buffer.from(ivHex, 'hex'));
  decipher.setAuthTag(Buffer.from(tagHex, 'hex'));
  const dec = Buffer.concat([decipher.update(Buffer.from(encHex, 'hex')), decipher.final()]);
  return dec.toString('utf8');
}
