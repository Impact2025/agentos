// Klant-Iris — een heel andere pet dan de manager-Iris in _iris_core.js.
// Zelfde nummer, zelfde LLM-laag (pickProvider/callModel), maar een klant mag
// NOOIT bij start_werk, stel_besluit_voor of Vincents mail/agenda/analytics
// kunnen komen. Daarom een eigen, veel kleiner toolset (precies één tool:
// escaleren) en een eigen kennisbron: de `helpdesk`-sectie die de lokale
// AgentOS-machine elke bridge_sync meestuurt (context.py:build_helpdesk),
// niet de rijke manager-context.
//
// Regel, hard: onzeker antwoord of iets met gevolgen (offerte, afspraak,
// klacht, persoonsgegevens) → nooit verzinnen, altijd escaleren naar Vincent.
// Zie whatsapp_escalations in schema.sql voor hoe dat verder loopt.
import { sql } from './_lib.js';
import { pickProvider, callModel } from './_iris_core.js';

export const MAX_CUSTOMER_TURNS = 10;

const ESCALATE_TOOL = {
  name: 'escaleer_naar_vincent',
  description:
    'Meld dat jij dit niet zelf kunt of mag afhandelen. Gebruik dit zodra het antwoord ' +
    'niet met zekerheid uit de meegegeven kennis volgt, OF zodra de vraag iets met ' +
    'gevolgen betreft die niet via `lees_vrije_momenten`/`stel_afspraak_voor` op te lossen ' +
    'zijn — een offerte, een klacht, of persoonsgegevens/een bestaand account. Verzin NOOIT ' +
    'een prijs, garantie, levertijd of toezegging die niet letterlijk in de kennis staat — ' +
    'escaleer in plaats daarvan. Jij stuurt daarna geen inhoudelijk antwoord meer; Vincent ' +
    'neemt het over.',
  input_schema: {
    type: 'object',
    properties: { reden: { type: 'string', description: 'Kort: waarom kun jij dit niet afhandelen?' } },
    required: ['reden'],
  },
};

// Twee tools voor een afspraakverzoek — bewust in twee stappen (eerst lezen,
// dan pas voorstellen): een tijd voorstellen zonder eerst te kijken wanneer
// Vincent vrij is, is precies het soort verzinnen dat de systeemprompt
// verbiedt. Wat ze WEL mag zien is uitsluitend start/eind van vrije blokken
// (`context.py:build_helpdesk`, afgeleid van `build_agenda`'s `free_by_day`)
// — geen titel, geen deelnemer; wat ze voorstelt landt als `calendar_add`-
// commando in dezelfde `decisions`-rij en dus dezelfde review-gate
// (`calendar_proposals`, pending_review) als wanneer Vincent het zelf via
// WhatsApp aan manager-Iris vraagt. Er wordt hier nooit automatisch geboekt.
const FREE_SLOTS_TOOL = {
  name: 'lees_vrije_momenten',
  description:
    "Geeft Vincents vrije blokken (≥45 min) voor de komende dagen — uitsluitend tijden, " +
    'nooit waar hij mee bezet is. Gebruik dit ALTIJD vóórdat je een tijd voorstelt, zodra ' +
    'een klant vraagt naar een afspraak, bel-moment, kennismaking of "heeft Vincent tijd".',
  input_schema: { type: 'object', properties: {}, required: [] },
};

const PROPOSE_APPOINTMENT_TOOL = {
  name: 'stel_afspraak_voor',
  description:
    "Zet een afspraakvoorstel klaar in Vincents goedkeur-wachtrij, nadat de klant een tijd " +
    "uit 'lees_vrije_momenten' heeft gekozen. Jij boekt NIETS — Vincent keurt het met één " +
    'tik goed of wijst het af. Zijn WhatsApp-nummer wordt automatisch aan het voorstel ' +
    'gehangen, dat hoef je niet zelf te noemen. Vraag WEL naar de naam van de klant als je die ' +
    'nog niet weet (vóór je deze tool aanroept, niet erna) en beschrijf waar het gesprek over ' +
    'gaat — dat helpt Vincent zich voor te bereiden.',
  input_schema: {
    type: 'object',
    properties: {
      opdracht: {
        type: 'string',
        description:
          'Volledige zin met datum, tijd en onderwerp, plus de naam van de klant als je die ' +
          'weet, bv. "Belafspraak met Jan de Boer woensdag 20 augustus om 14:00 over een ' +
          'teambuildingdag".',
      },
    },
    required: ['opdracht'],
  },
};

export const CUSTOMER_TOOLS = [ESCALATE_TOOL, FREE_SLOTS_TOOL, PROPOSE_APPOINTMENT_TOOL];
// Harde allowlist van tool-namen die klant-Iris mág aanroepen. Wordt zowel
// gebruikt om de toolset aan het model door te geven (zodat het ze kan kiezen)
// als om elke tool-call van het model tégen te checken vóór uitvoering
// (defense-in-depth: een verkeerd uitgelijnd model dat toch 'start_werk'
// teruggeeft, mag nooit bij de manager-toolset komen — zie customerConverse).
const CUSTOMER_TOOL_NAMES = new Set(CUSTOMER_TOOLS.map((t) => t.name));
const MAX_CUSTOMER_TOOL_ROUNDS = 3;

function normHost(url) {
  return String(url || '').toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
}

// Spatie-/accentloos vergelijken (zelfde patroon als shared/projects.py:
// squash_project) — een merknaam wordt even vaak aaneen als los geschreven
// ("Steentjebij Steentje" vs "steentje bij steentje", gemeten 18 aug 2026:
// een klant typte de losse vorm en de gate vond 'm niet). Een letterlijke
// substring-match op de ruwe tekst is dus te bros; wat overblijft na het
// weghalen van spaties/leestekens/accenten is de vergelijking die standhoudt.
function squash(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '') // accenten eraf
    .replace(/[^a-z0-9]/g, '');
}

// Deterministisch vóór LLM: een merknaam of domein die (spatieloos) in het
// bericht staat is een betrouwbaardere match dan een gok van het model, en
// kost geen API-call. Pas als dit niets vindt, vragen we het rechtstreeks —
// nooit aannemen welk project een onbekende afzender bedoelt (zie 3a in
// CLAUDE.md: het verkeerde project aannemen is precies de fout die daar
// beschreven staat, alleen dan voor een klant in plaats van voor Vincent).
export function resolveProject(sites, text) {
  const low = squash(text);
  for (const s of sites) {
    const name = squash(s.project);
    const host = squash(normHost(s.base_url));
    if (name && low.includes(name)) return s.project;
    if (host && low.includes(host)) return s.project;
  }
  return null;
}

export async function resolveOrAskProject(tenant, text) {
  const rows = await sql`SELECT payload FROM context_snapshot WHERE tenant = ${tenant}`;
  const sites = rows[0]?.payload?.helpdesk?.sites || [];
  const project = resolveProject(sites, text);
  if (project) return { project, ask: null };
  const names = sites.map((s) => s.project).filter(Boolean);
  const ask = names.length
    ? `Hoi! Ik ben Iris, de AI-assistent hier. Voor welk bedrijf of welke website neem je contact op? (${names.join(', ')})`
    : 'Hoi! Ik ben Iris, de AI-assistent hier. Voor welk bedrijf of welke website neem je contact op?';
  return { project: null, ask };
}

function knowledgeFor(helpdesk, project) {
  const site = (helpdesk.sites || []).find((s) => s.project === project);
  if (!site) return '';
  const lines = [`# ${site.project}`];
  if (site.base_url) lines.push(`Website: ${site.base_url}`);
  if (site.profile) lines.push(site.profile);
  if (site.ctas && site.ctas.length) lines.push('Call-to-actions die passen bij dit merk:\n- ' + site.ctas.join('\n- '));
  if (site.live_pages && site.live_pages.length) {
    lines.push(
      "# Live pagina's (de ENIGE URL's die je mag noemen — verzin nooit een andere link)\n"
      + site.live_pages.map((p) => `- ${p.title}: ${p.url}`).join('\n'));
  }
  if (helpdesk.maker) lines.push(`# Over de maker\n${helpdesk.maker}`);
  return lines.join('\n\n');
}

function customerSystemPrompt(project, knowledge, isFirstMessage) {
  return [
    `Je bent Iris, de AI-assistent van ${project}. Je praat via WhatsApp met een klant of `
      + 'geïnteresseerde — dit is niet Vincent, en jij bent hier klantenservice, geen manager.',
    isFirstMessage
      ? 'Dit is het eerste bericht van dit gesprek. Begin je antwoord met een korte, '
        + `natuurlijke zin dat je de AI-assistent van ${project} bent, voordat je de vraag beantwoordt.`
      : 'Dit gesprek loopt al — geen introductie meer nodig, ga direct op de vraag in.',
    '',
    '## Regels',
    '- Antwoord ALLEEN op basis van de kennis hieronder. Verzin nooit een prijs, garantie, '
      + 'levertijd, URL of toezegging die er niet letterlijk in staat.',
    '- Twijfel je, of gaat het om een offerte, klacht, of persoonsgegevens/een bestaand '
      + 'account: gebruik dan `escaleer_naar_vincent`. Dat is geen falen — beter eerlijk '
      + 'doorverwijzen dan iets verzinnen.',
    '- Wil de klant een afspraak, bel-moment of kennismaking: gebruik EERST '
      + "`lees_vrije_momenten` om te zien wanneer Vincent tijd heeft, stel dat voor, en zodra "
      + 'de klant een tijd kiest zet je het met `stel_afspraak_voor` klaar. Nooit zelf een '
      + 'tijd verzinnen of beloven dat het geboekt is — dat doet pas Vincents goedkeuring.',
    '- Kort en vriendelijk, chat-stijl. Geen markdown-koppen, geen lange lappen tekst.',
    "- Geen liggende streepjes (—) en geen emoji's.",
    '',
    '## Kennis',
    knowledge || '(geen kennis beschikbaar voor dit project — escaleer als de vraag meer dan een begroeting is)',
  ].join('\n');
}

// `waId` wordt hier ALTIJD zelf in de opdrachttekst geplakt — nooit
// overgelaten aan of het model eraan denkt. Gemeten (18 aug 2026, eerste
// echte testgesprek): de klant gaf zijn naam pas ná het voorstel, dus stond
// er een naamloos "Over"-blok in de agenda zonder enige aanwijzing wie het
// was. `nl_command.py` geeft bovendien pas een bruikbare titel (de hele zin,
// niet het eerste toevallige woord) zodra hij een "met [Naam]"-patroon
// herkent — daarom altijd "met Klant <nummer>" vóór de eigen tekst van het
// model, ongeacht wat het model zelf schreef.
async function runCustomerTool(name, input, tenant, freeByDay, waId) {
  if (name === 'lees_vrije_momenten') {
    if (!freeByDay || !freeByDay.length) {
      return 'Geen agendagegevens beschikbaar — escaleer als de klant om een afspraak vraagt.';
    }
    return JSON.stringify(freeByDay).slice(0, 4000);
  }
  if (name === 'stel_afspraak_voor') {
    const opdracht = (input.opdracht || '').toString().trim();
    if (!opdracht) return 'Geen opdracht meegegeven — beschrijf datum, tijd en onderwerp.';
    const finalText = `Afspraak met Klant ${waId} via WhatsApp: ${opdracht}`;
    const rows = await sql`
      INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
      VALUES (${tenant}, ${`cmd:calendar_add:${Date.now()}`}, 'command', ${'calendar_add'}, ${'calendar_add'},
              ${JSON.stringify({ text: finalText })}::jsonb)
      RETURNING id`;
    if (!rows.length) return 'Kon het voorstel niet klaarzetten — escaleer naar Vincent.';
    return { proposed: true, message:
      'Voorstel klaargezet in Vincents goedkeur-wachtrij. Zeg tegen de klant dat het voorstel '
      + 'klaarstaat en dat Vincent het nog moet bevestigen — het is dus nog geen definitieve boeking.' };
  }
  return `Onbekende tool '${name}'.`;
}

// Retourneert {reply, escalated, reason?, proposed?}. `history` is al
// getrimd door de aanroeper (whatsapp.js), in dezelfde [{role,content}] vorm
// als de manager-lus. `waId` is de afzender — nooit aan het model gevraagd,
// altijd zelf meegegeven aan `stel_afspraak_voor` (zie runCustomerTool).
//
// `callModelFn`, `runToolFn` en `loadHelpdeskFn` zijn optioneel te injecteren
// (voor tests): zo kunnen we customerConverse aanroepen zónder een echte
// LLM-key of een Neon-verbinding. Standaard vallen ze terug op de echte
// implementaties.
export async function customerConverse(tenant, project, history, userText, waId, opts = {}) {
  const callModelFn = opts.callModelFn || callModel;
  const runToolFn = opts.runToolFn || runCustomerTool;
  const loadHelpdeskFn = opts.loadHelpdeskFn || (async () => {
    const rows = await sql`SELECT payload FROM context_snapshot WHERE tenant = ${tenant}`;
    return rows[0]?.payload?.helpdesk || { sites: [] };
  });

  const provider = pickProvider();
  if (!provider) throw new Error('Geen LLM-key geconfigureerd.');

  const helpdesk = await loadHelpdeskFn();
  const freeByDay = helpdesk.free_by_day || [];
  const knowledge = knowledgeFor(helpdesk, project);
  const isFirst = history.length === 0;
  const system = customerSystemPrompt(project, knowledge, isFirst);
  const convo = [...history, { role: 'user', content: userText }];

  let reply = '';
  let proposed = false;
  for (let round = 0; round <= MAX_CUSTOMER_TOOL_ROUNDS; round += 1) {
    const step = await callModelFn(provider, system, convo, round < MAX_CUSTOMER_TOOL_ROUNDS ? CUSTOMER_TOOLS : null);
    reply = step.text || reply;

    const escalateCall = step.toolCalls.find((c) => c.name === 'escaleer_naar_vincent');
    if (escalateCall) {
      const reden = (escalateCall.input && escalateCall.input.reden) || 'onduidelijke of gevoelige vraag';
      return {
        reply: 'Bedankt voor je bericht! Ik geef dit door, je hoort snel iets van ons terug.',
        escalated: true,
        reason: reden,
      };
    }

    if (!step.toolCalls.length) break;

    // Defense-in-depth: een klant mag NOOIT een manager-tool (start_werk,
    // stel_besluit_voor, plan_agenda, ...) aanroepen — ook niet als het model
    // er per ongeluk een teruggeeft. Elke tool-call die niet op de expliciete
    // allowlist staat, wordt geweigerd en resulteert in een escalatie naar
    // Vincent. Zo kan een verkeerd uitgelijnd model nooit de review-gate
    // omzeilen. (Deze grens is bewezen door tests/whatsapp-security.test.js.)
    const forbidden = step.toolCalls.find((c) => !CUSTOMER_TOOL_NAMES.has(c.name));
    if (forbidden) {
      console.error('klant-Iris weigerde niet-toegestane tool', forbidden.name, 'voor', tenant, project);
      return {
        reply: 'Bedankt voor je bericht! Ik geef dit door, je hoort snel iets van ons terug.',
        escalated: true,
        reason: `model probeerde niet-toegestane tool '${forbidden.name}' aan te roepen`,
      };
    }

    step.appendAssistant(convo);
    const results = [];
    for (const call of step.toolCalls) {
      let out;
      try {
        out = await runToolFn(call.name, call.input || {}, tenant, freeByDay, waId);
      } catch (e) {
        console.error('klant-tool faalde', call.name, e);
        out = `Tool '${call.name}' faalde: ${String(e).slice(0, 200)}`;
      }
      if (out && typeof out === 'object' && out.proposed) {
        proposed = true;
        out = out.message;
      }
      results.push({ id: call.id, output: String(out) });
    }
    step.appendTool(convo, results);
  }

  if (!reply) throw new Error('Leeg antwoord van het model.');
  return { reply, escalated: false, proposed };
}
