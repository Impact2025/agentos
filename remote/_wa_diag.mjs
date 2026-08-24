import { neon } from '@neondatabase/serverless';
import { readFileSync } from 'node:fs';
const env = readFileSync('D:/apps/impactos/.env.vercel_diag', 'utf8');
const kv = {};
for (const line of env.split('\n')) {
  const m = line.match(/^([A-Z_]+)=(.*)$/);
  if (m) kv[m[1]] = m[2].replace(/^"|"$/g, '');
}
const dbUrl = kv['DATABASE_URL'];
if (!dbUrl) { console.log('NO DATABASE_URL'); process.exit(1); }
const sql = neon(dbUrl);

console.log('=== tenants: whatsapp config ===');
const tenants = await sql`SELECT slug, whatsapp_phone_number_id, whatsapp_allowed_from FROM tenants`;
for (const t of tenants) {
  console.log('  slug=' + t.slug,
    '| phone_id=' + (t.whatsapp_phone_number_id ? 'SET(' + String(t.whatsapp_phone_number_id).slice(0,6) + '..)' : 'EMPTY'),
    '| allowed_from=' + (t.whatsapp_allowed_from ? JSON.stringify(t.whatsapp_allowed_from) : 'EMPTY'));
}

console.log('=== whatsapp_processed: recent claims (last 20) ===');
try {
  const rows = await sql`SELECT message_id, status, processed_at FROM whatsapp_processed ORDER BY processed_at DESC NULLS LAST LIMIT 20`;
  if (!rows.length) console.log('  (table empty)');
  for (const r of rows) console.log('  ' + r.status + ' | ' + String(r.processed_at).slice(0,19) + ' | ' + String(r.message_id).slice(0,40));
} catch (e) { console.log('  ERROR: ' + String(e.message).slice(0,120)); }

console.log('=== whatsapp_threads: recent (last 10) ===');
try {
  const rows = await sql`SELECT tenant, wa_id, project, updated_at FROM whatsapp_threads ORDER BY updated_at DESC NULLS LAST LIMIT 10`;
  if (!rows.length) console.log('  (table empty)');
  for (const r of rows) console.log('  tenant=' + r.tenant + ' | wa_id=' + r.wa_id + ' | project=' + r.project + ' | ' + String(r.updated_at).slice(0,19));
} catch (e) { console.log('  ERROR: ' + String(e.message).slice(0,120)); }

console.log('=== whatsapp_throttle: recent (last 10) ===');
try {
  const rows = await sql`SELECT wa_id, count_1h, count_1m, window_start FROM whatsapp_throttle ORDER BY window_start DESC NULLS LAST LIMIT 10`;
  if (!rows.length) console.log('  (table empty)');
  for (const r of rows) console.log('  wa_id=' + r.wa_id + ' | 1h=' + r.count_1h + ' | 1m=' + r.count_1m + ' | ' + String(r.window_start).slice(0,19));
} catch (e) { console.log('  ERROR: ' + String(e.message).slice(0,120)); }

console.log('=== whatsapp_escalations: open (last 10) ===');
try {
  const rows = await sql`SELECT id, tenant, wa_id, project, status, created_at FROM whatsapp_escalations ORDER BY created_at DESC NULLS LAST LIMIT 10`;
  if (!rows.length) console.log('  (table empty)');
  for (const r of rows) console.log('  #' + r.id + ' ' + r.status + ' | tenant=' + r.tenant + ' | wa_id=' + r.wa_id + ' | ' + String(r.created_at).slice(0,19));
} catch (e) { console.log('  ERROR: ' + String(e.message).slice(0,120)); }
