// TEMP MIGRATION — verwijder na gebruik. Zorgt dat whatsapp_processed de
// kolommen heeft die whatsapp.js verwacht (status, processed_at) en rapporteert
// de echte structuur van watsapp_processed / whatsapp_throttle.
import { sql } from './_lib.js';

export const config = { api: { bodyParser: false }, maxDuration: 30 };

async function cols(table) {
  const rows = await sql`
    SELECT column_name, data_type, column_default, is_nullable
    FROM information_schema.columns WHERE table_name = ${table} ORDER BY ordinal_position`;
  return rows;
}

export default async function handler(req, res) {
  const out = [];
  const log = (m) => out.push(String(m));
  try {
    for (const t of ['whatsapp_processed', 'whatsapp_throttle']) {
      const c = await cols(t);
      log(`TABLE ${t}: ` + JSON.stringify(c.map((x) => ({ n: x.column_name, t: x.data_type, d: x.column_default, null: x.is_nullable }))));
    }
    // Idempotente migratie
    try {
      await sql`ALTER TABLE whatsapp_processed ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'received'`;
      log('ALTER whatsapp_processed.status OK');
    } catch (e) { log('ALTER status FAIL: ' + String(e.message || e).slice(0, 300)); }
    try {
      await sql`ALTER TABLE whatsapp_processed ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ NOT NULL DEFAULT now()`;
      log('ALTER whatsapp_processed.processed_at OK');
    } catch (e) { log('ALTER processed_at FAIL: ' + String(e.message || e).slice(0, 300)); }
    try {
      await sql`CREATE INDEX IF NOT EXISTS idx_whatsapp_processed_status ON whatsapp_processed (status, processed_at)`;
      log('index OK');
    } catch (e) { log('index FAIL: ' + String(e.message || e).slice(0, 200)); }
    // throttle kolommen
    try {
      await sql`ALTER TABLE whatsapp_throttle ADD COLUMN IF NOT EXISTS count_1h INT NOT NULL DEFAULT 1`;
      await sql`ALTER TABLE whatsapp_throttle ADD COLUMN IF NOT EXISTS count_1m INT NOT NULL DEFAULT 1`;
      await sql`ALTER TABLE whatsapp_throttle ADD COLUMN IF NOT EXISTS window_start TIMESTAMPTZ NOT NULL DEFAULT now()`;
      log('ALTER whatsapp_throttle cols OK');
    } catch (e) { log('ALTER throttle FAIL: ' + String(e.message || e).slice(0, 300)); }
    // Na-migratie structuur
    for (const t of ['whatsapp_processed', 'whatsapp_throttle']) {
      const c = await cols(t);
      log(`AFTER ${t}: ` + JSON.stringify(c.map((x) => x.column_name)));
    }
  } catch (e) {
    log('TOP FAIL: ' + String(e.message || e).slice(0, 400));
  }
  res.statusCode = 200;
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  res.end(out.join('\n'));
}
