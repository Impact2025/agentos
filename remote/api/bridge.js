// Bridge-endpoint — alleen voor lokale AgentOS-machines (bearer-token → tenant).
//   POST /api/bridge?op=push       → volledige actieve set items + briefing
//   GET  /api/bridge?op=decisions  → openstaande besluiten (pending)
//   POST /api/bridge?op=ack        → uitslag per besluit terugmelden
import { sql, json, resolveBridgeTenant } from './_lib.js';
import { pushToAll } from './_push.js';

export default async function handler(req, res) {
  const tenant = await resolveBridgeTenant(req, res);
  if (!tenant) return;
  const op = (req.query && req.query.op) || '';

  try {
    if (op === 'push' && req.method === 'POST') return await push(req, res, tenant);
    if (op === 'decisions' && req.method === 'GET') return await decisions(res, tenant);
    if (op === 'ack' && req.method === 'POST') return await ack(req, res, tenant);
    if (op === 'notes' && req.method === 'GET') return await notes(res, tenant);
    if (op === 'notes-ack' && req.method === 'POST') return await notesAck(req, res, tenant);
    return json(res, 400, { error: `onbekende op '${op}'` });
  } catch (e) {
    console.error('bridge error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}

async function push(req, res, tenant) {
  const body = req.body || {};
  const items = Array.isArray(body.items) ? body.items : [];
  const keys = items.map((i) => i.key);

  // Full-state sync: upsert alles in één statement, archiveer wat verdween.
  // (xmax = 0) markeert échte inserts — dat zijn de items die een melding
  // waard zijn; een update van een bestaand item spamt niet opnieuw.
  let newTitles = [];
  if (items.length) {
    const returned = await sql`
      INSERT INTO sync_items (tenant, key, kind, dismiss_kind, item_id, title, project,
                              created_at, summary, actions, detail, status, updated_at)
      SELECT ${tenant}, x.key, x.kind, x.dismiss_kind, x.item_id, x.title, x.project,
             x.created_at, x.summary, COALESCE(x.actions, '[]'::jsonb), x.detail,
             'active', now()
      FROM jsonb_to_recordset(${JSON.stringify(items)}::jsonb) AS x(
        key TEXT, kind TEXT, dismiss_kind TEXT, item_id TEXT, title TEXT,
        project TEXT, created_at TEXT, summary TEXT, actions JSONB, detail JSONB)
      ON CONFLICT (tenant, key) DO UPDATE SET
        kind = EXCLUDED.kind, title = EXCLUDED.title, project = EXCLUDED.project,
        created_at = EXCLUDED.created_at, summary = EXCLUDED.summary,
        actions = EXCLUDED.actions, detail = EXCLUDED.detail,
        status = 'active', updated_at = now()
      RETURNING key, (xmax = 0) AS inserted`;
    const newKeys = new Set(returned.filter((r) => r.inserted).map((r) => r.key));
    newTitles = items.filter((i) => newKeys.has(i.key)).map((i) => i.title || i.key);
  }
  await sql`
    UPDATE sync_items SET status = 'archived', updated_at = now()
    WHERE tenant = ${tenant} AND status = 'active' AND NOT (key = ANY(${keys}))`;

  if (body.briefing && Object.keys(body.briefing).length) {
    await sql`INSERT INTO briefings (tenant, payload, generated_at)
              VALUES (${tenant}, ${JSON.stringify(body.briefing)}::jsonb, now())`;
    await sql`DELETE FROM briefings WHERE tenant = ${tenant} AND id NOT IN
              (SELECT id FROM briefings WHERE tenant = ${tenant}
               ORDER BY generated_at DESC LIMIT 7)`;
  }
  // Context: precies één rij per tenant, elke sync overschreven. Een lege
  // context (de lokale opbouw faalde) mag de vorige nooit wissen — dan zou
  // een hik in Google's API het Vandaag-scherm leegmaken i.p.v. het oud tonen.
  if (body.context && Object.keys(body.context).length) {
    await sql`
      INSERT INTO context_snapshot (tenant, payload, generated_at)
      VALUES (${tenant}, ${JSON.stringify(body.context)}::jsonb, now())
      ON CONFLICT (tenant) DO UPDATE SET payload = EXCLUDED.payload,
                                         generated_at = EXCLUDED.generated_at`;
  }

  // Opruimen: gearchiveerde items ouder dan 14 dagen (deze tenant).
  await sql`DELETE FROM sync_items WHERE tenant = ${tenant} AND status = 'archived'
            AND updated_at < now() - interval '14 days'`;

  // Melding bij écht nieuwe items. Nooit de sync laten falen op een push-fout.
  if (newTitles.length) {
    const body = newTitles.length === 1
      ? newTitles[0]
      : `${newTitles.length} nieuwe besluiten — o.a. ${newTitles[0]}`;
    try { await pushToAll(tenant, { title: 'Iris Remote — nieuw besluit', body, url: '/' }); }
    catch (e) { console.error('push after push-op failed', e); }
  }
  return json(res, 200, { ok: true, upserted: items.length, nieuw: newTitles.length });
}

async function decisions(res, tenant) {
  const rows = await sql`
    SELECT id, item_key, item_kind, item_id, action, payload
    FROM decisions WHERE tenant = ${tenant} AND status = 'pending'
    ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { decisions: rows });
}

async function notes(res, tenant) {
  const rows = await sql`
    SELECT id, text, created_at FROM notes
    WHERE tenant = ${tenant} AND status = 'pending' ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { notes: rows });
}

async function notesAck(req, res, tenant) {
  const ids = ((req.body && req.body.ids) || []).map(Number).filter(Number.isFinite);
  if (ids.length) {
    await sql`UPDATE notes SET status = 'synced' WHERE tenant = ${tenant} AND id = ANY(${ids})`;
  }
  return json(res, 200, { ok: true, acked: ids.length });
}

async function ack(req, res, tenant) {
  const acks = (req.body && req.body.acks) || [];
  const failed = [];
  for (const a of acks) {
    const status = a.status === 'applied' ? 'applied' : 'failed';
    const rows = await sql`UPDATE decisions SET status = ${status},
              result = ${String(a.result || '').slice(0, 500)}, decided_at = now()
              WHERE id = ${a.id} AND tenant = ${tenant} AND status = 'pending'
              RETURNING item_key, action`;
    if (status === 'failed' && rows.length) {
      failed.push(`${rows[0].item_key}: ${a.result || 'mislukt'}`);
    }
    // Een refresh-token is de eerste écht gevoelige waarde die door dit kanaal
    // reist (elders is de payload al businessdata die toch in Neon staat, zie
    // schema.sql) — hij hoort er niet langer plat te blijven staan dan nodig.
    // Ongeacht geslaagd/mislukt: gelukt = niet meer nodig, mislukt = de
    // koppeling moet sowieso opnieuw vanaf stap 3.
    if (rows.length && rows[0].action === 'oauth_token_relay') {
      await sql`UPDATE decisions SET payload = '{}'::jsonb WHERE id = ${a.id} AND tenant = ${tenant}`;
    }
  }
  // Een mislukt besluit stond onderweg als "gedaan" in je hoofd — dat verdient
  // een melding, niet alleen een badge die je pas ziet als je de app opent.
  if (failed.length) {
    try {
      await pushToAll(tenant, {
        title: 'Iris Remote — besluit mislukt',
        body: failed[0].slice(0, 160), url: '/',
      });
    } catch (e) { console.error('push after ack failed', e); }
  }
  return json(res, 200, { ok: true, acked: acks.length });
}
