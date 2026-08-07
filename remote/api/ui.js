// UI-endpoint — voor de telefoon/browser (wachtwoord → intrekbare sessiecookie).
//   POST /api/ui?op=login       {password}   (met brute-force-rem per IP)
//   POST /api/ui?op=logout
//   POST /api/ui?op=logout-all  → trekt élk apparaat in
//   GET  /api/ui?op=sessions    → actieve apparaten
//   GET  /api/ui?op=items     → actieve items + besluit-status per item
//   GET  /api/ui?op=briefing  → laatste briefing (Iris + funnel)
//   POST /api/ui?op=decide    {item_key, action, payload}
//   POST /api/ui?op=note      {text}
import {
  sql, json, checkPassword, passwordConfigError, startSession, clearCookie,
  requireSession, endSession, endAllSessions, listSessions,
  loginLockSeconds, noteLoginFailure, clearLoginFailures,
} from './_lib.js';

// Welke acties de telefoon per item-type mag aanvragen. Moet een subset zijn
// van de whitelist in backend/domains/bridge/actions.py — de bridge weigert
// de rest toch, maar zo blijft de fout dicht bij de gebruiker.
const ALLOWED = {
  content: ['approve', 'reject', 'dismiss'],
  mail: ['send', 'edit', 'reject', 'dismiss'],
  personal_mail: ['send', 'reject', 'dismiss'],
  outreach: ['approve', 'reject', 'dismiss'],
  calendar: ['approve', 'reject', 'dismiss'],
  goal: ['dismiss'], task: ['dismiss'], error: ['dismiss'],
  vacancies: ['dismiss'], leads: ['dismiss'], linkbuilding: ['dismiss'],
  scheduler: ['dismiss'],
};

// Commando's die de telefoon mag aanzwengelen. Spiegel van `_COMMANDS` in
// backend/domains/bridge/actions.py — de bridge weigert de rest toch, maar zo
// blijft de fout bij de gebruiker in plaats van drie minuten later in een
// foutkaart. `fields` bepaalt welke payload-velden overleven; `label` is wat
// de UI en de chat terugmelden.
const COMMANDS = {
  content_run: { label: 'Artikelen schrijven → Wachtrij', fields: ['site', 'count'] },
  seo_refresh: { label: 'Wegzakkende pagina’s verrijken → Wachtrij', fields: ['site', 'count'] },
  outreach_run: { label: 'Outreach-concepten klaarzetten', fields: ['count'] },
  lead_search: { label: 'Nieuwe leads zoeken', fields: ['queries', 'template'] },
  linkbuilding_run: { label: 'Linkbuilding-concepten klaarzetten', fields: ['count'] },
  mail_sync: { label: 'Mail ophalen en triëren', fields: ['triage'] },
  helpdesk_run: { label: 'Helpdesk-concepten schrijven', fields: [] },
  iris_briefing: { label: 'Iris opnieuw laten analyseren', fields: [] },
  context_refresh: { label: 'Cijfers verversen', fields: ['sections'] },
  digest: { label: 'Ochtendrapport draaien', fields: [] },
};

export default async function handler(req, res) {
  const op = (req.query && req.query.op) || '';
  try {
    if (op === 'login' && req.method === 'POST') {
      // Een verkeerd geconfigureerd wachtwoord is een serverfout, geen
      // inlogpoging — anders staat de deur open zonder dat iemand het ziet.
      const configError = passwordConfigError();
      if (configError) return json(res, 503, { error: configError });

      const wait = await loginLockSeconds(req);
      if (wait > 0) {
        res.setHeader('Retry-After', String(wait));
        return json(res, 429, { error: 'Te veel pogingen', retry_after: wait });
      }
      if (!checkPassword((req.body || {}).password)) {
        const { wait: lock } = await noteLoginFailure(req);
        return json(res, 401, {
          error: lock > 0 ? `Onjuist wachtwoord — ${Math.ceil(lock / 60)} min geblokkeerd`
            : 'Onjuist wachtwoord',
          retry_after: lock,
        });
      }
      await clearLoginFailures(req);
      res.setHeader('Set-Cookie', await startSession(req));
      return json(res, 200, { ok: true });
    }
    if (op === 'logout' && req.method === 'POST') {
      await endSession(req);
      res.setHeader('Set-Cookie', clearCookie());
      return json(res, 200, { ok: true });
    }

    if (!(await requireSession(req, res))) return;

    if (op === 'sessions' && req.method === 'GET') {
      return json(res, 200, { sessions: await listSessions(req) });
    }
    if (op === 'logout-all' && req.method === 'POST') {
      await endAllSessions();
      res.setHeader('Set-Cookie', clearCookie());
      return json(res, 200, { ok: true });
    }

    if (op === 'items' && req.method === 'GET') return await items(res);
    if (op === 'briefing' && req.method === 'GET') return await briefing(res);
    if (op === 'context' && req.method === 'GET') return await context(res);
    if (op === 'decide' && req.method === 'POST') return await decide(req, res);
    if (op === 'command' && req.method === 'POST') return await command(req, res);
    if (op === 'note' && req.method === 'POST') return await note(req, res);
    if (op === 'notes' && req.method === 'GET') return await notesList(res);
    if (op === 'outbox' && req.method === 'GET') return await outbox(res);
    if (op === 'vapid' && req.method === 'GET') {
      return json(res, 200, { key: process.env.VAPID_PUBLIC_KEY || '' });
    }
    if (op === 'push-subscribe' && req.method === 'POST') return await pushSubscribe(req, res);
    if (op === 'push-unsubscribe' && req.method === 'POST') return await pushUnsubscribe(req, res);
    return json(res, 400, { error: `onbekende op '${op}'` });
  } catch (e) {
    console.error('ui error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}

async function items(res) {
  // Actieve items, mét het laatste besluit erbij zodat de UI kan tonen
  // "⏳ wacht op AgentOS" / "✓ uitgevoerd" / "⚠ mislukt: ...".
  const rows = await sql`
    SELECT i.*, d.status AS decision_status, d.action AS decision_action,
           d.result AS decision_result
    FROM sync_items i
    LEFT JOIN LATERAL (
      SELECT status, action, result FROM decisions
      WHERE item_key = i.key ORDER BY created_at DESC LIMIT 1
    ) d ON true
    WHERE i.status = 'active'
    ORDER BY i.updated_at DESC`;
  const last = await sql`
    SELECT max(updated_at) AS last_push FROM sync_items`;
  return json(res, 200, { items: rows, last_push: last[0]?.last_push || null });
}

async function briefing(res) {
  const rows = await sql`
    SELECT payload, generated_at FROM briefings
    ORDER BY generated_at DESC LIMIT 1`;
  return json(res, 200, rows[0] || { payload: null, generated_at: null });
}

async function decide(req, res) {
  const { item_key, action, payload } = req.body || {};
  const item = (await sql`
    SELECT key, dismiss_kind, item_id FROM sync_items WHERE key = ${item_key}`)[0];
  if (!item) return json(res, 404, { error: 'Item niet (meer) bekend' });
  if (!(ALLOWED[item.dismiss_kind] || []).includes(action)) {
    return json(res, 400, { error: `Actie '${action}' niet toegestaan op '${item.dismiss_kind}'` });
  }
  // Dubbel tikken: de partial-unique-index vangt de race; conflict = negeren.
  const rows = await sql`
    INSERT INTO decisions (item_key, item_kind, item_id, action, payload)
    VALUES (${item.key}, ${item.dismiss_kind}, ${item.item_id}, ${action},
            ${JSON.stringify(payload || {})}::jsonb)
    ON CONFLICT (item_key) WHERE status = 'pending' DO NOTHING
    RETURNING id`;
  return json(res, 200, { ok: true, queued: rows.length > 0 });
}

async function context(res) {
  const rows = await sql`
    SELECT payload, generated_at FROM context_snapshot WHERE id = 1`;
  return json(res, 200, rows[0] || { payload: null, generated_at: null });
}

async function command(req, res) {
  const { action, payload } = req.body || {};
  const spec = COMMANDS[action];
  if (!spec) return json(res, 400, { error: `Onbekend commando '${action}'` });

  // Alleen de velden die het commando kent gaan mee. Een payload die de
  // telefoon vrij mag vullen is een tweede API-oppervlak, en dat willen we
  // niet — de lokale kant klemt de waarden nóg een keer.
  const clean = {};
  for (const field of spec.fields) {
    if (payload && payload[field] !== undefined) clean[field] = payload[field];
  }
  // Eén pending commando van dezelfde soort tegelijk (partial unique index op
  // item_key): twee keer tikken op "Schrijf artikelen" moet één run geven.
  const key = `cmd:${action}`;
  const rows = await sql`
    INSERT INTO decisions (item_key, item_kind, item_id, action, payload)
    VALUES (${key}, 'command', ${action}, ${action}, ${JSON.stringify(clean)}::jsonb)
    ON CONFLICT (item_key) WHERE status = 'pending' DO NOTHING
    RETURNING id`;
  return json(res, 200, { ok: true, queued: rows.length > 0, label: spec.label });
}

async function notesList(res) {
  const rows = await sql`
    SELECT id, text, status, created_at FROM notes
    ORDER BY created_at DESC LIMIT 20`;
  return json(res, 200, { notes: rows });
}

async function outbox(res) {
  const rows = await sql`
    SELECT id, item_key, item_kind, action, status, result, created_at, decided_at
    FROM decisions ORDER BY created_at DESC LIMIT 20`;
  return json(res, 200, { decisions: rows });
}

async function pushSubscribe(req, res) {
  const sub = req.body || {};
  const keys = sub.keys || {};
  if (!sub.endpoint || !keys.p256dh || !keys.auth) {
    return json(res, 400, { error: 'Ongeldig push-abonnement' });
  }
  await sql`
    INSERT INTO push_subscriptions (endpoint, p256dh, auth)
    VALUES (${sub.endpoint}, ${keys.p256dh}, ${keys.auth})
    ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth`;
  return json(res, 200, { ok: true });
}

async function pushUnsubscribe(req, res) {
  const endpoint = (req.body || {}).endpoint || '';
  await sql`DELETE FROM push_subscriptions WHERE endpoint = ${endpoint}`;
  return json(res, 200, { ok: true });
}

async function note(req, res) {
  const text = String((req.body || {}).text || '').trim();
  if (!text) return json(res, 400, { error: 'Lege notitie' });
  await sql`INSERT INTO notes (text) VALUES (${text})`;
  return json(res, 200, { ok: true });
}
