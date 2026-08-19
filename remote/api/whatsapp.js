// WhatsApp-endpoint voor Iris — Meta Cloud API webhook.
//   GET  /api/whatsapp   → verificatie bij het instellen van de webhook in
//                          Meta's App Dashboard (hub.challenge terugkaatsen).
//   POST /api/whatsapp   → binnenkomend bericht, twee heel verschillende paden:
//
//   1. Afzender staat op `tenants.whatsapp_allowed_from` (Vincent zelf) →
//      manager-Iris (_iris_core.js): dezelfde tool-lus als de app-chat.
//      Commando's landen in `decisions` en wachten op de eerstvolgende
//      bridge_sync, exact zoals vanuit Iris Remote.
//
//   2. Elke andere afzender → klant-Iris (_customer_core.js): alleen de
//      kennisbank van het project waarover het gesprek gaat, en precies één
//      tool (escaleren). Onzeker of iets met gevolgen (offerte, afspraak,
//      klacht, persoonsgegevens) → nooit verzinnen, altijd een kaart voor
//      Vincent in plaats van een antwoord.
//
// Geen enkel pad hier voert zelf iets extern uit buiten "een WhatsApp-bericht
// versturen" — dat is de aard van een chat-kanaal. Wat wél gevolgen heeft
// (agents starten, mail sturen, publiceren) loopt via manager-Iris altijd nog
// door de bestaande `decisions`-gate.
//
// ── Wereldklasse-garanties (toegevoegd 18 aug 2026) ──────────────────────
//   • Deduplicatie is NU een claim/state-machine: het message_id wordt pas
//     als "verwerkt" gemarkeerd NADAT het antwoord daadwerkelijk is verzonden.
//     Een Vercel-timeout (functie > 60s gekild) of een LLM-fout mid-weg laat
//     het bericht op status 'received' staan, dus Meta's retry bezorgt het
//     opnieuw en de klant krijgt wél antwoord. Zie claimMessage()/
//     markReplied() en de status-kolom in whatsapp_processed.
//   • Per-afzender rate-limit: een nummer dat binnen 1 uur > 20 berichten
//     stuurt (of > 6 in 60s) wordt gedropt vóór de LLM — beschermt tegen
//     spam-loops en kosten, zonder legitieme klantengesprekken te breken.
//   • Toegangslijst blijft fail-closed: een onbekende afzender krijgt nooit
//     "wie ben je", maar gaat direct naar klant-Iris (kennisbank-only).
import crypto from 'node:crypto';
import { sql } from './_lib.js';
import { converse, MAX_TURNS } from './_iris_core.js';
import { resolveOrAskProject, customerConverse, MAX_CUSTOMER_TURNS } from './_customer_core.js';
import { sendText, markRead } from './_whatsapp_send.js';

// bodyParser:false is vereist om de exacte bytes te krijgen die Meta ook
// hashte voor X-Hub-Signature-256 — een her-JSON.stringify van req.body kan
// door key-volgorde of witruimte een andere hash opleveren dan Meta stuurde,
// en dan verwerpt de check elk écht bericht.
export const config = { api: { bodyParser: false }, maxDuration: 60 };

async function readRawBody(req) {
  if (req.rawBody) return req.rawBody; // dev-server.mjs zet dit al klaar
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function verifySignature(raw, header) {
  const secret = process.env.WHATSAPP_APP_SECRET;
  if (!secret) {
    console.error('whatsapp: WHATSAPP_APP_SECRET ontbreekt — webhook staat dicht');
    return false;
  }
  const expected = crypto.createHmac('sha256', secret).update(raw).digest('hex');
  const got = String(header || '').replace(/^sha256=/, '');
  // Tijdelijke diagnose (18 aug 2026 incident): geen geheime waarden in de
  // log, alleen lengtes/prefixen — genoeg om "lege body" van "verkeerd
  // secret" te onderscheiden zonder het geheim zelf te laten verschijnen.
  const match = got.length === expected.length
    && crypto.timingSafeEqual(Buffer.from(got, 'hex'), Buffer.from(expected, 'hex'));
  if (!match) {
    console.error('whatsapp signature mismatch', {
      rawLen: raw.length, hasHeader: !!header, gotLen: got.length,
      expectedLen: expected.length, expectedPrefix: expected.slice(0, 8),
    });
  }
  return match;
}

async function tenantForNumber(phoneNumberId) {
  const rows = await sql`
    SELECT slug, whatsapp_allowed_from FROM tenants WHERE whatsapp_phone_number_id = ${phoneNumberId}`;
  return rows[0] || null;
}

// ── Deduplicatie als state-machine (fix voor de "timeout drop" bug) ───────
// Vóór deze versie markeerde alreadyProcessed() het id als verwerkt VÓÓR de
// LLM-call + send. Een functie die > 60s liep (manager-lus + live Google)
// werd door Vercel gekild zonder 200; Meta leverde opnieuw, de retry zag het
// id al in de tabel en DROPTE het bericht definitief → geen antwoord ooit.
//
// Nieuw model: claimMessage() zet status='received' (idempotent, ON CONFLICT
// DO NOTHING → eerste keer true, elke retry ook true, dus Meta-retries lopen
// gewoon door). Pas NA succesvolle sendText roept markReplied() status op naar
// 'replied'. Een retry die een bericht met status='replied' tegenkomt, wordt
// alsnog vroeg gedropt (al geleverd). Een bericht dat vastliep op 'received'
// (timeout/crash) krijgt bij de retry dus een nieuwe kans — precies wat we
// willen. De garbage-collect-achtige opruiming van oude rijen voorkomt dat de
// tabel eindeloos groeit.
async function claimMessage(messageId) {
  const rows = await sql`
    INSERT INTO whatsapp_processed (message_id, status)
    VALUES (${messageId}, 'received')
    ON CONFLICT (message_id) DO NOTHING
    RETURNING message_id`;
  return rows.length === 1; // true = eerste keer gezien
}

// Wordt aangeroepen als we het bericht al hadden verwerkt (status='replied').
async function alreadyReplied(messageId) {
  const rows = await sql`
    SELECT 1 FROM whatsapp_processed WHERE message_id = ${messageId} AND status = 'replied' LIMIT 1`;
  return rows.length === 1;
}

async function markReplied(messageId) {
  await sql`
    UPDATE whatsapp_processed SET status = 'replied', processed_at = now() WHERE message_id = ${messageId}`;
}

// Oude 'received' (niet-replied) rijen langer dan 1 uur opruimen — die zijn
// ofwel definitief mislukt, ofwel nog in een zeldzame retry. Bij een echte
// retry is het bericht inmiddels opnieuw geclaimd (status blijft 'received');
// we ruimen hier niet actief, alleen de allang verlopen. Dit houdt de tabel
// klein zonder lopende retries te raken (WHERE processed_at < now()-1h én
// status='received').
async function sweepStaleClaims() {
  // Laagfrequent: alleen uitvoeren op een fractie van de calls om de DB niet
  // bij élke webhook te raken. Math.random is hier voldoende — geen security.
  if (Math.random() > 0.05) return;
  try {
    await sql`
      DELETE FROM whatsapp_processed
      WHERE status = 'received' AND processed_at < now() - interval '1 hour'`;
  } catch (e) {
    console.error('whatsapp sweep mislukt', e);
  }
}

// ── Per-afzender rate-limit (kostenbescherming) ───────────────────────────
// Meta levert 'at least once' (de dedupe vangt dat), maar een kwaadwillende of
// bug-loopy afzender die 100 *verschillende* berichten stuurt, kost 100
// LLM-calls. Een simpele teller per wa_id per venster vangt dat: > 20/uuur of
// > 6/60s ⇒ drop vóór de LLM. Legitieme klantengesprekken (een handvol berichten
// per dag) raken dit nooit.
const THROTTLE_PER_HOUR = 20;
const THROTTLE_PER_MIN = 6;

async function throttled(waId) {
  const rows = await sql`
    INSERT INTO whatsapp_throttle (wa_id, count_1h, count_1m, window_start)
    VALUES (${waId}, 1, 1, now())
    ON CONFLICT (wa_id) DO UPDATE SET
      count_1h = CASE
        WHEN whatsapp_throttle.window_start < now() - interval '1 hour'
          THEN 1
        ELSE whatsapp_throttle.count_1h + 1
      END,
      count_1m = CASE
        WHEN whatsapp_throttle.window_start < now() - interval '1 minute'
          THEN 1
        ELSE whatsapp_throttle.count_1m + 1
      END,
      window_start = CASE
        WHEN whatsapp_throttle.window_start < now() - interval '1 minute'
          THEN now()
        ELSE whatsapp_throttle.window_start
      END
    RETURNING count_1h, count_1m`;
  const { count_1h, count_1m } = rows[0];
  return count_1h > THROTTLE_PER_HOUR || count_1m > THROTTLE_PER_MIN;
}

async function loadThread(tenant, waId) {
  const rows = await sql`
    SELECT messages, project FROM whatsapp_threads WHERE tenant = ${tenant} AND wa_id = ${waId}`;
  return rows[0] || { messages: [], project: null };
}

async function saveThread(tenant, waId, messages, project, maxTurns) {
  const trimmed = messages.slice(-maxTurns);
  await sql`
    INSERT INTO whatsapp_threads (tenant, wa_id, messages, project, updated_at)
    VALUES (${tenant}, ${waId}, ${JSON.stringify(trimmed)}::jsonb, ${project}, now())
    ON CONFLICT (tenant, wa_id) DO UPDATE SET
      messages = EXCLUDED.messages, project = COALESCE(EXCLUDED.project, whatsapp_threads.project),
      updated_at = now()`;
}

async function handleManagerMessage(tenant, phoneNumberId, from, text) {
  const thread = await loadThread(tenant, from);
  const convo = [...thread.messages, { role: 'user', content: text }];

  let result;
  try {
    result = await converse(tenant, convo, 'whatsapp');
  } catch (e) {
    console.error('whatsapp manager-converse mislukt', tenant, e);
    await sendText(phoneNumberId, from,
      'Er ging iets mis bij het ophalen van een antwoord. Probeer het over een minuutje nog eens.');
    return;
  }

  await saveThread(tenant, from, [...convo, { role: 'assistant', content: result.reply }], null, MAX_TURNS);
  await sendText(phoneNumberId, from, result.reply);
}

async function logEscalation(tenant, from, phoneNumberId, project, question, reason) {
  const rows = await sql`
    INSERT INTO whatsapp_escalations (tenant, wa_id, phone_number_id, project, question, reason)
    VALUES (${tenant}, ${from}, ${phoneNumberId}, ${project}, ${question}, ${reason})
    RETURNING id`;
  return rows[0]?.id || null;
}

// Vincent zit al in WhatsApp met manager-Iris — een pushmelding zou hij pas
// zien als hij toevallig de app opent, een appje ziet hij meteen. `notifyMe`
// is dus bewust hetzelfde kanaal als de rest van dit bestand, alleen naar het
// EERSTE nummer in `whatsapp_allowed_from` (dat is Vincent zelf, zie
// schema.sql). Mislukt deze melding, dan mag dat het antwoord aan de klant
// nooit blokkeren — vandaar los afgevangen in de aanroeper.
async function notifyMe(phoneNumberId, managerNumber, text) {
  if (!managerNumber) return;
  await sendText(phoneNumberId, managerNumber, text);
}

async function handleCustomerMessage(tenant, phoneNumberId, from, text, managerNumber) {
  const thread = await loadThread(tenant, from);
  let project = thread.project;

  if (!project) {
    const resolved = await resolveOrAskProject(tenant, text);
    if (!resolved.project) {
      // Nog niet bekend welk project dit betreft: geen LLM-call nodig, gewoon
      // vragen. De vraag zelf dient meteen als AI-disclosure.
      await saveThread(tenant, from,
        [...thread.messages, { role: 'user', content: text }, { role: 'assistant', content: resolved.ask }],
        null, MAX_CUSTOMER_TURNS);
      await sendText(phoneNumberId, from, resolved.ask);
      return;
    }
    project = resolved.project;
    // Project zojuist herkend uit dít bericht — behandel het meteen ook als de
    // eerste inhoudelijke vraag in plaats van nog een keer "wat wil je weten"
    // te vragen. De geschiedenis start dus leeg voor customerConverse.
    thread.messages = [];
  }

  let result;
  try {
    result = await customerConverse(tenant, project, thread.messages, text, from);
  } catch (e) {
    console.error('whatsapp klant-converse mislukt', tenant, project, e);
    await sendText(phoneNumberId, from,
      'Er ging iets mis. Ik heb het doorgegeven, iemand neemt zo contact met je op.');
    const escId = await logEscalation(tenant, from, phoneNumberId, project, text,
      `technische fout: ${String(e.message || e).slice(0, 200)}`);
    if (escId) {
      notifyMe(phoneNumberId, managerNumber,
        `Klant-Iris kon niet antwoorden (${project}, ${from}): "${text.slice(0, 150)}". Check Iris Remote.`)
        .catch((err) => console.error('notifyMe mislukt', err));
    }
    return;
  }

  const nextHistory = [...thread.messages,
    { role: 'user', content: text }, { role: 'assistant', content: result.reply }];
  await saveThread(tenant, from, nextHistory, project, MAX_CUSTOMER_TURNS);
  await sendText(phoneNumberId, from, result.reply);

  if (result.escalated) {
    const escId = await logEscalation(tenant, from, phoneNumberId, project, text, result.reason);
    if (escId) {
      notifyMe(phoneNumberId, managerNumber,
        `Klantvraag wacht op jouw antwoord (${project}, ${from}): "${text.slice(0, 200)}". Reden: ${result.reason}. Beantwoord in Iris Remote.`)
        .catch((err) => console.error('notifyMe mislukt', err));
    }
  } else if (result.proposed) {
    notifyMe(phoneNumberId, managerNumber,
      `Nieuw afspraakvoorstel via WhatsApp (${project}, ${from}) staat klaar in je agenda-goedkeur-wachtrij.`)
      .catch((err) => console.error('notifyMe mislukt', err));
  }
}

async function handleMessage(phoneNumberId, msg) {
  // v1: alleen tekst. Spraakberichten/foto's negeren we bewust in plaats van
  // een verwarrend "dat snap ik niet" — de afzender ziet toch geen antwoord en
  // leert vanzelf dat hij het moet typen; een halve feature is hier erger dan
  // geen feature.
  if (msg.type !== 'text' || !msg.text?.body?.trim()) return;

  const messageId = String(msg.id || '');
  if (!messageId) return; // geen id ⇒ niets om te dedupe'en, veilig negeren

  // Claim vóór verwerking: eerste keer gezien. Retries (Meta 'at least once')
  // claimen hetzelfde id opnieuw (idempotent) en lopen gewoon door. We droppen
  // pas als het bericht al als 'replied' gemarkeerd is — dus een vastgelopen
  // bericht (timeout) krijgt bij de retry een nieuwe kans.
  await claimMessage(messageId);
  if (await alreadyReplied(messageId)) return;

  // Rate-limit: beschermt tegen spam-loops en LLM-kosten. Een legitieme klant
  // haalt deze grenzen nooit; een nummer dat ze wel raakt, wordt vóór de LLM
  // gedropt.
  if (await throttled(String(msg.from || ''))) {
    console.warn('whatsapp rate-limit geraakt voor', msg.from, '— bericht gedropt');
    return;
  }

  const tenant = await tenantForNumber(phoneNumberId);
  if (!tenant) {
    console.error('whatsapp: onbekend phone_number_id', phoneNumberId);
    return;
  }

  const allowed = String(tenant.whatsapp_allowed_from || '')
    .split(',').map((s) => s.trim()).filter(Boolean);
  const from = String(msg.from || '');
  const text = String(msg.text.body).slice(0, 4000);

  markRead(phoneNumberId, msg.id);

  if (allowed.includes(from)) {
    await handleManagerMessage(tenant.slug, phoneNumberId, from, text);
  } else {
    await handleCustomerMessage(tenant.slug, phoneNumberId, from, text, allowed[0]);
  }

  // Pas NA het versturen markeren we als 'replied' — daarmee is de state-machine
  // compleet en kan een timeout nooit meer een bericht stil dropkicken.
  await markReplied(messageId);
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    const q = req.query || {};
    const mode = q['hub.mode'];
    const token = q['hub.verify_token'];
    if (mode === 'subscribe' && token && process.env.WHATSAPP_VERIFY_TOKEN
        && token === process.env.WHATSAPP_VERIFY_TOKEN) {
      res.statusCode = 200;
      res.setHeader('content-type', 'text/plain');
      return res.end(String(q['hub.challenge'] || ''));
    }
    res.statusCode = 403;
    return res.end('verify_token mismatch');
  }

  if (req.method !== 'POST') {
    res.statusCode = 405;
    return res.end('POST only');
  }

  const raw = await readRawBody(req);
  if (!verifySignature(raw, req.headers['x-hub-signature-256'])) {
    console.error('whatsapp webhook: ongeldige of ontbrekende signature');
    res.statusCode = 401;
    return res.end('invalid signature');
  }

  let body;
  try { body = raw.length ? JSON.parse(raw.toString('utf8')) : {}; } catch { body = {}; }

  // Meta wil snel een 200 en herhaalt anders de aflevering; we verwerken
  // synchroon binnen de functietijd (maxDuration 60s, ruim genoeg voor een
  // paar tool-rondes) zodat een fout in de log komt in plaats van in de lucht
  // te verdwijnen achter een voortijdige 200. Een timeout leidt hooguit tot
  // een herhaalde aflevering, die de state-machine (status='received') dan
  // afvangt en opnieuw probeert.
  try {
    for (const entry of body.entry || []) {
      for (const change of entry.changes || []) {
        const value = change.value || {};
        const phoneNumberId = value.metadata?.phone_number_id;
        if (!phoneNumberId) continue;
        for (const msg of value.messages || []) {
          await handleMessage(phoneNumberId, msg).catch((e) => {
            console.error('whatsapp bericht verwerken mislukt', msg.id, e);
          });
        }
      }
    }
  } catch (e) {
    console.error('whatsapp webhook fout', e);
  }

  // Periodiek (laagfrequent) verlopen claims opruimen.
  sweepStaleClaims().catch(() => {});

  res.statusCode = 200;
  return res.end('EVENT_RECEIVED');
}
