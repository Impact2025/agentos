// Bridge-endpoint — alleen voor lokale ImpactOS-machines (bearer-token → tenant).
//   POST /api/bridge?op=push       → volledige actieve set items + briefing
//   GET  /api/bridge?op=decisions  → openstaande besluiten (pending)
//   POST /api/bridge?op=ack        → uitslag per besluit terugmelden
import { sql, json, resolveBridgeTenant } from './_lib.js';
import { pushToAll } from './_push.js';
import { encrypt } from './_crypto.js';
import { sendText } from './_whatsapp_send.js';

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
    if (op === 'reminder' && req.method === 'POST') return await reminder(req, res, tenant);
    if (op === 'impact-lead' && req.method === 'POST') return await impactLead(req, res, tenant);
    if (op === 'impact-leads' && req.method === 'GET') return await impactLeads(res, tenant);
    if (op === 'impact-leads-ack' && req.method === 'POST') return await impactLeadsAck(req, res, tenant);
    if (op === 'workshop-lead' && req.method === 'POST') return await workshopLead(req, res, tenant);
    if (op === 'workshop-leads' && req.method === 'GET') return await workshopLeads(res, tenant);
    if (op === 'workshop-leads-ack' && req.method === 'POST') return await workshopLeadsAck(req, res, tenant);
    if (op === 'booking-lead' && req.method === 'POST') return await bookingLead(req, res, tenant);
    if (op === 'booking-leads' && req.method === 'GET') return await bookingLeads(res, tenant);
    if (op === 'booking-leads-ack' && req.method === 'POST') return await bookingLeadsAck(req, res, tenant);
    if (op === 'customer-notify' && req.method === 'POST') return await customerNotify(req, res, tenant);
    if (op === 'lsp-submissions' && req.method === 'GET') return await lspSubmissions(res, tenant);
    if (op === 'lsp-submissions-ack' && req.method === 'POST') return await lspSubmissionsAck(req, res, tenant);
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

  // Google-config voor live agenda/GSC zonder ImpactOS (zie context.py:
  // build_google_config). Nooit laten falen op de rest van de push — één
  // tenant met een kapotte sleutel mag de items/context-sync niet meeslepen.
  if (body.google && body.google.client_email && body.google.private_key) {
    try {
      const g = body.google;
      const encKey = encrypt(g.private_key);
      await sql`
        UPDATE tenants SET
          calendar_client_email = ${g.client_email},
          calendar_private_key_enc = ${encKey},
          calendar_calendar_id = ${g.calendar_id || null},
          calendar_busy_ids = ${Array.isArray(g.busy_ids) ? g.busy_ids.join(',') : (g.busy_ids || null)},
          calendar_sub = ${g.sub || null},
          gsc_sites = ${JSON.stringify(g.gsc_sites || [])}::jsonb,
          google_synced_at = now(),
          google_last_error = NULL,
          google_last_error_at = NULL
        WHERE slug = ${tenant}`;
    } catch (e) {
      console.error('google-config opslaan mislukt', tenant, e);
      await sql`UPDATE tenants SET google_last_error = ${String(e).slice(0, 300)},
                google_last_error_at = now() WHERE slug = ${tenant}`.catch(() => {});
    }
  }

  // Opruimen: gearchiveerde items ouder dan 14 dagen (deze tenant).
  await sql`DELETE FROM sync_items WHERE tenant = ${tenant} AND status = 'archived'
            AND updated_at < now() - interval '14 days'`;

  // Melding bij écht nieuwe items. Nooit de sync laten falen op een push-fout.
  if (newTitles.length) {
    const body = newTitles.length === 1
      ? newTitles[0]
      : `${newTitles.length} nieuwe besluiten — o.a. ${newTitles[0]}`;
    try { await pushToAll(tenant, { title: 'Impact OS Remote — nieuw besluit', body, url: '/' }); }
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

// Agenda-herinnering (1 uur van tevoren) vanaf de lokale machine: die kent
// het Meta-token niet (leeft alleen hier in Vercel, zie CLAUDE.md 14e-b), dus
// stuurt hij de tekst en versturen wij naar het EERSTE nummer in
// whatsapp_allowed_from — dat is Vincent zelf (zelfde afspraak als notifyMe
// in whatsapp.js). Geen WhatsApp gekoppeld voor deze tenant → ok:false, geen
// 500: de scheduler-job aan de andere kant telt dat gewoon als 0 verstuurd.
async function reminder(req, res, tenant) {
  const text = String((req.body && req.body.text) || '').trim();
  if (!text) return json(res, 400, { error: 'text ontbreekt' });
  const rows = await sql`
    SELECT whatsapp_phone_number_id, whatsapp_allowed_from FROM tenants WHERE slug = ${tenant}`;
  const t = rows[0];
  const managerNumber = String((t && t.whatsapp_allowed_from) || '')
    .split(',').map((s) => s.trim()).filter(Boolean)[0];
  if (!t || !t.whatsapp_phone_number_id || !managerNumber) {
    return json(res, 200, { ok: false, error: 'whatsapp niet gekoppeld voor deze tenant' });
  }
  const ok = await sendText(t.whatsapp_phone_number_id, managerNumber, text);
  return json(res, 200, { ok });
}

// Bevestiging/afwijzing naar een specifieke klánt (23 aug 2026) — anders dan
// reminder() hierboven, die altijd naar Vincent zelf gaat, gaat dit naar wie
// een afspraak via klant-Iris voorstelde (calendar/agent.py:
// notify_customer_outcome, na approve/reject). We sturen alleen naar een
// wa_id dat al een whatsapp_threads-rij heeft voor deze tenant — dat is
// altijd zo voor een klant-Iris-gesprek, en het is een goedkope tweede grens
// tegen misbruik van BRIDGE_TOKEN als "stuur naar willekeurig nummer"-tool.
// Zelfde thread-continuïteit als whatsappReply in ui.js: de bevestiging komt
// terug in whatsapp_threads.messages, zodat een latere "hier is mijn
// mailadres" van de klant in context staat (deel_emailadres in
// _customer_core.js) in plaats van uit de lucht te vallen.
async function customerNotify(req, res, tenant) {
  const waId = String((req.body && req.body.wa_id) || '').trim();
  const text = String((req.body && req.body.text) || '').trim();
  if (!waId || !text) return json(res, 400, { error: 'wa_id en text zijn verplicht' });

  const [t] = await sql`SELECT whatsapp_phone_number_id FROM tenants WHERE slug = ${tenant}`;
  if (!t || !t.whatsapp_phone_number_id) {
    return json(res, 200, { ok: false, error: 'whatsapp niet gekoppeld voor deze tenant' });
  }
  const [thread] = await sql`
    SELECT messages FROM whatsapp_threads WHERE tenant = ${tenant} AND wa_id = ${waId}`;
  if (!thread) {
    return json(res, 200, { ok: false, error: 'geen bekend gesprek met dit nummer' });
  }

  const ok = await sendText(t.whatsapp_phone_number_id, waId, text);
  if (ok) {
    try {
      const messages = thread.messages || [];
      messages.push({ role: 'assistant', content: text });
      const trimmed = messages.slice(-10);
      await sql`
        UPDATE whatsapp_threads SET messages = ${JSON.stringify(trimmed)}::jsonb, updated_at = now()
        WHERE tenant = ${tenant} AND wa_id = ${waId}`;
    } catch (e) {
      console.error('customerNotify: thread-update mislukt (niet fataal)', e);
    }
  }
  return json(res, 200, { ok });
}

// Impact Calculator (weareimpact.nl): de website roept dit zelf aan zodra een
// bezoeker het dashboard ontgrendelt. Geen tenant-content om te archiveren
// (zie push()) — dit is een gebeurtenis, geen state, dus gewoon een insert.
async function impactLead(req, res, tenant) {
  const body = req.body || {};
  const email = String(body.email || '').trim();
  if (!email || !email.includes('@')) {
    return json(res, 400, { error: 'ongeldig e-mailadres' });
  }
  await sql`
    INSERT INTO impact_leads (tenant, email, naam, organisatie, inputs, results)
    VALUES (${tenant}, ${email}, ${body.naam || null}, ${body.organisatie || null},
            ${JSON.stringify(body.inputs || {})}::jsonb,
            ${JSON.stringify(body.results || {})}::jsonb)`;
  return json(res, 200, { ok: true });
}

async function impactLeads(res, tenant) {
  const rows = await sql`
    SELECT id, email, naam, organisatie, inputs, results, created_at
    FROM impact_leads WHERE tenant = ${tenant} AND status = 'pending'
    ORDER BY created_at ASC LIMIT 20`;
  return json(res, 200, { leads: rows });
}

async function impactLeadsAck(req, res, tenant) {
  const acks = (req.body && req.body.acks) || [];
  for (const a of acks) {
    const status = a.status === 'processed' ? 'processed' : 'failed';
    await sql`
      UPDATE impact_leads SET status = ${status},
             error = ${String(a.error || '').slice(0, 500) || null}, processed_at = now()
      WHERE id = ${a.id} AND tenant = ${tenant} AND status = 'pending'`;
  }
  return json(res, 200, { ok: true, acked: acks.length });
}

// AI Leadership Lab-leads (weareimpact.nl/lab) — zelfde pending/ack-vorm als
// impact_leads hierboven, zie schema.sql voor de motivatie.
async function workshopLead(req, res, tenant) {
  const body = req.body || {};
  const email = String(body.email || '').trim();
  if (!email || !email.includes('@')) {
    return json(res, 400, { error: 'ongeldig e-mailadres' });
  }
  await sql`
    INSERT INTO workshop_leads (tenant, email, naam, organisatie, rol, page_views)
    VALUES (${tenant}, ${email}, ${body.naam || null}, ${body.organisatie || null},
            ${body.rol || null}, ${JSON.stringify(body.pageViews || [])}::jsonb)`;
  return json(res, 200, { ok: true });
}

async function workshopLeads(res, tenant) {
  const rows = await sql`
    SELECT id, email, naam, organisatie, rol, page_views, created_at
    FROM workshop_leads WHERE tenant = ${tenant} AND status = 'pending'
    ORDER BY created_at ASC LIMIT 20`;
  return json(res, 200, { leads: rows });
}

async function workshopLeadsAck(req, res, tenant) {
  const acks = (req.body && req.body.acks) || [];
  for (const a of acks) {
    const status = a.status === 'processed' ? 'processed' : 'failed';
    await sql`
      UPDATE workshop_leads SET status = ${status},
             error = ${String(a.error || '').slice(0, 500) || null}, processed_at = now()
      WHERE id = ${a.id} AND tenant = ${tenant} AND status = 'pending'`;
  }
  return json(res, 200, { ok: true, acked: acks.length });
}

// Boekingsaanvragen (26 aug 2026, weareimpact.nl) — anders dan impact_leads/
// workshop_leads hierboven is dit geen eenmalig feit maar een levenscyclus
// (pending -> approved/rejected). De website pusht daarom bij élke
// statuswijziging opnieuw met hetzelfde bookingRequestId; de upsert zet
// status terug op 'pending' zodat ImpactOS 'm opnieuw oppikt, ongeacht wat
// de vorige sync ermee deed. Zie backend/domains/bridge/booking_leads.py.
async function bookingLead(req, res, tenant) {
  const body = req.body || {};
  const bookingRequestId = String(body.bookingRequestId || '').trim();
  const email = String(body.customerEmail || '').trim();
  if (!bookingRequestId || !email || !email.includes('@')) {
    return json(res, 400, { error: 'ongeldige boekingsgegevens' });
  }
  await sql`
    INSERT INTO booking_leads (
      tenant, booking_request_id, booking_type, start_time, duration_minutes,
      customer_name, customer_email, customer_phone, customer_organization,
      notes, booking_status
    ) VALUES (
      ${tenant}, ${bookingRequestId}, ${body.bookingType || null},
      ${body.startTime || null}, ${body.durationMinutes || null},
      ${body.customerName || null}, ${email}, ${body.customerPhone || null},
      ${body.customerOrganization || null}, ${body.notes || null},
      ${body.bookingStatus || 'pending'}
    )
    ON CONFLICT (tenant, booking_request_id) DO UPDATE SET
      booking_status = EXCLUDED.booking_status,
      status = 'pending',
      error = null,
      updated_at = now()`;
  return json(res, 200, { ok: true });
}

async function bookingLeads(res, tenant) {
  const rows = await sql`
    SELECT id, booking_request_id, booking_type, start_time, duration_minutes,
           customer_name, customer_email, customer_phone, customer_organization,
           notes, booking_status, created_at, updated_at
    FROM booking_leads WHERE tenant = ${tenant} AND status = 'pending'
    ORDER BY created_at ASC LIMIT 20`;
  return json(res, 200, { leads: rows });
}

async function bookingLeadsAck(req, res, tenant) {
  const acks = (req.body && req.body.acks) || [];
  for (const a of acks) {
    const status = a.status === 'processed' ? 'processed' : 'failed';
    await sql`
      UPDATE booking_leads SET status = ${status},
             error = ${String(a.error || '').slice(0, 500) || null}, processed_at = now()
      WHERE id = ${a.id} AND tenant = ${tenant} AND status = 'pending'`;
  }
  return json(res, 200, { ok: true, acked: acks.length });
}

// LSP-workshop (24 aug 2026): zelfde pull-vorm als impact_leads hierboven —
// de rij bestaat al volledig gevuld (WhatsApp verstuurt het rapport al zelf,
// zie whatsapp.js), dit haalt hem alleen op zodat er ook een Actiecentrum-
// kaart bij Vincent verschijnt (backend/domains/bridge/lsp_workshop.py).
async function lspSubmissions(res, tenant) {
  const rows = await sql`
    SELECT id, source, sender, contact_name, team_label, note_text, agent_type,
           dashboard_summary, participant_report, status, error, created_at
    FROM lsp_submissions WHERE tenant = ${tenant} AND impactos_synced = false
    ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { submissions: rows });
}

async function lspSubmissionsAck(req, res, tenant) {
  const ids = ((req.body && req.body.ids) || []).map(Number).filter(Number.isFinite);
  if (ids.length) {
    await sql`UPDATE lsp_submissions SET impactos_synced = true
              WHERE tenant = ${tenant} AND id = ANY(${ids})`;
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
        title: 'Impact OS Remote — besluit mislukt',
        body: failed[0].slice(0, 160), url: '/',
      });
    } catch (e) { console.error('push after ack failed', e); }
  }
  return json(res, 200, { ok: true, acked: acks.length });
}
