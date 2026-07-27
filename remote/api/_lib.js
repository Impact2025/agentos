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

// ── Wachtwoord ─────────────────────────────────────────────────────────────
// Achter deze ene deur zitten "publiceren" en "mail versturen". Een korte
// APP_PASSWORD is daarmee het zwakste punt van het hele systeem, en geen enkele
// rem hierna maakt dat goed. Dus: weigeren te starten in plaats van stilzwijgend
// een raadbaar wachtwoord accepteren.
const MIN_PASSWORD_LENGTH = 16;

export function passwordConfigError() {
  const pw = process.env.APP_PASSWORD || '';
  if (!pw) return 'APP_PASSWORD is niet gezet — inloggen is uitgeschakeld.';
  if (pw.length < MIN_PASSWORD_LENGTH) {
    return `APP_PASSWORD is te kort (${pw.length} tekens, minimaal ${MIN_PASSWORD_LENGTH}). `
      + 'Deze app staat publiek op internet en geeft toegang tot publiceren en mail versturen.';
  }
  return null;
}

export function checkPassword(pw) {
  if (passwordConfigError()) return false;
  return timingSafeEq(pw || '', process.env.APP_PASSWORD);
}

// ── Brute-force-rem ────────────────────────────────────────────────────────
// Vijf misslagen gratis, daarna verdubbelt de wachttijd per poging tot een uur.
// Een aanvaller schiet zichzelf zo binnen een minuut buitenspel; Vincent die
// zich vertypt merkt er niets van.
const FREE_ATTEMPTS = 5;
const MAX_LOCK_MINUTES = 60;

function ipHash(req) {
  const fwd = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim();
  const ip = fwd || req.socket?.remoteAddress || 'onbekend';
  const pepper = process.env.BRIDGE_TOKEN || 'no-key';
  return crypto.createHmac('sha256', pepper).update(ip).digest('hex');
}

// Geeft het aantal seconden dat dit IP nog moet wachten, of 0.
export async function loginLockSeconds(req) {
  const rows = await sql`
    SELECT EXTRACT(EPOCH FROM (locked_until - now()))::int AS wait
    FROM login_attempts WHERE ip_hash = ${ipHash(req)} AND locked_until > now()`;
  return rows[0]?.wait > 0 ? rows[0].wait : 0;
}

export async function noteLoginFailure(req) {
  // De lockout wordt in SQL berekend zodat twee gelijktijdige pogingen niet
  // allebei van dezelfde oude telling uitgaan.
  const rows = await sql`
    INSERT INTO login_attempts (ip_hash, fails, locked_until)
    VALUES (${ipHash(req)}, 1, NULL)
    ON CONFLICT (ip_hash) DO UPDATE SET
      fails = login_attempts.fails + 1,
      last_fail = now(),
      locked_until = CASE
        WHEN login_attempts.fails + 1 > ${FREE_ATTEMPTS}
        -- De exponent wordt afgetopt vóór het machtsverheffen: bij honderd
        -- pogingen is 2^95 geen grote wachttijd maar een integer-overflow.
        THEN now() + (least(
               power(2, least(login_attempts.fails + 1 - ${FREE_ATTEMPTS}, 12))::int,
               ${MAX_LOCK_MINUTES}) || ' minutes')::interval
        ELSE NULL END
    RETURNING fails, EXTRACT(EPOCH FROM (locked_until - now()))::int AS wait`;
  const row = rows[0] || {};
  return { fails: row.fails || 1, wait: row.wait > 0 ? row.wait : 0 };
}

export async function clearLoginFailures(req) {
  await sql`DELETE FROM login_attempts WHERE ip_hash = ${ipHash(req)}`;
}

// ── Sessies: intrekbaar, in de database ────────────────────────────────────
const SESSION_DAYS = 30;
const COOKIE = 'agentos_session';

function hashToken(t) {
  return crypto.createHash('sha256').update(String(t)).digest('hex');
}

function deviceLabel(req) {
  const ua = String(req.headers['user-agent'] || '');
  if (/iPhone|iPad/i.test(ua)) return 'iPhone/iPad';
  if (/Android/i.test(ua)) return 'Android';
  if (/Macintosh/i.test(ua)) return 'Mac';
  if (/Windows/i.test(ua)) return 'Windows';
  return 'Onbekend apparaat';
}

export async function startSession(req) {
  const token = crypto.randomBytes(32).toString('hex');
  await sql`
    INSERT INTO sessions (token_hash, label, expires_at)
    VALUES (${hashToken(token)}, ${deviceLabel(req)},
            now() + ${`${SESSION_DAYS} days`}::interval)`;
  // Opruimen kost hier niets en houdt de tabel klein zonder aparte cron-job.
  await sql`DELETE FROM sessions WHERE expires_at < now()`;
  return cookieFor(token);
}

function cookieFor(token) {
  return `${COOKIE}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_DAYS * 86400}`;
}

export function clearCookie() {
  return `${COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`;
}

function readCookie(req) {
  const raw = req.headers.cookie || '';
  for (const part of raw.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === COOKIE) return part.slice(i + 1).trim();
  }
  return '';
}

// Geeft de sessie terug, of null. Verlengt schuivend: wie de app blijft
// gebruiken wordt niet elke maand uitgelogd, wie hem laat liggen wél.
export async function loadSession(req) {
  const token = readCookie(req);
  if (!token) return null;
  const rows = await sql`
    SELECT token_hash, expires_at FROM sessions
    WHERE token_hash = ${hashToken(token)} AND expires_at > now()`;
  if (!rows.length) return null;
  await sql`
    UPDATE sessions
    SET last_seen = now(),
        expires_at = now() + ${`${SESSION_DAYS} days`}::interval
    WHERE token_hash = ${rows[0].token_hash} AND last_seen < now() - interval '1 hour'`;
  return rows[0];
}

export async function requireSession(req, res) {
  const session = await loadSession(req);
  if (!session) {
    json(res, 401, { error: 'login_required' });
    return false;
  }
  return true;
}

export async function endSession(req) {
  const token = readCookie(req);
  if (token) await sql`DELETE FROM sessions WHERE token_hash = ${hashToken(token)}`;
}

export async function endAllSessions() {
  await sql`DELETE FROM sessions`;
}

export async function listSessions(req) {
  const current = hashToken(readCookie(req));
  const rows = await sql`
    SELECT token_hash, label, created_at, last_seen, expires_at
    FROM sessions WHERE expires_at > now() ORDER BY last_seen DESC`;
  return rows.map((r) => ({
    label: r.label,
    created_at: r.created_at,
    last_seen: r.last_seen,
    current: r.token_hash === current,
  }));
}

function timingSafeEq(a, b) {
  const ba = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}
