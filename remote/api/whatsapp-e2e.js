// TEMP E2E TEST — verwijder na gebruik. Roept de ECHTE whatsapp.js-logica aan
// (claimMessage, throttled, tenantForNumber, converse, sendText) met een fake
// manager-bericht, zodat we end-to-end bewijzen dat Iris nu wél antwoordt.
// Stuurt een echt WhatsApp-bericht naar Vincents eigen nummer (allowed_from).
import { sql } from './_lib.js';
import { converse, MAX_TURNS } from './_iris_core.js';
import { sendText } from './_whatsapp_send.js';

export const config = { api: { bodyParser: false }, maxDuration: 60 };

async function claimMessage(messageId) {
  const rows = await sql`INSERT INTO whatsapp_processed (message_id, status) VALUES (${messageId}, 'received') ON CONFLICT (message_id) DO NOTHING RETURNING message_id`;
  return rows.length === 1;
}
async function alreadyReplied(messageId) {
  const rows = await sql`SELECT 1 FROM whatsapp_processed WHERE message_id = ${messageId} AND status = 'replied' LIMIT 1`;
  return rows.length === 1;
}
async function throttled(waId) {
  const rows = await sql`INSERT INTO whatsapp_throttle (wa_id, count_1h, count_1m, window_start) VALUES (${waId}, 1, 1, now())
    ON CONFLICT (wa_id) DO UPDATE SET count_1h = CASE WHEN whatsapp_throttle.window_start < now() - interval '1 hour' THEN 1 ELSE whatsapp_throttle.count_1h + 1 END,
    count_1m = CASE WHEN whatsapp_throttle.window_start < now() - interval '1 minute' THEN 1 ELSE whatsapp_throttle.count_1m + 1 END,
    window_start = CASE WHEN whatsapp_throttle.window_start < now() - interval '1 minute' THEN now() ELSE whatsapp_throttle.window_start END
    RETURNING count_1h, count_1m`;
  const { count_1h, count_1m } = rows[0];
  return count_1h > 20 || count_1m > 6;
}
async function tenantForNumber(phoneNumberId) {
  const rows = await sql`SELECT slug, whatsapp_allowed_from FROM tenants WHERE whatsapp_phone_number_id = ${phoneNumberId}`;
  return rows[0] || null;
}
async function loadThread(tenant, waId) {
  const rows = await sql`SELECT messages, project FROM whatsapp_threads WHERE tenant = ${tenant} AND wa_id = ${waId}`;
  return rows[0] || { messages: [], project: null };
}
async function saveThread(tenant, waId, messages, project, maxTurns) {
  const trimmed = messages.slice(-maxTurns);
  await sql`INSERT INTO whatsapp_threads (tenant, wa_id, messages, project, updated_at) VALUES (${tenant}, ${waId}, ${JSON.stringify(trimmed)}::jsonb, ${project}, now())
    ON CONFLICT (tenant, wa_id) DO UPDATE SET messages = EXCLUDED.messages, project = COALESCE(EXCLUDED.project, whatsapp_threads.project), updated_at = now()`;
}

export default async function handler(req, res) {
  const out = [];
  const log = (m) => out.push(String(m));
  const TEST_MSG_ID = '_e2e_' + Date.now();
  try {
    log('step: tenantForNumber');
    const tenantRow = await sql`SELECT slug, whatsapp_phone_number_id, whatsapp_allowed_from FROM tenants WHERE slug='weareimpact'`;
    const phoneNumberId = tenantRow[0].whatsapp_phone_number_id;
    const allowed = String(tenantRow[0].whatsapp_allowed_from || '').split(',').map((s) => s.trim()).filter(Boolean);
    log('phoneNumberId=' + (phoneNumberId ? 'SET' : 'EMPTY') + ' allowed=' + JSON.stringify(allowed));
    if (!phoneNumberId) throw new Error('geen whatsapp_phone_number_id');
    if (!allowed.length) throw new Error('geen whatsapp_allowed_from');

    log('step: claimMessage');
    await claimMessage(TEST_MSG_ID);
    if (await alreadyReplied(TEST_MSG_ID)) { log('already replied -> stop'); return done(); }

    log('step: throttled');
    if (await throttled(allowed[0])) { log('throttled -> drop'); return done(); }

    log('step: converse (LLM)');
    const convo = [{ role: 'user', content: 'Dit is een test van Hermes: zeg kort dat de WhatsApp-verbinding weer werkt.' }];
    const result = await converse('weareimpact', convo, 'whatsapp');
    log('LLM reply: ' + result.reply.slice(0, 200));

    log('step: saveThread');
    await saveThread('weareimpact', allowed[0], [...convo, { role: 'assistant', content: result.reply }], null, MAX_TURNS);

    log('step: sendText naar ' + allowed[0]);
    const sent = await sendText(phoneNumberId, allowed[0], '[E2E-test] ' + result.reply);
    log('sendText ok=' + sent);

    log('step: markReplied');
    await sql`UPDATE whatsapp_processed SET status='replied', processed_at=now() WHERE message_id = ${TEST_MSG_ID}`;
    log('E2E SUCCES — kijk op je WhatsApp of je het test-bericht hebt.');
  } catch (e) {
    log('E2E FAIL: ' + String(e.message || e).slice(0, 400));
    log('STACK: ' + String(e.stack || '').slice(0, 500));
  }
  function done() { res.statusCode = 200; res.setHeader('content-type', 'text/plain; charset=utf-8'); res.end(out.join('\n')); }
  return done();
}
