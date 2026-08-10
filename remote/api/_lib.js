// Gedeelde helpers voor de Vercel-functies. Bestanden met een underscore-prefix
// in api/ worden door Vercel NIET als route ontsloten.
import crypto from 'node:crypto';
import { neon } from '@neondatabase/serverless';

export const sql = neon(process.env.DATABASE_URL);

export function json(res, status, body) {
  res.status(status).setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(body));
}

// ── Multi-tenant: welke klant hoort bij dit verzoek? ────────────────────────
// Eén Vercel-deploy + één Neon-database bedient meerdere klanten (WeAreImpact,
// Nicole, ...), elk met een eigen subdomein. BASE_DOMAIN is het domein waarop
// die subdomeinen hangen (bv. "domein.nl" → nicole.domein.nl = tenant "nicole").
// Zonder BASE_DOMAIN (de huidige *.vercel.app-situatie, of lokale dev) valt
// alles terug op DEFAULT_TENANT — dat houdt de bestaande installatie werkend
// zonder dat er meteen een custom domain hoeft te staan.
const BASE_DOMAIN = (process.env.BASE_DOMAIN || '').toLowerCase().replace(/^\./, '');
const DEFAULT_TENANT = process.env.DEFAULT_TENANT || 'weareimpact';

export function tenantFromHost(req) {
  const host = String(req.headers.host || '').toLowerCase().split(':')[0];
  if (BASE_DOMAIN && host.endsWith(`.${BASE_DOMAIN}`)) {
    const sub = host.slice(0, -(BASE_DOMAIN.length + 1));
    // Eén niveau: "nicole.domein.nl" ja, "a.b.domein.nl" nee — dat laatste is
    // geen klant-subdomein maar iets anders (of een misconfiguratie), en
    // "geen tenant kunnen bepalen" moet nooit stil op de default uitkomen.
    if (sub && !sub.includes('.')) return sub;
  }
  return DEFAULT_TENANT;
}

function hashToken(t) {
  return crypto.createHash('sha256').update(String(t)).digest('hex');
}

// ── Bridge-auth: bearer-token → tenant, alleen voor lokale AgentOS-machines ─
// Vóór multi-tenant was dit één globale BRIDGE_TOKEN. Nu draagt élke lokale
// instance zijn eigen token (in zijn eigen .env), en zoekt de server via de
// hash op welke tenant daarbij hoort — dezelfde token kan dus nooit twee
// klanten laten schrijven, en de Python-kant hoeft niets over tenants te weten.
export async function resolveBridgeTenant(req, res) {
  const got = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!got) { json(res, 401, { error: 'unauthorized' }); return null; }
  const rows = await sql`SELECT slug FROM tenants WHERE token_hash = ${hashToken(got)}`;
  if (!rows.length) { json(res, 401, { error: 'unauthorized' }); return null; }
  return rows[0].slug;
}

// ── Wachtwoord ─────────────────────────────────────────────────────────────
// Achter deze ene deur zitten "publiceren" en "mail versturen". Een zwak
// wachtwoord is daarmee het zwakste punt van het hele systeem, en geen enkele
// rem hierna maakt dat goed. Opgeslagen als scrypt (traag, gezouten) — niet
// als sha256, want een wachtwoord is lage-entropie mensentekst en dát is
// precies waar een snelle hash tegen niets beschermt bij een databaselek.
export const MIN_PASSWORD_LENGTH = 16;

export function weakPassword(pw) {
  return !pw || String(pw).length < MIN_PASSWORD_LENGTH;
}

export function hashPassword(pw) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(String(pw), salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(pw, stored) {
  const [salt, hash] = String(stored || '').split(':');
  if (!salt || !hash) return false;
  const check = crypto.scryptSync(String(pw || ''), salt, 64);
  const storedBuf = Buffer.from(hash, 'hex');
  if (check.length !== storedBuf.length) return false;
  return crypto.timingSafeEqual(check, storedBuf);
}

// Geeft de reden waarom inloggen voor deze tenant uitstaat, of null als het
// gewoon kan. Een onbekende tenant is een serverfout (geen custom domain
// gekoppeld, of de klant nog niet geprovisioneerd), geen "fout wachtwoord" —
// anders staat de deur potentieel open zonder dat iemand het ziet.
export async function tenantConfigError(tenant) {
  const rows = await sql`SELECT 1 FROM tenants WHERE slug = ${tenant}`;
  if (!rows.length) return `Onbekende tenant '${tenant}' — nog niet geprovisioneerd (zie scripts/add-tenant.mjs).`;
  return null;
}

export async function checkPassword(tenant, pw) {
  const rows = await sql`SELECT password_hash FROM tenants WHERE slug = ${tenant}`;
  if (!rows.length) return false;
  return verifyPassword(pw, rows[0].password_hash);
}

// ── Brute-force-rem ────────────────────────────────────────────────────────
// Vijf misslagen gratis, daarna verdubbelt de wachttijd per poging tot een uur.
// Een aanvaller schiet zichzelf zo binnen een minuut buitenspel; Vincent die
// zich vertypt merkt er niets van. Bewust niet tenant-gescoped: het IP is de
// aanvaller, niet de klant, dus één teller per IP over alle tenants heen.
const FREE_ATTEMPTS = 5;
const MAX_LOCK_MINUTES = 60;

function ipHash(req) {
  const fwd = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim();
  const ip = fwd || req.socket?.remoteAddress || 'onbekend';
  const pepper = process.env.IP_PEPPER || 'agentos-remote-default-pepper';
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

// ── Sessies: intrekbaar, in de database, tenant-gebonden ────────────────────
const SESSION_DAYS = 30;
const COOKIE = 'agentos_session';

function deviceLabel(req) {
  const ua = String(req.headers['user-agent'] || '');
  if (/iPhone|iPad/i.test(ua)) return 'iPhone/iPad';
  if (/Android/i.test(ua)) return 'Android';
  if (/Macintosh/i.test(ua)) return 'Mac';
  if (/Windows/i.test(ua)) return 'Windows';
  return 'Onbekend apparaat';
}

export async function startSession(req, tenant) {
  const token = crypto.randomBytes(32).toString('hex');
  await sql`
    INSERT INTO sessions (token_hash, tenant, label, expires_at)
    VALUES (${hashToken(token)}, ${tenant}, ${deviceLabel(req)},
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

// Geeft {token_hash, tenant, expires_at}, of null. Verlengt schuivend: wie de
// app blijft gebruiken wordt niet elke maand uitgelogd, wie hem laat liggen wél.
export async function loadSession(req) {
  const token = readCookie(req);
  if (!token) return null;
  const rows = await sql`
    SELECT token_hash, tenant, expires_at FROM sessions
    WHERE token_hash = ${hashToken(token)} AND expires_at > now()`;
  if (!rows.length) return null;
  await sql`
    UPDATE sessions
    SET last_seen = now(),
        expires_at = now() + ${`${SESSION_DAYS} days`}::interval
    WHERE token_hash = ${rows[0].token_hash} AND last_seen < now() - interval '1 hour'`;
  return rows[0];
}

// Geeft de tenant-slug terug bij een geldige sessie, anders null (en stuurt
// dan zelf de 401). De sessie moet bij de tenant van DIT verzoek horen — een
// cookie die per ongeluk op een ander subdomein meegaat mag daar nooit gelden.
export async function requireSession(req, res) {
  const session = await loadSession(req);
  const wanted = tenantFromHost(req);
  if (!session || session.tenant !== wanted) {
    json(res, 401, { error: 'login_required' });
    return null;
  }
  return session.tenant;
}

export async function endSession(req) {
  const token = readCookie(req);
  if (token) await sql`DELETE FROM sessions WHERE token_hash = ${hashToken(token)}`;
}

export async function endAllSessions(tenant) {
  await sql`DELETE FROM sessions WHERE tenant = ${tenant}`;
}

export async function listSessions(req, tenant) {
  const current = hashToken(readCookie(req));
  const rows = await sql`
    SELECT token_hash, label, created_at, last_seen, expires_at
    FROM sessions WHERE tenant = ${tenant} AND expires_at > now() ORDER BY last_seen DESC`;
  return rows.map((r) => ({
    label: r.label,
    created_at: r.created_at,
    last_seen: r.last_seen,
    current: r.token_hash === current,
  }));
}
