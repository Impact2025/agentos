// UI-endpoint — voor de telefoon/browser (wachtwoord → intrekbare sessiecookie).
// Welke klant (tenant) dit is, volgt uit het subdomein (zie _lib.js:tenantFromHost).
//   POST /api/ui?op=login       {password}   (met brute-force-rem per IP)
//   POST /api/ui?op=logout
//   POST /api/ui?op=logout-all  → trekt élk apparaat van deze tenant in
//   GET  /api/ui?op=sessions    → actieve apparaten (deze tenant)
//   GET  /api/ui?op=items     → actieve items + besluit-status per item
//   GET  /api/ui?op=briefing  → laatste briefing (Iris + funnel)
//   POST /api/ui?op=decide    {item_key, action, payload}
//   POST /api/ui?op=note      {text}
//   POST /api/ui?op=note-delete {id}  → alleen nog-niet-gesyncte notities
import {
  sql, json, tenantFromHost, checkPassword, tenantConfigError, startSession, clearCookie,
  requireSession, endSession, endAllSessions, listSessions,
  loginLockSeconds, noteLoginFailure, clearLoginFailures,
  resolveBridgeTenant,
} from './_lib.js';
import { attachLive } from './_google.js';
import { sendText } from './_whatsapp_send.js';

// Welke acties de telefoon per item-type mag aanvragen. Moet een subset zijn
// van de whitelist in backend/domains/bridge/actions.py — de bridge weigert
// de rest toch, maar zo blijft de fout dicht bij de gebruiker.
const ALLOWED = {
  content: ['approve', 'reject', 'dismiss'],
  mail: ['send', 'edit', 'reject', 'dismiss'],
  personal_mail: ['send', 'reject', 'dismiss'],
  social: ['send', 'edit', 'reject', 'dismiss'],
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
//
// `keyField` is er voor commando's die ergens ópslaan: twee keer "blokkeer deze
// afzender" op twee verschillende mails zijn twee besluiten, geen dubbele tik.
// Zonder dit veld deelt elk commando van dezelfde soort één dedupe-sleutel
// (`cmd:<action>`) en verdwijnt de tweede stilzwijgend — precies de faalmodus
// die dit systeem elders 'stil verdwijnen' noemt.
const COMMANDS = {
  content_run: { label: 'Artikelen schrijven → Wachtrij', fields: ['site', 'count'] },
  seo_refresh: { label: 'Wegzakkende pagina’s verrijken → Wachtrij', fields: ['site', 'count'] },
  outreach_run: { label: 'Outreach-concepten klaarzetten', fields: ['count'] },
  lead_search: { label: 'Nieuwe leads zoeken', fields: ['queries', 'template'] },
  linkbuilding_run: { label: 'Linkbuilding-concepten klaarzetten', fields: ['count'] },
  mail_sync: { label: 'Mail ophalen en triëren', fields: ['triage'] },
  mail_rule: {
    label: 'Afzender blokkeren', fields: ['email_id', 'email', 'scope', 'action'],
    keyField: 'email',
  },
  mail_archive: { label: 'Mail archiveren', fields: ['email_id'], keyField: 'email_id' },
  helpdesk_run: { label: 'Helpdesk-concepten schrijven', fields: [] },
  social_run: { label: 'Social-kanalen ophalen en beantwoorden', fields: ['inbox_id'] },
  iris_briefing: { label: 'Iris opnieuw laten analyseren', fields: [] },
  context_refresh: { label: 'Cijfers verversen', fields: ['sections'] },
  digest: { label: 'Ochtendrapport draaien', fields: [] },
  // Zware Gauntlet-escalatie voor 'stuck'/'rejected' content — bewust NIET
  // in remote/api/iris.js' COMMANDS (cloud-Iris' start_werk-whitelist), dus
  // alleen bereikbaar via een tik op de telefoon, nooit via de chat.
  orchestrator_run: { label: 'Vastgelopen stuk door de Gauntlet jagen', fields: [] },
  // Agenda-opdracht: vrije tekst/spraak -> afspraak-voorstel (review-gate).
  // 'text' bevat de volledige zin; de backend parsed datum/tijd/wie.
  // keyField=text zodat twee verschillende opdrachten niet op dezelfde
  // dedupe-sleutel botsen (elke zin is een eigen besluit).
  calendar_add: { label: 'Afspraak inplannen', fields: ['text'], keyField: 'text' },
  // Rituelen: eigen dagboek, geen review-gate (zie backend/domains/bridge/actions.py).
  // `nonce` als keyField — zonder dat blokkeert de partial-unique-index een
  // tweede log van dezelfde soort vóórdat de eerste gesynct is (bv. twee wins
  // na elkaar), en dat is precies de faalmodus die `mail_archive` hierboven al
  // met een keyField oplost.
  ritual_morning_save: {
    label: 'Ochtendritueel vastgelegd',
    fields: ['date', 'intentie', 'affirmatie', 'dankbaarheid', 'energyLevel', 'sleepQuality', 'nonce'],
    keyField: 'nonce',
  },
  ritual_evening_save: {
    label: 'Avondritueel vastgelegd',
    fields: ['date', 'whatWentWell', 'biggestWin', 'energyLevel', 'tomorrowTop3', 'gratitude', 'nonce'],
    keyField: 'nonce',
  },
  ritual_win_add: {
    label: 'Win vastgelegd',
    fields: ['title', 'description', 'category', 'impactLevel', 'nonce'],
    keyField: 'nonce',
  },
  ritual_goal_progress: {
    label: 'Doel bijgewerkt',
    fields: ['goal_id', 'progress', 'completed'],
    keyField: 'goal_id',
  },
  // Iris-onboarding-wizard (zie de onboarding-sectie in app.js). site_id is
  // het doelwit: twee verschillende klanten die tegelijk stap 1 indienen
  // mogen elkaar niet blokkeren, dus keyField=site_id i.p.v. de gedeelde
  // `cmd:<action>`-sleutel. oauth_token_relay staat hier BEWUST niet — die
  // decision schrijft remote/api/oauth.js rechtstreeks in Neon (na de
  // Google/Microsoft-consent-redirect), niet de telefoon via dit endpoint.
  onboarding_step1: { label: 'Bedrijfsdoel opgeslagen', fields: ['site_id', 'profile'], keyField: 'site_id' },
  onboarding_step2: { label: 'Schrijfstijl opgeslagen', fields: ['site_id', 'tone_text'], keyField: 'site_id' },
  onboarding_step4: { label: 'Werk-grenzen opgeslagen', fields: ['site_id', 'preset', 'overrides'], keyField: 'site_id' },
  onboarding_complete: { label: 'Onboarding afgerond', fields: ['site_id'], keyField: 'site_id' },
  onboarding_new_client: { label: 'Nieuwe klant aangemaakt', fields: ['name'], keyField: 'name' },
};

export default async function handler(req, res) {
  const op = (req.query && req.query.op) || '';
  const tenant = tenantFromHost(req);
  try {
    // ── Bridge-proxy variant van whatsapp-stats (VOÓR de sessie-check) ──────
    // De lokale AgentOS-backend (SQLite) bedient het dashboard op :1250, maar de
    // WhatsApp-data staat in de aparte Neon-Postgres van het remote-systeem.
    // In plaats van de twee databases aan elkaar te koppelen, proxy't de lokale
    // backend hiernaartoe met zijn BRIDGE_TOKEN — remote blijft bron van
    // waarheid, er worden géén DB-credentials gedeeld. resolveBridgeTenant
    // levert de tenant uit de token-hash, exact zoals de gewone bridge-push.
    // Moet vóór requireSession staan: de bridge authenticeert via Bearer-token,
    // niet via een UI-sessiecookie.
    if (op === 'whatsapp-stats-bridge' && req.method === 'GET') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return; // 401 al gestuurd door helper
      return await whatsappStats(res, bt);
    }
    // Zelfde reden en patroon als whatsapp-stats-bridge hierboven — Vincent
    // wil het volle Communicatie-overzicht (niet alleen de cijfers) ook op
    // :1250 zien, niet alleen op zijn telefoon via Iris Remote. De
    // handler-functies zelf zijn al tenant-parametrisch (geen sessie-state
    // erin), dus dit is puur een tweede, bearer-geauthenticeerde ingang naar
    // exact dezelfde logica — geen nieuwe waarheid, geen dubbele code.
    if (op === 'whatsapp-bridge' && req.method === 'GET') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return;
      return await whatsappList(res, bt);
    }
    if (op === 'whatsapp-conversations-bridge' && req.method === 'GET') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return;
      return await whatsappConversations(res, bt);
    }
    if (op === 'whatsapp-thread-bridge' && req.method === 'GET') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return;
      return await whatsappThread(req, res, bt);
    }
    if (op === 'whatsapp-reply-bridge' && req.method === 'POST') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return;
      return await whatsappReply(req, res, bt);
    }
    if (op === 'whatsapp-dismiss-bridge' && req.method === 'POST') {
      const bt = await resolveBridgeTenant(req, res);
      if (!bt) return;
      return await whatsappDismiss(req, res, bt);
    }
    if (op === 'login' && req.method === 'POST') {
      // Een onbekende/niet-geprovisioneerde tenant is een serverfout, geen
      // inlogpoging — anders staat de deur open zonder dat iemand het ziet.
      const configError = await tenantConfigError(tenant);
      if (configError) return json(res, 503, { error: configError });

      const wait = await loginLockSeconds(req);
      if (wait > 0) {
        res.setHeader('Retry-After', String(wait));
        return json(res, 429, { error: 'Te veel pogingen', retry_after: wait });
      }
      if (!(await checkPassword(tenant, (req.body || {}).password))) {
        const { wait: lock } = await noteLoginFailure(req);
        return json(res, 401, {
          error: lock > 0 ? `Onjuist wachtwoord — ${Math.ceil(lock / 60)} min geblokkeerd`
            : 'Onjuist wachtwoord',
          retry_after: lock,
        });
      }
      await clearLoginFailures(req);
      res.setHeader('Set-Cookie', await startSession(req, tenant));
      return json(res, 200, { ok: true });
    }
    if (op === 'logout' && req.method === 'POST') {
      await endSession(req);
      res.setHeader('Set-Cookie', clearCookie());
      return json(res, 200, { ok: true });
    }

    const sessionTenant = await requireSession(req, res);
    if (!sessionTenant) return;

    if (op === 'sessions' && req.method === 'GET') {
      return json(res, 200, { sessions: await listSessions(req, sessionTenant) });
    }
    if (op === 'logout-all' && req.method === 'POST') {
      await endAllSessions(sessionTenant);
      res.setHeader('Set-Cookie', clearCookie());
      return json(res, 200, { ok: true });
    }

    if (op === 'items' && req.method === 'GET') return await items(res, sessionTenant);
    if (op === 'briefing' && req.method === 'GET') return await briefing(res, sessionTenant);
    if (op === 'context' && req.method === 'GET') return await context(res, sessionTenant);
    if (op === 'decide' && req.method === 'POST') return await decide(req, res, sessionTenant);
    if (op === 'command' && req.method === 'POST') return await command(req, res, sessionTenant);
    if (op === 'note' && req.method === 'POST') return await note(req, res, sessionTenant);
    if (op === 'note-delete' && req.method === 'POST') return await noteDelete(req, res, sessionTenant);
    if (op === 'notes' && req.method === 'GET') return await notesList(res, sessionTenant);
    if (op === 'outbox' && req.method === 'GET') return await outbox(res, sessionTenant);
    if (op === 'google-status' && req.method === 'GET') return await googleStatus(res, sessionTenant);
    if (op === 'vapid' && req.method === 'GET') {
      return json(res, 200, { key: process.env.VAPID_PUBLIC_KEY || '' });
    }
    if (op === 'push-subscribe' && req.method === 'POST') return await pushSubscribe(req, res, sessionTenant);
    if (op === 'push-unsubscribe' && req.method === 'POST') return await pushUnsubscribe(req, res, sessionTenant);
    if (op === 'whatsapp' && req.method === 'GET') return await whatsappList(res, sessionTenant);
    if (op === 'whatsapp-reply' && req.method === 'POST') return await whatsappReply(req, res, sessionTenant);
    if (op === 'whatsapp-dismiss' && req.method === 'POST') return await whatsappDismiss(req, res, sessionTenant);
    if (op === 'whatsapp-stats' && req.method === 'GET') return await whatsappStats(res, sessionTenant);
    if (op === 'whatsapp-conversations' && req.method === 'GET') return await whatsappConversations(res, sessionTenant);
    if (op === 'whatsapp-thread' && req.method === 'GET') return await whatsappThread(req, res, sessionTenant);
    return json(res, 400, { error: `onbekende op '${op}'` });
  } catch (e) {
    console.error('ui error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}

async function items(res, tenant) {
  // Actieve items, mét het laatste besluit erbij zodat de UI kan tonen
  // "⏳ wacht op AgentOS" / "✓ uitgevoerd" / "⚠ mislukt: ...".
  const rows = await sql`
    SELECT i.*, d.status AS decision_status, d.action AS decision_action,
           d.result AS decision_result
    FROM sync_items i
    LEFT JOIN LATERAL (
      SELECT status, action, result FROM decisions
      WHERE tenant = ${tenant} AND item_key = i.key ORDER BY created_at DESC LIMIT 1
    ) d ON true
    WHERE i.tenant = ${tenant} AND i.status = 'active'
    ORDER BY i.updated_at DESC`;
  const last = await sql`
    SELECT max(updated_at) AS last_push FROM sync_items WHERE tenant = ${tenant}`;
  return json(res, 200, { items: rows, last_push: last[0]?.last_push || null });
}

// ── WhatsApp — klant-escalaties ─────────────────────────────────────────────
// Bewust GEEN sync_items: die worden bij elke bridge_sync-push volledig
// overschreven door wat de lokale machine aanlevert (bridge.js:push — "wat
// verdween wordt gearchiveerd"), en een klant-escalatie ontstaat hier in
// Neon, niet lokaal. Zou hij als sync_item leven, dan verdwijnt hij bij de
// eerstvolgende sync omdat de lokale machine hem nooit heeft aangeleverd.
// Een eigen, kleine tabel + eigen endpoints voorkomt die botsing volledig.

async function whatsappList(res, tenant) {
  const rows = await sql`
    SELECT id, wa_id, project, question, reason, status, reply_text, created_at, answered_at
    FROM whatsapp_escalations WHERE tenant = ${tenant} AND status = 'open'
    ORDER BY created_at ASC LIMIT 50`;
  return json(res, 200, { escalations: rows });
}

async function whatsappReply(req, res, tenant) {
  const { id, text } = req.body || {};
  const body = String(text || '').trim();
  if (!body) return json(res, 400, { error: 'Lege tekst' });
  const row = (await sql`
    SELECT id, wa_id, phone_number_id, status FROM whatsapp_escalations
    WHERE id = ${id} AND tenant = ${tenant}`)[0];
  if (!row) return json(res, 404, { error: 'Niet gevonden' });
  if (row.status !== 'open') return json(res, 400, { error: 'Deze escalatie is al afgehandeld' });
  // Direct via het gedeelde WHATSAPP_TOKEN — geen omweg via decisions/bridge_sync
  // nodig, want versturen vergt geen lokale data of credential.
  const ok = await sendText(row.phone_number_id, row.wa_id, body);
  if (!ok) return json(res, 502, { error: 'Versturen via WhatsApp mislukt — probeer opnieuw' });
  await sql`
    UPDATE whatsapp_escalations SET status = 'answered', reply_text = ${body}, answered_at = now()
    WHERE id = ${id} AND tenant = ${tenant}`;
  // Thread-continuïteit: Vincents antwoord terugschrijven naar de klant-draad
  // zodat klant-Iris het bij de volgende klantvraag in context heeft. Zonder
  // dit wist Iris bij een vervolgvraag die naar Vincents antwoord verwijst
  // (bijv. "en wat kost dat dan?") de context en herhaalt of escaleert ze
  // nutteloos. We plakken het als assistent-bericht (Vincent = de menselijke
  // "assistent" in dit gesprek) en houden de draad binnen MAX_CUSTOMER_TURNS.
  try {
    const rows = await sql`
      SELECT messages FROM whatsapp_threads WHERE tenant = ${tenant} AND wa_id = ${row.wa_id}`;
    const messages = rows[0]?.messages || [];
    messages.push({ role: 'assistant', content: body });
    const trimmed = messages.slice(-10);
    await sql`
      INSERT INTO whatsapp_threads (tenant, wa_id, messages, updated_at)
      VALUES (${tenant}, ${row.wa_id}, ${JSON.stringify(trimmed)}::jsonb, now())
      ON CONFLICT (tenant, wa_id) DO UPDATE SET
        messages = EXCLUDED.messages, updated_at = now()`;
  } catch (e) {
    console.error('whatsappReply: thread-update mislukt (niet fataal)', e);
  }
  return json(res, 200, { ok: true });
}

async function whatsappDismiss(req, res, tenant) {
  const { id } = req.body || {};
  await sql`
    UPDATE whatsapp_escalations SET status = 'dismissed', answered_at = now()
    WHERE id = ${id} AND tenant = ${tenant} AND status = 'open'`;
  return json(res, 200, { ok: true });
}

// ── WhatsApp — managementoverzicht ──────────────────────────────────────────
// Wat er tot 19 aug 2026 ontbrak: er bestond geen enkele plek die volume,
// escalatiegraad of rate-limit-druk toonde — alleen de kale escalatielijst
// hierboven. Deze functie voegt niets nieuws toe aan wat al gemeten wordt
// (`whatsapp_rate_limit` telt al per (tenant, wa_id, dag), `whatsapp_escalations`
// draagt al `created_at`/`answered_at`); hij leest alleen wat er al ligt.
//
// `near_limit` gebruikt 75% van `WHATSAPP_CUSTOMER_DAILY_LIMIT` (default 40,
// zelfde env-var als `_customer_core.js`) als signaal — de klant die er tegen
// aanloopt is interessanter dan de klant die hem al raakte, want de eerste
// kun je nog vóór zijn.
//
// `escalation_rate_7d` deelt door actieve gesprékken, niet door berichten:
// "hoe vaak moest Iris een mens erbij halen" is een uitspraak per gesprek,
// niet per bericht — een lang gesprek met 20 berichten en 1 escalatie is een
// ander signaal dan 20 gesprekken met 1 escalatie elk.
async function whatsappStats(res, tenant) {
  const limit = Number(process.env.WHATSAPP_CUSTOMER_DAILY_LIMIT || 40);
  const nearLimitThreshold = Math.round(limit * 0.75);

  const [volume] = await sql`
    SELECT
      COALESCE(SUM(count) FILTER (WHERE day = CURRENT_DATE), 0)::int AS messages_today,
      COALESCE(SUM(count) FILTER (WHERE day >= CURRENT_DATE - 6), 0)::int AS messages_7d,
      COUNT(DISTINCT wa_id) FILTER (WHERE day >= CURRENT_DATE - 6)::int AS active_conversations_7d
    FROM whatsapp_rate_limit WHERE tenant = ${tenant}`;

  const nearLimit = await sql`
    SELECT wa_id, count FROM whatsapp_rate_limit
    WHERE tenant = ${tenant} AND day = CURRENT_DATE AND count >= ${nearLimitThreshold}
    ORDER BY count DESC LIMIT 10`;

  const [esc] = await sql`
    SELECT
      COUNT(*) FILTER (WHERE status = 'open')::int AS open,
      COUNT(*) FILTER (WHERE created_at >= now() - interval '7 days')::int AS created_7d,
      COUNT(*) FILTER (WHERE status = 'answered' AND answered_at >= now() - interval '7 days')::int AS answered_7d,
      COUNT(*) FILTER (WHERE status = 'dismissed' AND answered_at >= now() - interval '7 days')::int AS dismissed_7d,
      AVG(EXTRACT(EPOCH FROM (answered_at - created_at)))
        FILTER (WHERE status = 'answered' AND answered_at >= now() - interval '7 days')::int AS avg_response_seconds
    FROM whatsapp_escalations WHERE tenant = ${tenant}`;

  const openByProject = await sql`
    SELECT COALESCE(project, 'onbekend') AS project, COUNT(*)::int AS open
    FROM whatsapp_escalations WHERE tenant = ${tenant} AND status = 'open'
    GROUP BY project ORDER BY open DESC`;

  // Geen nieuwe meting — `created_at` op whatsapp_threads staat al vast op het
  // allereerste bericht van dat nummer (zie schema.sql), dit telt 'm alleen.
  const [contacts] = await sql`
    SELECT COUNT(*) FILTER (WHERE created_at >= now() - interval '7 days')::int AS new_contacts_7d
    FROM whatsapp_threads WHERE tenant = ${tenant} AND project IS NOT NULL`;

  return json(res, 200, {
    daily_limit: limit,
    messages_today: volume.messages_today,
    messages_7d: volume.messages_7d,
    active_conversations_7d: volume.active_conversations_7d,
    new_contacts_7d: contacts.new_contacts_7d,
    near_limit: nearLimit,
    escalations: {
      open: esc.open,
      created_7d: esc.created_7d,
      answered_7d: esc.answered_7d,
      dismissed_7d: esc.dismissed_7d,
      avg_response_seconds: esc.avg_response_seconds,
      escalation_rate_7d: volume.active_conversations_7d
        ? Math.round(100 * esc.created_7d / volume.active_conversations_7d)
        : null,
    },
    open_by_project: openByProject,
  });
}

// ── Communicatie — volledig gespreksoverzicht (22 aug 2026) ────────────────
// Tot dusver toonde de app alleen escalaties (waar klant-Iris vastliep). Een
// klant die gewoon een goed antwoord kreeg was nergens te zien. Dit leest
// dezelfde `whatsapp_threads`-tabel als de rest van dit bestand, alleen niet
// gefilterd op "vastgelopen" — `project IS NOT NULL` sluit het manager-
// gesprek (Vincent zelf, project blijft altijd NULL) uit een klantoverzicht.
async function whatsappConversations(res, tenant) {
  const rows = await sql`
    SELECT t.wa_id, t.project, t.contact_name, t.created_at, t.updated_at,
      jsonb_array_length(t.messages) AS message_count,
      (t.created_at >= now() - interval '48 hours') AS is_new,
      COALESCE(e.open, 0)::int AS open_escalations
    FROM whatsapp_threads t
    LEFT JOIN (
      SELECT wa_id, COUNT(*) AS open FROM whatsapp_escalations
      WHERE tenant = ${tenant} AND status = 'open' GROUP BY wa_id
    ) e ON e.wa_id = t.wa_id
    WHERE t.tenant = ${tenant} AND t.project IS NOT NULL
    ORDER BY t.updated_at DESC LIMIT 100`;
  return json(res, 200, { conversations: rows });
}

// Eén klantgesprek in detail — voor de "bekijk transcript"-klik in het
// Communicatie-scherm. Escalaties erbij (niet alleen open) zodat je ook ziet
// wat er al opgelost is, niet alleen wat nu nog wacht.
async function whatsappThread(req, res, tenant) {
  const waId = String((req.query && req.query.wa_id) || '');
  if (!waId) return json(res, 400, { error: 'wa_id ontbreekt' });
  const rows = await sql`
    SELECT wa_id, project, contact_name, messages, created_at, updated_at
    FROM whatsapp_threads WHERE tenant = ${tenant} AND wa_id = ${waId}`;
  if (!rows.length) return json(res, 404, { error: 'Gesprek niet gevonden' });
  const escalations = await sql`
    SELECT id, question, reason, status, reply_text, created_at, answered_at
    FROM whatsapp_escalations WHERE tenant = ${tenant} AND wa_id = ${waId}
    ORDER BY created_at DESC LIMIT 20`;
  return json(res, 200, { thread: rows[0], escalations });
}

async function briefing(res, tenant) {
  const rows = await sql`
    SELECT payload, generated_at FROM briefings
    WHERE tenant = ${tenant} ORDER BY generated_at DESC LIMIT 1`;
  return json(res, 200, rows[0] || { payload: null, generated_at: null });
}

async function decide(req, res, tenant) {
  const { item_key, action, payload } = req.body || {};
  const item = (await sql`
    SELECT key, dismiss_kind, item_id FROM sync_items WHERE tenant = ${tenant} AND key = ${item_key}`)[0];
  if (!item) return json(res, 404, { error: 'Item niet (meer) bekend' });
  if (!(ALLOWED[item.dismiss_kind] || []).includes(action)) {
    return json(res, 400, { error: `Actie '${action}' niet toegestaan op '${item.dismiss_kind}'` });
  }
  // Dubbel tikken: de partial-unique-index vangt de race; conflict = negeren.
  const rows = await sql`
    INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
    VALUES (${tenant}, ${item.key}, ${item.dismiss_kind}, ${item.item_id}, ${action},
            ${JSON.stringify(payload || {})}::jsonb)
    ON CONFLICT (tenant, item_key) WHERE status = 'pending' DO NOTHING
    RETURNING id`;
  return json(res, 200, { ok: true, queued: rows.length > 0 });
}

async function context(res, tenant) {
  const rows = await sql`
    SELECT payload, generated_at FROM context_snapshot WHERE tenant = ${tenant}`;
  const snap = rows[0] || { payload: null, generated_at: null };
  // Per-tenant identiteit (naam + Google-status) uit de laatste push — de
  // enige correcte bron voor de UI-naam (nooit hard-coded "Vincent").
  let tenantInfo = null;
  try {
    const trows = await sql`SELECT name FROM tenants WHERE slug = ${tenant}`;
    if (trows[0]) {
      const g = await googleStatusRow(tenant);
      tenantInfo = {
        slug: tenant,
        name: trows[0].name,
        google_configured: !!(g && g.configured),
        calendar_configured: !!(g && g.configured),
        google_account: g ? g.account_email : null,
      };
    }
  } catch (e) {
    console.error('tenant-info ophalen mislukt', tenant, e);
  }
  // Agenda en GSC-trend proberen we altijd live te verversen (Google-
  // service-account, geen AgentOS nodig) — de rest van de snapshot blijft
  // uit de cache. Nooit laten falen op de rest van het antwoord: één tenant
  // zonder Google-koppeling krijgt gewoon zijn snapshot terug, ongewijzigd.
  if (snap.payload) {
    try {
      snap.live = await attachLive(tenant, snap.payload);
    } catch (e) {
      console.error('attachLive mislukt', tenant, e);
      snap.live = { agenda: false, seo: false };
    }
  }
  const out = { payload: snap.payload, generated_at: snap.generated_at, live: snap.live };
  if (tenantInfo) out.tenant = tenantInfo;
  return json(res, 200, out);
}

// Interne helper: leest de Google-configuratiestatus voor een tenant (zowel
// de service-account-kolommen als de per-tenant OAuth-koppeling).
async function googleStatusRow(tenant) {
  const rows = await sql`
    SELECT (calendar_client_email IS NOT NULL AND calendar_private_key_enc IS NOT NULL) AS configured,
           calendar_calendar_id,
           (SELECT account_email FROM oauth_accounts WHERE site_id = ${tenant} AND provider = 'google' ORDER BY updated_at DESC LIMIT 1) AS account_email
    FROM tenants WHERE slug = ${tenant}`;
  return rows[0] || null;
}

async function command(req, res, tenant) {
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
  // tenant+item_key): twee keer tikken op "Schrijf artikelen" moet één run geven.
  // Commando's mét `keyField` richten zich op iets concreets (déze afzender,
  // déze mail) — die krijgen hun doelwit in de sleutel, anders is de tweede
  // blokkade een stille no-op.
  const doelwit = spec.keyField ? String(clean[spec.keyField] || '').slice(0, 120) : '';
  const key = doelwit ? `cmd:${action}:${doelwit}` : `cmd:${action}`;
  const rows = await sql`
    INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
    VALUES (${tenant}, ${key}, 'command', ${action}, ${action}, ${JSON.stringify(clean)}::jsonb)
    ON CONFLICT (tenant, item_key) WHERE status = 'pending' DO NOTHING
    RETURNING id`;
  return json(res, 200, { ok: true, queued: rows.length > 0, label: spec.label });
}

async function notesList(res, tenant) {
  const rows = await sql`
    SELECT id, text, status, created_at FROM notes
    WHERE tenant = ${tenant} ORDER BY created_at DESC LIMIT 20`;
  return json(res, 200, { notes: rows });
}

async function outbox(res, tenant) {
  const rows = await sql`
    SELECT id, item_key, item_kind, action, status, result, created_at, decided_at
    FROM decisions WHERE tenant = ${tenant} ORDER BY created_at DESC LIMIT 20`;
  return json(res, 200, { decisions: rows });
}

// Alleen statusvelden — nooit de credentials zelf naar de browser. Voedt het
// diagnoseblok in de Systeem-tab: "live" is stil zolang alles werkt, maar een
// verlopen/ingetrokken service-account moet net zo zichtbaar worden als de
// ingetrokken Outlook-sessie dat eerder al moest worden (zie CLAUDE.md 14d).
async function googleStatus(res, tenant) {
  const rows = await sql`
    SELECT (calendar_client_email IS NOT NULL AND calendar_private_key_enc IS NOT NULL) AS configured,
           calendar_calendar_id,
           jsonb_array_length(COALESCE(gsc_sites, '[]'::jsonb)) AS gsc_site_count,
           google_synced_at, google_last_error, google_last_error_at
    FROM tenants WHERE slug = ${tenant}`;
  return json(res, 200, rows[0] || { configured: false });
}

async function pushSubscribe(req, res, tenant) {
  const sub = req.body || {};
  const keys = sub.keys || {};
  if (!sub.endpoint || !keys.p256dh || !keys.auth) {
    return json(res, 400, { error: 'Ongeldig push-abonnement' });
  }
  await sql`
    INSERT INTO push_subscriptions (tenant, endpoint, p256dh, auth)
    VALUES (${tenant}, ${sub.endpoint}, ${keys.p256dh}, ${keys.auth})
    ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                                         tenant = EXCLUDED.tenant`;
  return json(res, 200, { ok: true });
}

async function pushUnsubscribe(req, res, tenant) {
  const endpoint = (req.body || {}).endpoint || '';
  await sql`DELETE FROM push_subscriptions WHERE tenant = ${tenant} AND endpoint = ${endpoint}`;
  return json(res, 200, { ok: true });
}

async function note(req, res, tenant) {
  const text = String((req.body || {}).text || '').trim();
  if (!text) return json(res, 400, { error: 'Lege notitie' });
  await sql`INSERT INTO notes (tenant, text) VALUES (${tenant}, ${text})`;
  return json(res, 200, { ok: true });
}

// Alleen 'pending' is te verwijderen: eenmaal 'synced' staat de tekst al als
// markdown in de vault, en de rij hier is dan het enige bewijs dát dat
// gebeurde — die laten we met rust, ook al is hij verder nutteloos.
async function noteDelete(req, res, tenant) {
  const id = Number((req.body || {}).id);
  if (!id) return json(res, 400, { error: 'Ontbrekend notitie-id' });
  const rows = await sql`
    DELETE FROM notes WHERE tenant = ${tenant} AND id = ${id} AND status = 'pending'
    RETURNING id`;
  return json(res, 200, { ok: true, deleted: rows.length > 0 });
}
