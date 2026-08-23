// Media-download voor WhatsApp-afbeeldingen (Meta Cloud API, twee stappen:
// media-id -> tijdelijke URL -> bytes, beide keren met hetzelfde WHATSAPP_TOKEN
// geauthenticeerd). Alleen gebruikt door het manager-kanaal in whatsapp.js —
// klant-Iris krijgt hier bewust geen toegang toe (zie whatsapp.js).
const GRAPH_VERSION = 'v21.0';

// Ruim boven wat een foto van een rooster ooit weegt (WhatsApp comprimeert al
// zelf); vooral een plafond tegen een per ongeluk meegestuurde RAW-foto of
// video die als 'image' binnenkomt.
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export async function downloadWhatsappImage(mediaId) {
  const token = process.env.WHATSAPP_TOKEN;
  if (!token) throw new Error('WHATSAPP_TOKEN ontbreekt');

  const metaRes = await fetch(`https://graph.facebook.com/${GRAPH_VERSION}/${mediaId}`, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (!metaRes.ok) throw new Error(`media-metadata HTTP ${metaRes.status}`);
  const meta = await metaRes.json().catch(() => ({}));
  if (!meta.url) throw new Error('geen media-url in Meta-antwoord');
  if (!/^image\//.test(meta.mime_type || '')) throw new Error(`onverwacht mime-type '${meta.mime_type}'`);
  if (meta.file_size && meta.file_size > MAX_IMAGE_BYTES) throw new Error('afbeelding te groot');

  const fileRes = await fetch(meta.url, { headers: { authorization: `Bearer ${token}` } });
  if (!fileRes.ok) throw new Error(`media-download HTTP ${fileRes.status}`);
  const buf = Buffer.from(await fileRes.arrayBuffer());
  if (buf.length > MAX_IMAGE_BYTES) throw new Error('afbeelding te groot');

  return { mediaType: meta.mime_type, base64: buf.toString('base64') };
}
