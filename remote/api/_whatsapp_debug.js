// TEMP DIAGNOSTIC — verwijder na gebruik. Draait dezelfde DB-calls als
// whatsapp.js en geeft de echte fout terug in de response body.
import { sql } from './_lib.js';

export const config = { api: { bodyParser: false }, maxDuration: 30 };

export default async function handler(req, res) {
  const out = [];
  const log = (m) => out.push(String(m));
  try {
    log('DB ok, neon bound');
    const t = await sql`SELECT slug, whatsapp_phone_number_id, whatsapp_allowed_from FROM tenants`;
    log('tenants rows=' + JSON.stringify(t.map((x) => ({
      slug: x.slug,
      pid: x.whatsapp_phone_number_id ? 'SET' : 'EMPTY',
      allowed: x.whatsapp_allowed_from || null,
    }))));
    const pid = t[0]?.whatsapp_phone_number_id || 'DUMMY';
    try {
      await sql`INSERT INTO whatsapp_processed (message_id, status) VALUES ('_diag_' || now()::text, 'received') ON CONFLICT (message_id) DO NOTHING RETURNING message_id`;
      log('claimMessage OK');
    } catch (e) { log('claimMessage FAIL: ' + String(e.message || e).slice(0, 300)); }
    try {
      await sql`INSERT INTO whatsapp_threads (tenant, wa_id, messages) VALUES ('weareimpact','_diag_','[]'::jsonb) ON CONFLICT (tenant, wa_id) DO UPDATE SET messages=EXCLUDED.messages`;
      log('saveThread OK');
    } catch (e) { log('saveThread FAIL: ' + String(e.message || e).slice(0, 300)); }
    try {
      const snap = await sql`SELECT count(*)::int c FROM context_snapshot WHERE tenant='weareimpact'`;
      log('context_snapshot count=' + snap[0].c);
    } catch (e) { log('context_snapshot FAIL: ' + String(e.message || e).slice(0, 300)); }
    try {
      const si = await sql`SELECT count(*)::int c FROM sync_items WHERE tenant='weareimpact' AND status='active'`;
      log('sync_items active=' + si[0].c);
    } catch (e) { log('sync_items FAIL: ' + String(e.message || e).slice(0, 300)); }
  } catch (e) {
    log('TOP-LEVEL FAIL: ' + String(e.message || e).slice(0, 400));
    log('STACK: ' + String(e.stack || '').slice(0, 600));
  }
  res.statusCode = 200;
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  res.end(out.join('\n'));
}
