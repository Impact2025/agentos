// Meta Cloud API — versturen. Gedeeld door whatsapp.js (de webhook, klant-
// antwoorden en houd-vast-berichten) en ui.js (Vincent die een escalatie
// vanuit Iris Remote beantwoordt) — één plek die weet hoe een WhatsApp-
// bericht de deur uitgaat, met hetzelfde gedeelde WHATSAPP_TOKEN.
const GRAPH_VERSION = 'v21.0';
const SEND_CHUNK_LIMIT = 3800; // WhatsApp-limiet is 4096 tekens per bericht

function splitForWhatsapp(text) {
  const parts = [];
  let rest = String(text || '').trim();
  while (rest.length > SEND_CHUNK_LIMIT) {
    let cut = rest.lastIndexOf('\n', SEND_CHUNK_LIMIT);
    if (cut < SEND_CHUNK_LIMIT * 0.5) cut = rest.lastIndexOf(' ', SEND_CHUNK_LIMIT);
    if (cut < SEND_CHUNK_LIMIT * 0.5) cut = SEND_CHUNK_LIMIT;
    parts.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) parts.push(rest);
  return parts.length ? parts : [''];
}

async function graphPost(phoneNumberId, body) {
  const r = await fetch(`https://graph.facebook.com/${GRAPH_VERSION}/${phoneNumberId}/messages`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${process.env.WHATSAPP_TOKEN}`,
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    console.error('whatsapp graph-call mislukt', r.status, JSON.stringify(data).slice(0, 400));
  }
  return r.ok;
}

export async function sendText(phoneNumberId, to, text) {
  let allOk = true;
  for (const chunk of splitForWhatsapp(text)) {
    const ok = await graphPost(phoneNumberId, {
      messaging_product: 'whatsapp', to, type: 'text', text: { body: chunk, preview_url: false },
    });
    allOk = allOk && ok;
  }
  return allOk;
}

export function markRead(phoneNumberId, messageId) {
  // Fire-and-forget: een mislukt leesbevestigingetje mag het antwoord niet ophouden.
  graphPost(phoneNumberId, { messaging_product: 'whatsapp', status: 'read', message_id: messageId })
    .catch(() => {});
}
