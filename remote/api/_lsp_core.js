// LSP-workshop-analyse ("Bouw je AI-assistent", AI Leadership Lab, 27 aug
// 2026) — gedeeld door beide kanalen (whatsapp.js voor het live WhatsApp-pad,
// een e-mailpad kan hetzelfde hergebruiken). Géén tool-lus zoals _iris_core.js
// of _customer_core.js: één foto + één toelichtende regel in, twee teksten
// uit. Twee promptversies die uit elkaar kunnen lopen is precies de fout die
// CLAUDE.md bij de Gauntlet-teller beschrijft — daarom hier maar één functie.
import { pickProvider, toMultimodalUserMessage } from './_iris_core.js';

const SYSTEM_PROMPT = [
  'Je bent Iris tijdens het AI Leadership Lab van WeAreImpact en Grantmaster',
  '(27 augustus 2026, CIC Rotterdam). Teams bouwen tijdens een Lego Serious',
  'Play-oefening met LEGO ofwel hun grootste administratieve frictie, ofwel',
  'hun ideale AI-assistent, en sturen jou daarna een foto van het bouwwerk met',
  'een korte toelichtende regel. Jij analyseert de foto en de regel samen en',
  'levert direct bruikbaar advies.',
  '',
  'Antwoord uitsluitend met een JSON-object met precies twee velden:',
  '{"dashboard_summary": "...", "participant_report": "..."}',
  '',
  '"dashboard_summary": twee tot drie korte zinnen, bedoeld voor een groot',
  'projectiescherm dat het hele team tegelijk leest. Geen jargon, geen',
  'inleiding, meteen de kern van wat het bouwwerk laat zien.',
  '',
  '"participant_report": het volledige rapport voor het team zelf, in',
  'lopende tekst (geen markdown-koppen, WhatsApp en e-mail renderen dat niet).',
  'Bouw het op uit twee onderdelen die je met een korte kopzin inleidt:',
  '1. Een concrete prompt-architectuur: welke rol krijgt de AI-assistent,',
  '   welke taak precies, en in welk outputformaat — toegespitst op wat de',
  '   foto en de toelichting laten zien, nooit generiek.',
  '2. Een concrete workflow-oplossing: de stappen, welke controle een mens',
  '   moet blijven doen, en waar het misgaat als je dit te snel volledig',
  '   automatiseert. Eindig met één zin die het team maandagochtend meteen',
  '   kan uitproberen.',
  '',
  'Regels: schrijf in het Nederlands, gebruik nooit liggende streepjes (—) of',
  "emoji's, verzin nooit cijfers, bedrijven of tools die niet aannemelijk uit",
  'de foto of de toelichting volgen. Kun je de foto niet goed genoeg lezen om',
  'iets concreets te zeggen, zeg dat dan expliciet in beide velden in plaats',
  'van iets te verzinnen.',
].join('\n');

function extractJson(text) {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end < start) return null;
  try { return JSON.parse(text.slice(start, end + 1)); } catch { return null; }
}

const FALLBACK_REPORT =
  "Iris kon dit keer geen volledige analyse maken van jullie bouwwerk. Stuur "
  + "de foto gerust nog eens, of vraag het tijdens de sessie direct aan "
  + 'Vincent of André — zij helpen je meteen verder.';

// { text, image: {mediaType, base64}, contactName } → { dashboard_summary, participant_report }
export async function analyzeLspBuild({ text, image, contactName }) {
  const provider = pickProvider();
  if (!provider) {
    return {
      dashboard_summary: 'Nieuwe inzending binnen, analyse volgt zo.',
      participant_report: FALLBACK_REPORT,
      error: 'geen LLM-provider geconfigureerd',
    };
  }

  const noteLine = text && text.trim()
    ? `Toelichting van het team: "${text.trim()}"`
    : 'Het team gaf geen toelichtende regel mee.';
  const naam = contactName ? `Contactpersoon: ${contactName}.` : '';
  const userText = [noteLine, naam].filter(Boolean).join(' ');
  const userMessage = toMultimodalUserMessage(userText, image, provider.name);

  try {
    const r = await fetch(provider.url, {
      method: 'POST',
      headers: provider.name === 'openrouter'
        ? { 'content-type': 'application/json', authorization: `Bearer ${provider.key}` }
        : { 'content-type': 'application/json', 'x-api-key': provider.key, 'anthropic-version': '2023-06-01' },
      body: JSON.stringify(provider.name === 'openrouter'
        ? { model: provider.model, max_tokens: 1200, messages: [{ role: 'system', content: SYSTEM_PROMPT }, userMessage] }
        : { model: provider.model, max_tokens: 1200, system: SYSTEM_PROMPT, messages: [userMessage] }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data.error && data.error.message) || `HTTP ${r.status}`);

    const raw = provider.name === 'openrouter'
      ? String((data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || '')
      : String((data.content || []).filter((c) => c.type === 'text').map((c) => c.text).join(''));

    const parsed = extractJson(raw);
    if (parsed && parsed.dashboard_summary && parsed.participant_report) {
      return {
        dashboard_summary: String(parsed.dashboard_summary).slice(0, 600),
        participant_report: String(parsed.participant_report).slice(0, 4000),
      };
    }
    // Geen valide JSON: liever de ruwe tekst als rapport tonen dan een
    // deelnemer met niets te laten staan tijdens een live sessie.
    return {
      dashboard_summary: 'Nieuwe inzending binnen, analyse volgt zo.',
      participant_report: raw.trim() || FALLBACK_REPORT,
      error: 'antwoord niet als JSON te parsen',
    };
  } catch (e) {
    console.error('lsp: analyse mislukt', e);
    return {
      dashboard_summary: 'Nieuwe inzending binnen, analyse volgt zo.',
      participant_report: FALLBACK_REPORT,
      error: String(e).slice(0, 300),
    };
  }
}
