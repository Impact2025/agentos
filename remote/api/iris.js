// Cloud-Iris — chat-endpoint voor de app (Impact OS Remote in de browser/PWA).
// De tool-lus zelf (TOOLS, runTool, de provider-laag, de systeemprompt) staat
// in _iris_core.js, gedeeld met whatsapp.js — zie de uitleg daar.
import { json, requireSession } from './_lib.js';
import { MAX_TURNS, converse } from './_iris_core.js';

export const config = { maxDuration: 60 };

export default async function handler(req, res) {
  if (req.method !== 'POST') return json(res, 405, { error: 'POST only' });
  const tenant = await requireSession(req, res);
  if (!tenant) return;

  // Alleen geldige, recente beurten doorlaten — de client is geen bron van waarheid.
  const messages = ((req.body || {}).messages || [])
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .slice(-MAX_TURNS)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 4000) }));
  if (!messages.length || messages[messages.length - 1].role !== 'user') {
    return json(res, 400, { error: 'Geen vraag ontvangen' });
  }

  try {
    const result = await converse(tenant, messages, 'app');
    return json(res, 200, result);
  } catch (e) {
    console.error('iris error', e);
    const status = /Geen LLM-key/.test(String(e.message)) ? 400 : 502;
    return json(res, status, { error: String(e.message || e).slice(0, 300) });
  }
}
