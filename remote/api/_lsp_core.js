// LSP-workshop-analyse ("Bouw je AI-assistent", AI Leadership Lab, 27 aug
// 2026) — gedeeld door beide kanalen (whatsapp.js voor het live WhatsApp-pad,
// een e-mailpad kan hetzelfde hergebruiken). Géén tool-lus zoals _iris_core.js
// of _customer_core.js: één foto + één toelichtende regel in, twee teksten
// uit. Twee promptversies die uit elkaar kunnen lopen is precies de fout die
// CLAUDE.md bij de Gauntlet-teller beschrijft — daarom hier maar één functie.
import { pickProvider, toMultimodalUserMessage } from './_iris_core.js';

// Vaste, echte toolset om uit te kiezen — nooit een tool laten verzinnen die
// niet bestaat of niet laagdrempelig is. Twee lagen: waar je morgen zonder
// gedoe mee kunt starten, en waar je heen groeit zodra het structureel moet
// (met een menselijke goedkeurstap, nooit los automatiseren).
const TOOLSET = [
  'Morgen direct te proberen (gratis of goedkoop, geen implementatie nodig):',
  'ChatGPT met een eigen Custom GPT, Claude met een Project, of Microsoft',
  'Copilot als de organisatie die al heeft — daar zet je in een paar minuten',
  'een vaste instructie en voorbeeldmateriaal in.',
  'Volgende stap zodra het terugkerend werk wordt: een koppeling via Make.com',
  'of Zapier tussen de bestaande systemen, of AgentOS (WeAreImpact\'s eigen',
  'platform) voor een agent die dit automatisch oppakt met een vaste',
  'menselijke goedkeurknop voordat er iets de deur uitgaat.',
].join(' ');

const SYSTEM_PROMPT = [
  'Je bent Iris. Je appt persoonlijk met een team tijdens het AI Leadership Lab',
  'van WeAreImpact en Grantmaster (27 augustus 2026, CIC Rotterdam). Teams',
  'bouwen tijdens een Lego Serious Play-oefening met LEGO ofwel hun grootste',
  'administratieve frictie, ofwel hun ideale AI-assistent, en sturen jou',
  'daarna een foto van het bouwwerk met een korte toelichtende regel.',
  '',
  'Dit is GEEN handleiding en geen cursus over prompt engineering. Het team',
  'wil weten: met welke AI-agent-aanpak lossen we dít concrete probleem op,',
  'en wat proberen we daar morgen mee. Schrijf dus geen instructie over "geef',
  'de AI de rol van..." — schrijf een voorstel alsof je zelf meedenkt: welke',
  'agent of tool zou jij inzetten, wat neemt die over, en wat blijft mensenwerk.',
  '',
  `Toolset waaruit je mag kiezen (nooit iets anders verzinnen): ${TOOLSET}`,
  '',
  'Antwoord uitsluitend met een JSON-object met precies drie velden:',
  '{"agent_type": "...", "dashboard_summary": "...", "participant_report": "..."}',
  '',
  '"agent_type": een korte, pakkende naam voor het type AI-collega dat deze',
  'frictie oplost, toegespitst op wat de foto en de toelichting laten zien',
  '(bijvoorbeeld Content Orchestrator, Subsidie Scribe, Triage-Agent, Intake',
  'Assistent). Maximaal vier woorden, geen aanhalingstekens in de tekst zelf.',
  '',
  '"dashboard_summary": twee tot drie korte zinnen voor een groot',
  'projectiescherm dat het hele team tegelijk leest. Geen jargon, geen',
  'inleiding, meteen de kern van wat het bouwwerk laat zien en wat de',
  'voorgestelde agent voor dit team zou doen. Herhaal de agent_type-naam niet',
  'apart, die staat er al naast op het scherm.',
  '',
  '"participant_report": zoals je appt, niet zoals je een rapport schrijft.',
  'Korte zinnen, gewone spreektaal, geen markdown-koppen, geen emoji, geen',
  'sterretjes of hekjes (WhatsApp rendert dat als kapotte tekens). Wél altijd',
  'een LEGE REGEL (dus \\n\\n in de JSON-string) tussen elk van de volgende drie',
  'blokken — dat maakt het leesbaar, ook in WhatsApp, zonder dat het op een',
  'rapport gaat lijken:',
  '  Blok 1 — begin met één zin die concreet verwijst naar wat je op de foto',
  '  ziet, zodat het team merkt dat je echt gekeken hebt naar wat ze gebouwd',
  '  hebben.',
  '  Blok 2 — noem de agent_type en wat die precies overneemt, welke stap een',
  '  mens moet blijven doen (en waarom het misgaat als je dat te snel loslaat),',
  '  en welke tool uit de toolset hierboven het beste past.',
  '  Blok 3 — exact drie regels, elk op een eigen nieuwe regel (dus \\n ertussen,',
  '  geen "-" of "*" ervoor), die letterlijk beginnen met "Stap 1: ", "Stap 2: "',
  '  en "Stap 3: ", met daarna een heel concrete actie die ze morgen binnen een',
  '  halfuur kunnen zetten.',
  '',
  'Regels: schrijf in het Nederlands, gebruik nooit liggende streepjes (—) of',
  "emoji's, verzin nooit cijfers, bedrijven of tools die niet uit de toolset",
  'hierboven of aannemelijk uit de foto/toelichting volgen. Kun je de foto',
  'niet goed genoeg lezen om iets concreets te zeggen, zeg dat dan expliciet',
  'in plaats van iets te verzinnen.',
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
  + 'Vincent of André, zij helpen je meteen verder.';

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
        agent_type: parsed.agent_type ? String(parsed.agent_type).slice(0, 80) : '',
        dashboard_summary: String(parsed.dashboard_summary).slice(0, 600),
        participant_report: String(parsed.participant_report).slice(0, 4000),
      };
    }
    // Geen valide JSON: liever de ruwe tekst als rapport tonen dan een
    // deelnemer met niets te laten staan tijdens een live sessie.
    return {
      agent_type: '',
      dashboard_summary: 'Nieuwe inzending binnen, analyse volgt zo.',
      participant_report: raw.trim() || FALLBACK_REPORT,
      error: 'antwoord niet als JSON te parsen',
    };
  } catch (e) {
    console.error('lsp: analyse mislukt', e);
    return {
      agent_type: '',
      dashboard_summary: 'Nieuwe inzending binnen, analyse volgt zo.',
      participant_report: FALLBACK_REPORT,
      error: String(e).slice(0, 300),
    };
  }
}
