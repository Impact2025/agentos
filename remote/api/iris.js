// Cloud-Iris — chatten met Iris terwijl de pc uitstaat. Context komt uit Neon
// (laatste briefing + funnel + de open besluiten); het LLM loopt via dezelfde
// OpenModel-gateway (Anthropic Messages-formaat) als lokaal. Iris kan hier
// NIETS uitvoeren — besluiten lopen altijd via de decide-knoppen.
import { sql, json, requireSession } from './_lib.js';

export const config = { maxDuration: 60 };

const MAX_TURNS = 12;

export default async function handler(req, res) {
  if (req.method !== 'POST') return json(res, 405, { error: 'POST only' });
  if (!requireSession(req, res)) return;

  const apiKey = process.env.OPENMODEL_API_KEY || '';
  if (!apiKey) {
    return json(res, 400, { error: 'OPENMODEL_API_KEY niet gezet in de Vercel-env — cloud-Iris staat uit.' });
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
    const briefingRow = (await sql`
      SELECT payload, generated_at FROM briefings ORDER BY generated_at DESC LIMIT 1`)[0];
    const items = await sql`
      SELECT dismiss_kind, title, project, summary FROM sync_items
      WHERE status = 'active' ORDER BY updated_at DESC LIMIT 40`;

    const p = (briefingRow && briefingRow.payload) || {};
    const system = [
      'Je bent Iris, de SEO-expert-manager van Agent OS (het lokale AI-dashboard van Vincent).',
      'Vincent spreekt je nu onderweg via Iris Remote; zijn machine staat mogelijk uit.',
      'Je context is de laatst gesynchroniseerde snapshot — zeg het erbij als data verouderd kan zijn.',
      'BELANGRIJK: je kunt hier niets uitvoeren, publiceren of versturen. Wil Vincent iets laten',
      'gebeuren, verwijs dan naar de knoppen in het Actiecentrum-tabblad (die besluiten voert',
      'AgentOS uit bij de volgende sync, achter de bestaande review-gates).',
      'Antwoord in het Nederlands, beknopt en concreet, zoals een scherpe manager.',
      '',
      `## Laatste briefing (${briefingRow ? String(briefingRow.generated_at).slice(0, 16) : 'geen'})`,
      p.iris ? `Datum ${p.iris.date || '?'} — cijfers: ${JSON.stringify(p.iris.grades || {})}` : 'Geen briefing beschikbaar.',
      p.iris && p.iris.markdown ? p.iris.markdown.slice(0, 6000) : '',
      '',
      '## Funnel',
      JSON.stringify(p.funnel || {}),
      '',
      '## Open besluiten in het Actiecentrum',
      items.length
        ? items.map((i) => `- [${i.dismiss_kind}] ${i.title} (${i.project || '?'}): ${(i.summary || '').slice(0, 140)}`).join('\n')
        : 'Niets open — alles is afgehandeld.',
    ].join('\n');

    const base = (process.env.OPENMODEL_BASE_URL || 'https://api.openmodel.ai').replace(/\/$/, '');
    const r = await fetch(`${base}/v1/messages`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: process.env.OPENMODEL_MODEL || 'deepseek-v4-flash',
        max_tokens: 1200,
        system,
        messages,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const detail = (data.error && data.error.message) || `HTTP ${r.status}`;
      return json(res, 502, { error: `Gateway: ${String(detail).slice(0, 200)}` });
    }
    const reply = (data.content || []).map((c) => c.text || '').join('').trim();
    if (!reply) return json(res, 502, { error: 'Leeg antwoord van de gateway — probeer opnieuw.' });
    return json(res, 200, { reply });
  } catch (e) {
    console.error('iris error', e);
    return json(res, 500, { error: String(e).slice(0, 300) });
  }
}
