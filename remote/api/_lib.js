// Gedeelde helpers voor de Vercel-functies. Bestanden met een underscore-prefix
// in api/ worden door Vercel NIET als route ontsloten.
import crypto from 'node:crypto';
import { neon } from '@neondatabase/serverless';

export const sql = neon(process.env.DATABASE_URL);

export function json(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(body));
}

// ── Bridge-auth: bearer-token, alleen voor de lokale AgentOS-machine ────────
export function requireBearer(req, res) {
  const token = process.env.BRIDGE_TOKEN || '';
  const got = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!token || !timingSafeEq(got, token)) {
    json(res, 401, { error: 'unauthorized' });
    return false;
  }
  return true;
}

// ── UI-auth: wachtwoord → stateless HMAC-cookie (single-user) ───────────────
function sessionValue() {
  const key = process.env.BRIDGE_TOKEN || 'no-key';
  const pw = process.env.APP_PASSWORD || '';
  return crypto.createHmac('sha256', key).update(`session:${pw}`).digest('hex');
}

export function checkPassword(pw) {
  const want = process.env.APP_PASSWORD || '';
  return want.length > 0 && timingSafeEq(pw || '', want);
}

export function sessionCookie() {
  return `agentos_session=${sessionValue()}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${30 * 86400}`;
}

export function clearCookie() {
  return 'agentos_session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0';
}

export function requireSession(req, res) {
  const cookies = Object.fromEntries(
    (req.headers.cookie || '').split(';').map((c) => {
      const i = c.indexOf('=');
      return [c.slice(0, i).trim(), c.slice(i + 1).trim()];
    })
  );
  if (!timingSafeEq(cookies.agentos_session || '', sessionValue())) {
    json(res, 401, { error: 'login_required' });
    return false;
  }
  return true;
}

function timingSafeEq(a, b) {
  const ba = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}
