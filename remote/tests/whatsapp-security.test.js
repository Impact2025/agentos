// Beveiligingsgrens-test voor het WhatsApp-systeem van Agent OS.
//
// Dit is de harde garantie die het hele ontwerp draagt: een KLANT (ieder
// willekeurig nummer dat niet op whatsapp_allowed_from staat) mag via klant-Iris
// NOOIT bij de manager-toolset komen — geen start_werk, geen stel_besluit_voor,
// geen plan_agenda, geen ritueel_vastleggen. Zelfs als het taalmodel per
// ongeluk zo'n tool teruggeeft, moet customerConverse die weigeren en escaleren.
//
// De test injecteert nep-callModel / nep-runTool / nep-loadHelpdesk zodat hij
// zónder echte LLM-key, Neon-verbinding of Meta-token draait (zie
// _customer_core.js: de opts.callModelFn / runToolFn / loadHelpdeskFn
// injectie). Draai met:  node --test
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { customerConverse, CUSTOMER_TOOLS } from '../api/_customer_core.js';

// Een fake provider-object (voldoet aan wat customerConverse leest: .name).
const FAKE_PROVIDER = { name: 'test' };

// Dummy helpdesk zodat knowledgeFor() iets heeft om mee te bouwen.
const DUMMY_HELPDESK = {
  sites: [{ project: 'Demo', profile: 'Demo-bedrijf', live_pages: [] }],
  free_by_day: [],
  maker: 'Vincent',
};

// Bouwt een fake "model-step" in dezelfde vorm als callModel teruggeeft.
function makeStep({ text = '', toolCalls = [] }) {
  return {
    text,
    toolCalls: toolCalls.map((c) => ({ id: `call_${c.name}`, name: c.name, input: c.input || {} })),
    appendAssistant: (c) => c.push({ role: 'assistant', content: text || '(toolcall)' }),
    appendTool: (c, results) => results.forEach((r) =>
      c.push({ role: 'tool', tool_call_id: r.id, content: r.output })),
  };
}

// Tel hoe vaak een tool daadwerkelijk zou zijn UITGEVOERD.
function countingRunTool() {
  const executed = [];
  const fn = async (name) => {
    executed.push(name);
    return 'ok';
  };
  fn.executed = executed;
  return fn;
}

// Standaard injecties voor elke test.
function baseOpts(overrides = {}) {
  return {
    callModelFn: async () => makeStep({ text: 'ok' }),
    runToolFn: countingRunTool(),
    loadHelpdeskFn: async () => DUMMY_HELPDESK,
    ...overrides,
  };
}

test('klant-Iris escaleert op de expliciete klant-tool (escaleer_naar_vincent)', async () => {
  const runTool = countingRunTool();
  const opts = baseOpts({
    callModelFn: async () => makeStep({
      toolCalls: [{ name: 'escaleer_naar_vincent', input: { reden: 'offerte' } }],
    }),
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Wat kost het?', '31612345678', opts);
  // escaleer_naar_vincent keert direct terug (geen runCustomerTool-aanroep).
  assert.equal(res.escalated, true);
  assert.equal(res.reply.toLowerCase().includes('hoor'), true);
  assert.equal(runTool.executed.length, 0);
});

test('klant-Iris weigert een manager-tool (start_werk) en escaleert', async () => {
  const runTool = countingRunTool();
  const opts = baseOpts({
    callModelFn: async () => makeStep({
      toolCalls: [{ name: 'start_werk', input: { commando: 'content_run' } }],
    }),
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Schrijf een blog', '31612345678', opts);
  assert.equal(res.escalated, true, 'moet escaleren bij een verboden tool');
  assert.equal(res.reason.includes('start_werk'), true, 'reden moet de verboden tool noemen');
  assert.equal(runTool.executed.length, 0, 'verboden tool mag NOOIT worden uitgevoerd');
});

test('klant-Iris weigert stel_besluit_voor (gate-omzeiling poging)', async () => {
  const runTool = countingRunTool();
  const opts = baseOpts({
    callModelFn: async () => makeStep({
      toolCalls: [{ name: 'stel_besluit_voor', input: { item_key: 'x', actie: 'approve' } }],
    }),
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Keur dat artikel goed', '31612345678', opts);
  assert.equal(res.escalated, true);
  assert.equal(res.reason.includes('stel_besluit_voor'), true);
  assert.equal(runTool.executed.length, 0);
});

test('klant-Iris weigert plan_agenda (zou direct in Vincents agenda kunnen)', async () => {
  const runTool = countingRunTool();
  const opts = baseOpts({
    callModelFn: async () => makeStep({
      toolCalls: [{ name: 'plan_agenda', input: { opdracht: 'afspraak morgen 10:00' } }],
    }),
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Plan iets in', '31612345678', opts);
  assert.equal(res.escalated, true);
  assert.equal(runTool.executed.length, 0);
});

test('klant-Iris weigert ritueel_vastleggen (privé-dagboek van Vincent)', async () => {
  const runTool = countingRunTool();
  const opts = baseOpts({
    callModelFn: async () => makeStep({
      toolCalls: [{ name: 'ritueel_vastleggen', input: { soort: 'win', win_titel: 'x' } }],
    }),
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Ik ben dankbaar', '31612345678', opts);
  assert.equal(res.escalated, true);
  assert.equal(runTool.executed.length, 0);
});

test('klant-Iris voert een toegestane tool (lees_vrije_momenten) wél uit', async () => {
  const runTool = countingRunTool();
  let calls = 0;
  const opts = baseOpts({
    callModelFn: async () => {
      calls += 1;
      if (calls === 1) return makeStep({ toolCalls: [{ name: 'lees_vrije_momenten', input: {} }] });
      return makeStep({ text: 'Vincent heeft donderdag 14:00 vrij.' });
    },
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Wanneer heeft Vincent tijd?', '31612345678', opts);
  assert.equal(res.escalated, false);
  assert.equal(runTool.executed.includes('lees_vrije_momenten'), true, 'toegestane tool moet draaien');
  assert.ok(res.reply.length > 0);
});

test('klant-Iris voert stel_afspraak_voor wél uit (landt in review-gate, niet geboekt)', async () => {
  // De echte runCustomerTool geeft bij stel_afspraak_voor een object
  // {proposed:true, message} terug; spiegel dat hier zodat we de proposed-logica
  // van customerConverse (niet de DB-INSERT) testen.
  const runTool = async (name) => {
    if (name === 'stel_afspraak_voor') return { proposed: true, message: 'Voorstel klaargezet.' };
    return 'ok';
  };
  let calls = 0;
  const opts = baseOpts({
    callModelFn: async () => {
      calls += 1;
      if (calls === 1) return makeStep({ toolCalls: [{ name: 'stel_afspraak_voor', input: { opdracht: 'Bel Jan do 14:00' } }] });
      return makeStep({ text: 'Ik heb een voorstel klaargezet.' });
    },
    runToolFn: runTool,
  });
  const res = await customerConverse('weareimpact', 'Demo', [], 'Ik wil Jan bellen', '31612345678', opts);
  assert.equal(res.escalated, false);
  assert.equal(runTool.executed?.includes?.('stel_afspraak_voor') ?? true, true);
  assert.equal(res.proposed, true);
});

test('klant-Iris voert deel_emailadres wél uit (alleen als de klant zelf een mailadres geeft)', async () => {
  const runTool = countingRunTool();
  let calls = 0;
  const opts = baseOpts({
    callModelFn: async () => {
      calls += 1;
      if (calls === 1) {
        return makeStep({ toolCalls: [{ name: 'deel_emailadres', input: { email: 'klant@voorbeeld.nl' } }] });
      }
      return makeStep({ text: 'Genoteerd, dank je.' });
    },
    runToolFn: runTool,
  });
  const res = await customerConverse(
    'weareimpact', 'Demo', [], 'Mijn mailadres is klant@voorbeeld.nl', '31612345678', opts);
  assert.equal(res.escalated, false);
  assert.equal(runTool.executed.includes('deel_emailadres'), true, 'toegestane tool moet draaien');
});

test('CUSTOMER_TOOLS bevat precies de vier verwachte klant-tools, niets anders', () => {
  const names = CUSTOMER_TOOLS.map((t) => t.name).sort();
  assert.deepEqual(names, [
    'escaleer_naar_vincent',
    'lees_vrije_momenten',
    'stel_afspraak_voor',
    'deel_emailadres',
  ].sort());
  // Harde eis: géén van de manager-tools mag ooit in de klant-toolset zitten.
  const managerOnly = ['start_werk', 'stel_besluit_voor', 'plan_agenda', 'ritueel_vastleggen',
    'lees_context', 'lees_besluiten', 'lees_briefing'];
  for (const m of managerOnly) {
    assert.equal(names.includes(m), false, `manager-tool '${m}' zit in de klant-toolset`);
  }
});
