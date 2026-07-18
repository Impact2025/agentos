// Bridge-endpoint — alleen voor de lokale AgentOS-machine (bearer-token).
//   POST /api/bridge?op=push       → volledige actieve set items + briefing
//   GET  /api/bridge?op=decisions  → openstaande besluiten (pending)
//   POST /api/bridge?op=ack        → uitslag per besluit terugmelden
import { sql, json, requireBearer } from './_lib.js';
import { pushToAll } from './_push.js';

export default async function handler(req, res) {
  if (!requireBearer(req, res)) return;
  const op = (req.query && req.query.op) || '';

  try {
    if (op === 'push' && req.method === 'POST') return await push(req, res);
    if (op === 'decisions' && req.method === 'GET') return await decisions(res);
    if (op === 'ack' && req.method === 'POST') return await ack(req, res);
    if (op === 'notes' && req.method === 'GET') return await notes(res);
    if (op === 'notes-ack' && req.method === 'POST') return await notesAck(req, res);
    return json(res, 400, { error: `onbekende op '${op}'` });
  } catch (e) {
    console.error('bridge error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}

async function push(req, res) {
  const body = req.body || {};
  const items = Array.isArray(body.items) ? body.items : [];
  const keys = items.map((i) => i.key);

  // Full-state sync: upsert alles in één statement, archiveer wat verdween.
  // (xmax = 0) markeert échte inserts — dat zijn de items die een melding
  // waard zijn; een update van een bestaand item spamt niet opnieuw.
  let newTitles = [];
  if (items.length) {
    const returned = await sql`
      INSERT INTO sync_items (key, kind, dismiss_kind, item_id, title, project,
                              created_at, summary, actions, detail, status, updated_at)
      SELECT x.key, x.kind, x.dismiss_kind, x.item_id, x.title, x.project,
             x.created_at, x.summary, COALESCE(x.actions, '[]'::jsonb), x.detail,
             'active', now()
      FROM jsonb_to_recordset(${JSON.stringify(items)}::jsonb) AS x(
        key TEXT, kind TEXT, dismiss_kind TEXT, item_id TEXT, title TEXT,
        project TEXT, created_at TEXT, summary TEXT, actions JSONB, detail JSONB)
      ON CONFLICT (key) DO UPDATE SET
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
    WHERE status = 'active' AND NOT (key = ANY(${keys}))`;

  if (body.briefing && Object.keys(body.briefing).length) {
    await sql`INSERT INTO briefings (payload, generated_at)
              VALUES (${JSON.stringify(body.briefing)}::jsonb, now())`;
    await sql`DELETE FROM briefings WHERE id NOT IN
              (SELECT id FROM briefings ORDER BY generated_at DESC LIMIT 7)`;
  }
  // Opruimen: gearchiveerde items ouder dan 14 dagen.
  await sql`DELETE FROM sync_items WHERE status = 'archived'
            AND updated_at < now() - interval '14 days'`;

  // Melding bij écht nieuwe items. Nooit de sync laten falen op een push-fout.
  if (newTitles.length) {
    const body = newTitles.length === 1
      ? newTitles[0]
      : `${newTitles.length} nieuwe besluiten — o.a. ${newTitles[0]}`;
    try { await pushToAll({ title: 'Iris Remote — nieuw besluit', body, url: '/' }); }
    catch (e) { console.error('push after push-op failed', e); }
  }
  return json(res, 200, { ok: true, upserted: items.length, nieuw: newTitles.length });
}

async function decisions(res) {
  const rows = await sql`
    SELECT id, item_key, item_kind, item_id, action, payload
    FROM decisions WHERE status = 'pending' ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { decisions: rows });
}

async function notes(res) {
  const rows = await sql`
    SELECT id, text, created_at FROM notes
    WHERE status = 'pending' ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { notes: rows });
}

async function notesAck(req, res) {
  const ids = ((req.body && req.body.ids) || []).map(Number).filter(Number.isFinite);
  if (ids.length) {
    await sql`UPDATE notes SET status = 'synced' WHERE id = ANY(${ids})`;
  }
  return json(res, 200, { ok: true, acked: ids.length });
}

async function ack(req, res) {
  const acks = (req.body && req.body.acks) || [];
  const failed = [];
  for (const a of acks) {
    const status = a.status === 'applied' ? 'applied' : 'failed';
    const rows = await sql`UPDATE decisions SET status = ${status},
              result = ${String(a.result || '').slice(0, 500)}, decided_at = now()
              WHERE id = ${a.id} AND status = 'pending'
              RETURNING item_key`;
    if (status === 'failed' && rows.length) {
      failed.push(`${rows[0].item_key}: ${a.result || 'mislukt'}`);
    }
  }
  // Een mislukt besluit stond onderweg als "gedaan" in je hoofd — dat verdient
  // een melding, niet alleen een badge die je pas ziet als je de app opent.
  if (failed.length) {
    try {
      await pushToAll({
        title: 'Iris Remote — besluit mislukt',
        body: failed[0].slice(0, 160), url: '/',
      });
    } catch (e) { console.error('push after ack failed', e); }
  }
  return json(res, 200, { ok: true, acked: acks.length });
}
