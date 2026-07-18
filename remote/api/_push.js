// Web-push-helper: stuur een melding naar alle geregistreerde apparaten.
// Zonder VAPID-keys in de env doet dit stil niets (meldingen zijn optioneel).
// Verlopen abonnementen (404/410 van de push-dienst) worden opgeruimd.
import webpush from 'web-push';
import { sql } from './_lib.js';

export async function pushToAll(payload) {
  const pub = process.env.VAPID_PUBLIC_KEY || '';
  const priv = process.env.VAPID_PRIVATE_KEY || '';
  if (!pub || !priv) return 0;
  webpush.setVapidDetails(
    process.env.VAPID_SUBJECT || 'mailto:v.munster@weareimpact.nl', pub, priv);

  const subs = await sql`SELECT id, endpoint, p256dh, auth FROM push_subscriptions`;
  let sent = 0;
  for (const s of subs) {
    try {
      await webpush.sendNotification(
        { endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } },
        JSON.stringify(payload),
        { TTL: 3600 }
      );
      sent += 1;
    } catch (e) {
      if (e.statusCode === 404 || e.statusCode === 410) {
        await sql`DELETE FROM push_subscriptions WHERE id = ${s.id}`;
      } else {
        console.error('push failed', e.statusCode || e.message);
      }
    }
  }
  return sent;
}
