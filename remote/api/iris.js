// Cloud-Iris — je assistent onderweg, ook als de pc uitstaat.
//
// Verschil met de eerste versie: Iris kreeg toen één platgeslagen samenvatting
// mee en kon verder niets. Nu draait ze een echte tool-lus over de laatste
// snapshot in Neon (agenda, mail, analytics, SEO, open besluiten) en mag ze
// wérk aanzwengelen.
//
// De grens ligt scherp en bewust:
//   • Iris MAG commando's in de rij zetten. Alles wat daaruit komt landt in een
//     review-gate (Wachtrij, outreach_review, voorstel) — nooit extern.
//   • Iris MAG GEEN gate passeren. Goedkeuren, versturen en boeken blijven een
//     menselijke tik. Ze kan zo'n besluit alleen VOORSTELLEN; de app rendert
//     dat als een knop. Zou ze het zelf mogen queuen, dan was het model de
//     goedkeurder geworden en had de hele review-gate geen betekenis meer.
import { sql, json, requireSession } from './_lib.js';

export const config = { maxDuration: 60 };

const MAX_TURNS = 12;
const MAX_TOOL_ROUNDS = 4;

// Spiegel van COMMANDS in ui.js — hier alleen de namen, om te valideren.
const COMMANDS = {
  content_run: 'Artikelen schrijven → Wachtrij',
  seo_refresh: 'Wegzakkende pagina’s verrijken → Wachtrij',
  outreach_run: 'Outreach-concepten klaarzetten',
  lead_search: 'Nieuwe leads zoeken',
  linkbuilding_run: 'Linkbuilding-concepten klaarzetten',
  mail_sync: 'Mail ophalen en triëren',
  helpdesk_run: 'Helpdesk-concepten schrijven',
  iris_briefing: 'Iris opnieuw laten analyseren',
  context_refresh: 'Cijfers verversen',
  digest: 'Ochtendrapport draaien',
};

const TOOLS = [
  {
    name: 'lees_context',
    description:
      'Haal een sectie van de laatste snapshot op. "agenda" = vandaag + komende dagen, ' +
      'vrije blokken en agendaproblemen. "mail" = achterstand, urgente berichten, ' +
      'afzenderpatronen. "analytics" = GA4 laatste 7 dagen vs. de 7 daarvoor, bronnen, ' +
      'top-pagina\'s. "seo" = GSC-trend, stijgers en dalers per site. "pulse" = de ' +
      'deterministische lijst wat goed en slecht gaat. "rituals" = zijn ochtend/avond-' +
      'ritueel van vandaag, streaks, energie, waar hij dankbaar voor is, weekintentie en ' +
      'persoonlijke doelen met voortgang. Gebruik dit vóór je iets beweert over cijfers ' +
      'of over hoe zijn dag/week gaat — gok nooit.',
    input_schema: {
      type: 'object',
      properties: {
        sectie: { type: 'string', enum: ['agenda', 'mail', 'analytics', 'seo', 'pulse', 'rituals'] },
      },
      required: ['sectie'],
    },
  },
  {
    name: 'lees_besluiten',
    description:
      'De items die op Vincent wachten, met previews. Filter optioneel op soort ' +
      '(content, mail, outreach, calendar, error, ...).',
    input_schema: {
      type: 'object',
      properties: {
        soort: { type: 'string' },
        detail: { type: 'boolean', description: 'Ook de volledige preview meesturen (lang).' },
      },
      required: [],
    },
  },
  {
    name: 'lees_briefing',
    description: 'De volledige laatste Iris-briefing: markdown, cijfers per project, knelpunten.',
    input_schema: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'start_werk',
    description:
      'Zet een agent aan het werk. Het resultaat landt ALTIJD in een review-gate — ' +
      'er gaat niets live en er wordt niets verstuurd. Gebruik dit als Vincent iets ' +
      'gedaan wil hebben, niet alleen wil weten. Beschikbaar: ' +
      Object.entries(COMMANDS).map(([k, v]) => `${k} (${v})`).join(', ') + '.',
    input_schema: {
      type: 'object',
      properties: {
        commando: { type: 'string', enum: Object.keys(COMMANDS) },
        site: { type: 'string', description: 'Voor content_run/seo_refresh: sitenaam of -id.' },
        count: { type: 'integer', description: 'Aantal (wordt lokaal geklemd).' },
      },
      required: ['commando'],
    },
  },
  {
    name: 'plan_agenda',
    description:
      'Maak een agenda-afspraak of terugkerend blok aan uit een vrije Nederlandse zin. ' +
      'Gebruik dit zodra Vincent iets in zijn agenda wil zetten, blokken, reserveren of ' +
      'plannen (bv. "blok de komende 6 weken op maandag van 08.30 tot 10.00 voor Focustijd", ' +
      '"dinsdag 18 augustus 12.15 tandarts", "online meeting met Thijs op 19 augustus 10.00"). ' +
      'AgentOS parseert de zin, checkt reistijd en conflicten, en zet het als voorstel in het ' +
      'Actiecentrum — Vincent boekt het met één tik. Zeg dus NOOIT dat je geen agenda-tool ' +
      'hebt of dat Vincent het zelf in Google Calendar moet zetten: roep deze tool aan met ' +
      'zijn volledige zin.',
    input_schema: {
      type: 'object',
      properties: {
        opdracht: {
          type: 'string',
          description:
            'De volledige zin van Vincent met dag/datum, tijd(vak) en onderwerp. Geef zo ' +
            'compleet mogelijk door; de parser haalt datum, tijd, duur, locatie, deelnemers ' +
            'en eventuele wekelijkse herhaling er zelf uit.',
        },
      },
      required: ['opdracht'],
    },
  },
  {
    name: 'stel_besluit_voor',
    description:
      'Stel een besluit vóór dat een review-gate passeert (goedkeuren, versturen, boeken, ' +
      'afwijzen). Je voert het NIET uit: Vincent krijgt een knop te zien en beslist zelf. ' +
      'Gebruik het item_key uit lees_besluiten.',
    input_schema: {
      type: 'object',
      properties: {
        item_key: { type: 'string' },
        actie: { type: 'string', enum: ['approve', 'send', 'reject', 'dismiss'] },
        waarom: { type: 'string', description: 'Eén zin: waarom raad je dit aan?' },
      },
      required: ['item_key', 'actie', 'waarom'],
    },
  },
  {
    name: 'ritueel_vastleggen',
    description:
      'Legt een moment uit Vincents persoonlijke ritueel vast — ochtend, avond, of een ' +
      'win. Dit is GEEN werk-commando en passeert geen review-gate: het is zijn eigen ' +
      'dagboek, direct opgeslagen. Gebruik dit zodra hij dat in het gesprek noemt (bv. ' +
      '"ik ben dankbaar voor...", "mijn intentie voor vandaag is...", "grote win: ..."), ' +
      'niet alleen als hij er expliciet om vraagt. Vul alleen de velden die hij noemde; ' +
      'verzin nooit een intentie of dankbaarheid die hij niet uitsprak.',
    input_schema: {
      type: 'object',
      properties: {
        soort: { type: 'string', enum: ['ochtend', 'avond', 'win'] },
        intentie: { type: 'string', description: 'Alleen bij soort=ochtend.' },
        dankbaarheid: { type: 'array', items: { type: 'string' }, description: 'Alleen bij soort=ochtend, max 3.' },
        energie: { type: 'integer', description: '1-10, bij ochtend of avond.' },
        wat_ging_goed: { type: 'string', description: 'Alleen bij soort=avond.' },
        top3_morgen: { type: 'array', items: { type: 'string' }, description: 'Alleen bij soort=avond.' },
        gratitude: { type: 'string', description: 'Dankbaarheid vandaag, alleen bij soort=avond.' },
        win_titel: { type: 'string', description: 'Verplicht bij soort=win.' },
        win_beschrijving: { type: 'string', description: 'Alleen bij soort=win.' },
      },
      required: ['soort'],
    },
  },
];

// ── NL datum/tijd-resolver voor agenda-bevestigingen ───────────────────
// Gespiegeld van backend/domains/calendar/nl_command.py zodat de chat nooit een
// verkeerde datum "gokt": de bevestiging spelt exact wat de parser straks boekt.
// ("aanstaande vrijdag niet beschikbaar" -> 14 aug, niet 15 aug.) Bij twijfel
// geeft het '' terug en zegt de tool "ik heb het in het Actiecentrum gezet".
function resolveNlDate(opdracht) {
  const NL = ['zondag','maandag','dinsdag','woensdag','donderdag','vrijdag','zaterdag'];
  const MONTHS = {jan:1,januari:1,feb:2,februari:2,mrt:3,maart:3,apr:4,april:4,mei:5,jun:6,juni:6,jul:7,juli:7,aug:8,augustus:8,sep:9,september:9,okt:10,oktober:10,nov:11,november:11,dec:12,december:12};
  const now = new Date();
  const tz = 'Europe/Amsterdam';
  const strip = (d) => d.toLocaleDateString('nl-NL', { timeZone: tz, weekday:'long', day:'numeric', month:'long' });
  const low = opdracht.toLowerCase();
  // expliciete "dd month" of "dd-month"
  const dm = low.match(/\b(\d{1,2})[\s-]+(jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec|januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\b/);
  if (dm) {
    const d = parseInt(dm[1],10), m = MONTHS[dm[2].slice(0,3)];
    let y = now.getFullYear(); if (m < now.getMonth()+1) y += 1;
    const dt = new Date(y, m-1, d);
    if (!isNaN(dt)) return strip(dt);
  }
  const wd = NL.findIndex((d) => new RegExp('\\b'+d+'(s|en|se)?\\b').test(low));
  if (wd >= 0) {
    let diff = (wd - now.getDay() + 7) % 7; if (diff === 0) diff = 7;
    if (/volgende|komende|next/.test(low)) diff += 7;
    if (/over\s*\d+\s*we(e|e)k/.test(low)) { const n = parseInt(low.match(/over\s*(\d+)\s*we(e|e)k/)[1],10); diff += 7*n; }
    const dt = new Date(now); dt.setDate(now.getDate()+diff);
    return strip(dt);
  }
  if (/morgen/.test(low)) { const dt = new Date(now); dt.setDate(now.getDate()+1); return strip(dt); }
  if (/overmorgen/.test(low)) { const dt = new Date(now); dt.setDate(now.getDate()+2); return strip(dt); }
  return '';
}

// ── Tool-uitvoering (leest Neon, schrijft hooguit een commando) ─────────────

async function runTool(name, input, effects, tenant) {
  if (name === 'lees_context') {
    const rows = await sql`SELECT payload, generated_at FROM context_snapshot WHERE tenant = ${tenant}`;
    const snap = rows[0];
    if (!snap || !snap.payload) return 'Geen contextsnapshot beschikbaar — de machine heeft nog niet gesynct.';
    const section = snap.payload[input.sectie];
    if (!section) return `Sectie '${input.sectie}' ontbreekt in de snapshot.`;
    return JSON.stringify({ gesynct_op: snap.generated_at, [input.sectie]: section }).slice(0, 12000);
  }

  if (name === 'lees_besluiten') {
    const soort = input.soort || null;
    const rows = soort
      ? await sql`SELECT key, dismiss_kind, title, project, summary, created_at, detail
                  FROM sync_items WHERE tenant=${tenant} AND status='active' AND dismiss_kind=${soort}
                  ORDER BY updated_at DESC LIMIT 25`
      : await sql`SELECT key, dismiss_kind, title, project, summary, created_at, detail
                  FROM sync_items WHERE tenant=${tenant} AND status='active' ORDER BY updated_at DESC LIMIT 25`;
    if (!rows.length) return 'Geen open besluiten.';
    const shaped = rows.map((r) => ({
      item_key: r.key, soort: r.dismiss_kind, titel: r.title, project: r.project,
      samenvatting: (r.summary || '').slice(0, 300), sinds: r.created_at,
      // Previews zijn groot (hele artikelen); alleen op verzoek en afgekapt.
      preview: input.detail ? JSON.stringify(r.detail || {}).slice(0, 3000) : undefined,
    }));
    return JSON.stringify(shaped).slice(0, 14000);
  }

  if (name === 'lees_briefing') {
    const rows = await sql`SELECT payload, generated_at FROM briefings
                            WHERE tenant=${tenant} ORDER BY generated_at DESC LIMIT 1`;
    if (!rows.length) return 'Nog geen briefing gesynct.';
    const p = rows[0].payload || {};
    return JSON.stringify({
      gesynct_op: rows[0].generated_at,
      datum: p.iris?.date, cijfers: p.iris?.grades, advies: p.iris?.advice,
      knelpunten: p.bottlenecks, projecten: p.projects, trefkans: p.track_record,
      funnel: p.funnel,
      markdown: (p.iris?.markdown || '').slice(0, 8000),
    }).slice(0, 16000);
  }

  if (name === 'start_werk') {
    const action = input.commando;
    if (!COMMANDS[action]) return `Onbekend commando '${action}'.`;
    const payload = {};
    if (input.site) payload.site = String(input.site);
    if (input.count !== undefined) payload.count = input.count;
    const rows = await sql`
      INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
      VALUES (${tenant}, ${`cmd:${action}`}, 'command', ${action}, ${action}, ${JSON.stringify(payload)}::jsonb)
      ON CONFLICT (tenant, item_key) WHERE status = 'pending' DO NOTHING
      RETURNING id`;
    effects.commands.push({ action, label: COMMANDS[action], queued: rows.length > 0 });
    return rows.length
      ? `In de rij gezet: ${COMMANDS[action]}. AgentOS voert dit uit bij de volgende sync; het resultaat komt achter de review-gate.`
      : `Stond al in de rij: ${COMMANDS[action]}. Niet dubbel gestart.`;
  }

  if (name === 'plan_agenda') {
    const opdracht = (input.opdracht || '').toString().trim();
    if (!opdracht) return 'Geen opdracht meegegeven — geef de volledige zin door.';
    const rows = await sql`
      INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
      VALUES (${tenant}, ${`cmd:calendar_add:${Date.now()}`}, 'command', ${'calendar_add'}, ${'calendar_add'},
              ${JSON.stringify({ text: opdracht })}::jsonb)
      RETURNING id`;
    effects.commands.push({ action: 'calendar_add', label: 'Afspraak in agenda plannen', queued: rows.length > 0 });
    const wanneer = resolveNlDate(opdracht);
    const tijd = /\b\d{1,2}[:.]\d{2}\b/.test(opdracht) ? ' op de genoemde tijd' : (/hele\s*dag|whole\s*day|niet\s*beschikbaar|vrije\s*dag/.test(opdracht.toLowerCase()) ? ' als hele dag' : ' (tijd nog in te vullen)');
    return rows.length
      ? `Agenda-voorstel klaargezet${wanneer ? ' voor ' + wanneer + tijd : ''} uit "${opdracht.slice(0, 80)}". Je ziet het in het Actiecentrum; met één tik van Vincent staat het in Google Agenda (conflictcheck inbegrepen).`
      : 'Kon het agenda-commando niet in de rij zetten.';
  }

  if (name === 'stel_besluit_voor') {
    const item = (await sql`SELECT key, title, dismiss_kind FROM sync_items
                             WHERE tenant = ${tenant} AND key = ${input.item_key}`)[0];
    if (!item) return `Item '${input.item_key}' bestaat niet (meer).`;
    effects.proposals.push({
      item_key: item.key, kind: item.dismiss_kind, title: item.title,
      action: input.actie, why: input.waarom,
    });
    return `Voorstel klaargezet voor Vincent: ${input.actie} op "${item.title}". ` +
      'Hij ziet nu een knop; jij hebt niets uitgevoerd.';
  }

  if (name === 'ritueel_vastleggen') {
    const soort = input.soort;
    const map = { ochtend: 'ritual_morning_save', avond: 'ritual_evening_save', win: 'ritual_win_add' };
    const action = map[soort];
    if (!action) return `Onbekende soort '${soort}' (verwacht ochtend/avond/win).`;
    const payload = { nonce: String(Date.now()) };
    if (soort === 'ochtend') {
      if (input.intentie) payload.intentie = String(input.intentie);
      if (Array.isArray(input.dankbaarheid)) payload.dankbaarheid = input.dankbaarheid.slice(0, 3).map(String);
      if (input.energie !== undefined) payload.energyLevel = input.energie;
    } else if (soort === 'avond') {
      if (input.wat_ging_goed) payload.whatWentWell = String(input.wat_ging_goed);
      if (Array.isArray(input.top3_morgen)) payload.tomorrowTop3 = input.top3_morgen.slice(0, 3).map(String);
      if (input.gratitude) payload.gratitude = String(input.gratitude);
      if (input.energie !== undefined) payload.energyLevel = input.energie;
    } else {
      if (!input.win_titel) return 'Geen titel voor de win meegegeven.';
      payload.title = String(input.win_titel);
      if (input.win_beschrijving) payload.description = String(input.win_beschrijving);
    }
    const rows = await sql`
      INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
      VALUES (${tenant}, ${`cmd:${action}:${payload.nonce}`}, 'command', ${action}, ${action},
              ${JSON.stringify(payload)}::jsonb)
      RETURNING id`;
    effects.commands.push({ action, label: `Ritueel vastgelegd (${soort})`, queued: rows.length > 0 });
    return rows.length
      ? `Vastgelegd. Landt bij de volgende sync in zijn rituelen — geen goedkeuring nodig, het is zijn eigen dagboek.`
      : 'Kon het niet vastleggen.';
  }

  return `Onbekende tool '${name}'.`;
}

// ── Provider-laag (OpenRouter of OpenModel) ─────────────────────────────────
//
// Lokaal draait Iris' denkwerk via OpenRouter (OpenAI chat-completions-formaat,
// Bearer-auth) óf via de OpenModel-gateway (Anthropic Messages-formaat). Die
// twee verschillen niet alleen in endpoint maar ook in hoe tool-calls eruitzien
// — dus de tool-lus mag niet aan één van beide vastzitten. Deze laag kiest de
// provider uit de env en normaliseert elke ronde naar één vorm:
//   { text, toolCalls: [{ id, name, input }] }
// zodat de lus zelf provider-blind blijft.

function pickProvider() {
  // OpenRouter wint als hij gezet is: dat is wat Vincent lokaal gebruikt, en
  // een OpenRouter-key in het OpenModel-veld zou stil falen op een 401.
  if (process.env.OPENROUTER_API_KEY) {
    return {
      name: 'openrouter',
      key: process.env.OPENROUTER_API_KEY,
      url: `${(process.env.OPENROUTER_BASE_URL || 'https://openrouter.ai/api').replace(/\/$/, '')}/v1/chat/completions`,
      model: process.env.OPENROUTER_MODEL || process.env.CLAUDE_VIA_OPENROUTER || 'anthropic/claude-sonnet-4-5',
    };
  }
  if (process.env.OPENMODEL_API_KEY) {
    return {
      name: 'openmodel',
      key: process.env.OPENMODEL_API_KEY,
      url: `${(process.env.OPENMODEL_BASE_URL || 'https://api.openmodel.ai').replace(/\/$/, '')}/v1/messages`,
      model: process.env.OPENMODEL_SMART_MODEL || process.env.OPENMODEL_MODEL || 'claude-sonnet-4-6',
    };
  }
  return null;
}

// TOOLS staat in Anthropic-vorm (input_schema); OpenAI/OpenRouter wil ze als
// {type:'function', function:{name,description,parameters}}.
const OPENAI_TOOLS = TOOLS.map((t) => ({
  type: 'function',
  function: { name: t.name, description: t.description, parameters: t.input_schema },
}));

// Eén ronde bij de provider. `convo` is provider-native (verschilt per provider
// in hoe assistent-tool-calls en tool-resultaten eruitzien); daarom beheert deze
// laag ook het toevoegen ervan, via de teruggegeven `appendAssistant`/`appendTool`.
async function callModel(provider, system, convo, offerTools) {
  if (provider.name === 'openrouter') {
    const r = await fetch(provider.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${provider.key}`,
        // OpenRouter vraagt hierom voor toeschrijving; puur cosmetisch.
        'HTTP-Referer': 'https://iris-remote.vercel.app',
        'X-Title': 'Iris Remote',
      },
      body: JSON.stringify({
        model: provider.model,
        max_tokens: 1500,
        messages: [{ role: 'system', content: system }, ...convo],
        ...(offerTools ? { tools: OPENAI_TOOLS, tool_choice: 'auto' } : {}),
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.error && data.error.message) || `HTTP ${r.status}`);
    const msg = (data.choices && data.choices[0] && data.choices[0].message) || {};
    const toolCalls = (msg.tool_calls || []).map((tc) => {
      let input = {};
      try { input = JSON.parse(tc.function.arguments || '{}'); } catch { input = {}; }
      return { id: tc.id, name: tc.function.name, input };
    });
    return {
      text: (msg.content || '').trim(),
      toolCalls,
      appendAssistant: (c) => c.push(msg), // moet de tool_calls dragen
      appendTool: (c, results) => results.forEach((rr) =>
        c.push({ role: 'tool', tool_call_id: rr.id, content: rr.output })),
    };
  }

  // OpenModel-gateway (Anthropic Messages).
  const r = await fetch(provider.url, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': provider.key,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: provider.model,
      max_tokens: 1500,
      system,
      messages: convo,
      ...(offerTools ? { tools: TOOLS } : {}),
    }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data.error && data.error.message) || `HTTP ${r.status}`);
  const content = data.content || [];
  const toolCalls = content.filter((c) => c.type === 'tool_use')
    .map((u) => ({ id: u.id, name: u.name, input: u.input || {} }));
  return {
    text: content.filter((c) => c.type === 'text').map((c) => c.text).join('').trim(),
    toolCalls,
    appendAssistant: (c) => c.push({ role: 'assistant', content }),
    appendTool: (c, results) => c.push({
      role: 'user',
      content: results.map((rr) => ({ type: 'tool_result', tool_use_id: rr.id, content: rr.output })),
    }),
  };
}

// ── Systeemprompt ───────────────────────────────────────────────────────────

function systemPrompt(snapshotAt, pulse, openCount) {
  const now = new Date().toLocaleString('nl-NL', { timeZone: 'Europe/Amsterdam' });
  return [
    'Je bent Iris: de manager-agent van Agent OS en Vincents persoonlijke assistent.',
    `Het is nu ${now} (Europe/Amsterdam). Vincent spreekt je via Iris Remote op zijn telefoon.`,
    '',
    '## Hoe je werkt',
    '- Je hebt tools. Gebruik ze vóórdat je iets beweert over cijfers, agenda of mail.',
    '  Nooit gokken, nooit cijfers uit je hoofd noemen.',
    '- Denk mee als een scherpe stafchef: benoem wat opvalt, wat het betekent, en wat',
    '  de eerstvolgende stap is. Niet opsommen wat hij al ziet.',
    '- Antwoord in het Nederlands, kort en concreet. Geen inleidingen, geen excuses.',
    '  Cijfers met hun vergelijking erbij ("412 sessies, 22% minder dan vorige week").',
    '- NOOIT een datum of tijdstip uit je hoofd noemen bij agenda-zaken. Roep altijd',
    '  plan_agenda aan en herhaal exact de datum die de tool teruggeeft in je antwoord.',
    '  Een verkeerde dag (bv. "vrijdag 15 augustus" terwijl het de 14e is) is erger',
    '  dan helemaal geen datum noemen.',
    '',
    '## Wat je zelf mag doen',
    '- `start_werk` zet agents aan het werk. Alles wat daaruit komt landt in een',
    '  review-gate, dus je mag dit gebruiken zonder eerst te vragen als het duidelijk',
    '  volgt uit wat Vincent vraagt. Zeg er altijd bij wat je gestart hebt.',
    '- Publiceren en mailen doe je NOOIT zelf: die gates zijn van Vincent. Vind je dat er',
    '  iets goedgekeurd of verstuurd moet worden, gebruik dan `stel_besluit_voor` — hij',
    '  krijgt een knop en beslist zelf.',
    '- Het AGENDA-voorstel mág je wél zelf aanmaken: roep `plan_agenda` aan met Vincents',
    '  volledige zin zodra hij iets in zijn agenda wil (blokken, reserveren, plannen).',
    '  Je zet het als voorstel in het Actiecentrum; het daadwerkelijke boeken in Google',
    '  Agenda blijft zijn tik. Zeg dus NOOIT dat je geen agenda-tool hebt.',
    '',
    '## Zijn persoonlijke ritueel',
    '- `lees_context` met sectie "rituals" laat zien hoe zijn dag/week gaat: ochtend- en',
    '  avondritueel, streaks, energie, waar hij dankbaar voor is, zijn weekintentie en zijn',
    '  persoonlijke doelen met voortgang. Gebruik dit om mee te leven, niet om te sturen —',
    '  het is GEEN actiepunt. Bij lage energie of een dagenlang overgeslagen ritueel dring',
    '  je nooit aan op een zware werk-run (content/outreach/seo_refresh); noem het hooguit',
    '  vriendelijk, en pas je tóón aan.',
    '- Noemt hij zijn intentie, een dankbaarheidsmoment of een win in het gesprek, leg dat',
    '  dan vast met `ritueel_vastleggen`. Dat is zijn eigen dagboek en passeert geen gate —',
    '  je hoeft niet te vragen of het mag, alleen zeggen dat je het hebt vastgelegd.',
    '',
    '## Wat je zeker weet',
    `- De snapshot is van ${snapshotAt || 'onbekend'}. Staat de pc uit, dan is dit het`,
    '  laatste dat je weet — zeg dat erbij als de data verouderd kan zijn.',
    `- Er wachten nu ${openCount} besluiten op Vincent.`,
    pulse ? `- Deterministische stand van zaken (geen LLM-oordeel):\n${JSON.stringify(pulse)}` : '',
  ].filter(Boolean).join('\n');
}

// ── Handler ─────────────────────────────────────────────────────────────────

export default async function handler(req, res) {
  if (req.method !== 'POST') return json(res, 405, { error: 'POST only' });
  const tenant = await requireSession(req, res);
  if (!tenant) return;

  const provider = pickProvider();
  if (!provider) {
    return json(res, 400, {
      error: 'Geen LLM-key in de Vercel-env — zet OPENROUTER_API_KEY (of OPENMODEL_API_KEY). Cloud-Iris staat uit.',
    });
  }

  // Alleen geldige, recente beurten doorlaten — de client is geen bron van waarheid.
  const messages = ((req.body || {}).messages || [])
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .slice(-MAX_TURNS)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 4000) }));
  if (!messages.length || messages[messages.length - 1].role !== 'user') {
    return json(res, 400, { error: 'Geen vraag ontvangen' });
  }

  try {
    const snap = (await sql`SELECT payload, generated_at FROM context_snapshot WHERE tenant = ${tenant}`)[0];
    const openCount = (await sql`SELECT count(*)::int AS c FROM sync_items WHERE tenant=${tenant} AND status='active'`)[0].c;
    const system = systemPrompt(
      snap ? String(snap.generated_at).slice(0, 16) : null,
      snap?.payload?.pulse || null,
      openCount,
    );

    const effects = { commands: [], proposals: [] };
    const convo = [...messages];

    let reply = '';
    for (let round = 0; round <= MAX_TOOL_ROUNDS; round += 1) {
      // In de laatste ronde geen tools meer aanbieden: anders kan het model in
      // tool-calls blijven hangen en krijgt Vincent nooit een antwoord.
      let step;
      try {
        step = await callModel(provider, system, convo, round < MAX_TOOL_ROUNDS);
      } catch (e) {
        return json(res, 502, { error: `${provider.name}: ${String(e.message).slice(0, 200)}` });
      }
      reply = step.text || reply;
      if (!step.toolCalls.length) break;

      step.appendAssistant(convo);
      const results = [];
      for (const call of step.toolCalls) {
        let out;
        try {
          out = await runTool(call.name, call.input || {}, effects, tenant);
        } catch (e) {
          console.error('tool error', call.name, e);
          out = `Tool '${call.name}' faalde: ${String(e).slice(0, 200)}`;
        }
        results.push({ id: call.id, output: String(out) });
      }
      step.appendTool(convo, results);
    }

    if (!reply) return json(res, 502, { error: 'Leeg antwoord van het model — probeer opnieuw.' });
    return json(res, 200, { reply, commands: effects.commands, proposals: effects.proposals });
  } catch (e) {
    console.error('iris error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}
