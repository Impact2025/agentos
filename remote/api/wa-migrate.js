// TEMP MIGRATION — verwijder na gebruik. Maakt alle whatsapp-tabellen idempotent
// aan zodat whatsapp.js dezelfde structuur vindt als schema.sql voorschrijft.
import { sql } from './_lib.js';

export const config = { api: { bodyParser: false }, maxDuration: 30 };

export default async function handler(req, res) {
  const out = [];
  const log = (m) => out.push(String(m));
  const run = async (label, fn) => {
    try { const r = await fn(); log(`${label}: OK ${r ?? ''}`.trim()); }
    catch (e) { log(`${label}: FAIL ${String(e.message || e).slice(0, 260)}`); }
  };

  await run('create whatsapp_threads', () => sql`
    CREATE TABLE IF NOT EXISTS whatsapp_threads (
      tenant TEXT NOT NULL, wa_id TEXT NOT NULL, messages JSONB NOT NULL DEFAULT '[]'::jsonb,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (tenant, wa_id))`);
  await run('alter threads project', () => sql`
    ALTER TABLE whatsapp_threads ADD COLUMN IF NOT EXISTS project TEXT`);
  await run('create whatsapp_escalations', () => sql`
    CREATE TABLE IF NOT EXISTS whatsapp_escalations (
      id SERIAL PRIMARY KEY, tenant TEXT NOT NULL, wa_id TEXT NOT NULL, phone_number_id TEXT NOT NULL,
      project TEXT, question TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
      reply_text TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), answered_at TIMESTAMPTZ)`);
  await run('create whatsapp_processed', () => sql`
    CREATE TABLE IF NOT EXISTS whatsapp_processed (
      message_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'received',
      processed_at TIMESTAMPTZ NOT NULL DEFAULT now())`);
  await run('create whatsapp_throttle', () => sql`
    CREATE TABLE IF NOT EXISTS whatsapp_throttle (
      wa_id TEXT PRIMARY KEY, count_1h INT NOT NULL DEFAULT 1, count_1m INT NOT NULL DEFAULT 1,
      window_start TIMESTAMPTZ NOT NULL DEFAULT now())`);
  await run('index processed_status', () => sql`
    CREATE INDEX IF NOT EXISTS idx_whatsapp_processed_status ON whatsapp_processed (status, processed_at)`);
  await run('index throttle_win', () => sql`
    CREATE INDEX IF NOT EXISTS idx_whatsapp_throttle_win ON whatsapp_throttle (window_start)`);

  // Na-migratie: bestaat elke tabel en heeft hij de verwachte kolommen?
  for (const t of ['whatsapp_threads', 'whatsapp_escalations', 'whatsapp_processed', 'whatsapp_throttle']) {
    try {
      const c = await sql`SELECT column_name FROM information_schema.columns WHERE table_name = ${t} ORDER BY ordinal_position`;
      log(`AFTER ${t}: ${c.map((x) => x.column_name).join(', ') || '(leeg!)'}`);
    } catch (e) { log(`AFTER ${t}: ERROR ${String(e.message || e).slice(0, 160)}`); }
  }

  res.statusCode = 200;
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  res.end(out.join('\n'));
}
