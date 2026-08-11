// Iris-onboarding stap 3 — per-klant Google/Microsoft OAuth. Dit is de
// publiek bereikbare tegenhanger van wat vroeger `backend/domains/onboarding/
// oauth_google.py`/`oauth_microsoft.py` zelf deden: de lokale AgentOS-instance
// hoeft geen open poort te hebben (zie CLAUDE.md 14, Bridge doet alleen
// uitgaande sync) — Google/Microsoft redirecten hierheen, en het resultaat
// reist verder als een Bridge-`decision` (action='oauth_token_relay'), niet
// via een nieuw kanaal. `backend/domains/bridge/actions.py:_cmd_oauth_token_relay`
// pakt 'm op bij de eerstvolgende sync en schrijft 'm in de lokale
// `oauth_accounts`-tabel (zelfde opslagformaat als de oude lokale flow).
//
//   GET /api/oauth?provider=google|microsoft&op=authorize&site=<site_id>
//   GET /api/oauth?provider=google|microsoft&op=callback&code=...&state=...
//
// Client-secrets staan hier als Vercel-omgevingsvariabelen (niet meer nodig
// in de lokale .env voor dit doel): GOOGLE_OAUTH_CLIENT_ID/_SECRET,
// OUTLOOK_CLIENT_ID/_SECRET/OUTLOOK_TENANT_ID.
import crypto from 'node:crypto';
import { sql, tenantFromHost, requireSession } from './_lib.js';

const GOOGLE_AUTH = 'https://accounts.google.com/o/oauth2/v2/auth';
const GOOGLE_TOKEN = 'https://oauth2.googleapis.com/token';
const GOOGLE_USERINFO = 'https://www.googleapis.com/oauth2/v2/userinfo';
// Vol i.p.v. .readonly: de lokale GSC-sync gebruikt dezelfde credential ook
// om te schrijven (submit_sitemap), niet alleen te lezen — zie de Python-
// tegenhanger die hier is uitgefaseerd. Calendar-scope altijd mee aanvragen
// is een kleine UX-kost (één extra regel op het consentscherm) voor minder
// complexiteit hier: Vercel kent de CALENDAR_BACKEND-instelling van een
// specifieke klant-instance niet.
const GOOGLE_SCOPES = [
  'https://www.googleapis.com/auth/webmasters',
  'https://www.googleapis.com/auth/calendar',
  'openid', 'email',
];

// Moet gelijk blijven aan backend/domains/outlook/service.py:GRAPH_SCOPES,
// plus offline_access — MSAL voegt dat impliciet toe, een kale HTTP-code-
// exchange (hier, geen MSAL in Node) niet.
const MS_SCOPES = [
  'https://graph.microsoft.com/Mail.Read',
  'https://graph.microsoft.com/Mail.ReadWrite',
  'https://graph.microsoft.com/Mail.Send',
  'https://graph.microsoft.com/User.Read',
  'https://graph.microsoft.com/Calendars.ReadWrite',
  'offline_access',
];

function redirectUri(req) {
  return `https://${req.headers.host}/api/oauth?provider=PROVIDER&op=callback`;
}

function wizardUrl(req, params) {
  const q = new URLSearchParams(params);
  return `https://${req.headers.host}/#onboarding?${q.toString()}`;
}

function redirect(res, url) {
  res.writeHead(302, { Location: url });
  res.end();
}

export default async function handler(req, res) {
  const provider = String((req.query && req.query.provider) || '');
  const op = String((req.query && req.query.op) || '');
  if (provider !== 'google' && provider !== 'microsoft') {
    res.status(400).end(`Onbekende provider '${provider}'`);
    return;
  }
  try {
    if (op === 'authorize') return await authorize(req, res, provider);
    if (op === 'callback') return await callback(req, res, provider);
    res.status(400).end(`Onbekende op '${op}'`);
  } catch (e) {
    console.error('oauth error', e);
    res.status(500).end(`Onverwachte fout: ${String(e).slice(0, 300)}`);
  }
}

async function authorize(req, res, provider) {
  // Alleen een ingelogde gebruiker van déze tenant mag een koppelpoging
  // starten — zonder deze check kan iedereen die de URL raadt een eigen
  // Google/Microsoft-account aan een willekeurige site_id vastknopen.
  const tenant = await requireSession(req, res);
  if (!tenant) return; // requireSession stuurde al een 401

  const siteId = String((req.query && req.query.site) || '').trim();
  if (!siteId) return void res.status(400).end('Geen site meegegeven');

  const clientId = provider === 'google'
    ? process.env.GOOGLE_OAUTH_CLIENT_ID
    : process.env.OUTLOOK_CLIENT_ID;
  if (!clientId) {
    return void res.status(409).end(
      `${provider === 'google' ? 'GOOGLE_OAUTH_CLIENT_ID' : 'OUTLOOK_CLIENT_ID'} ontbreekt in de Vercel-omgevingsvariabelen.`
    );
  }

  const state = crypto.randomBytes(24).toString('hex');
  await sql`
    INSERT INTO oauth_state (state, tenant, site_id, provider)
    VALUES (${state}, ${tenant}, ${siteId}, ${provider})`;
  // Oude, nooit-gebruikte pogingen (afgebroken consentschermen) niet laten
  // opstapelen — vergt geen aparte cron, dit endpoint wordt vaak genoeg geraakt.
  await sql`DELETE FROM oauth_state WHERE created_at < now() - interval '30 minutes'`;

  const redirectUriValue = redirectUri(req).replace('PROVIDER', provider);
  const url = provider === 'google'
    ? `${GOOGLE_AUTH}?${new URLSearchParams({
        client_id: clientId,
        redirect_uri: redirectUriValue,
        response_type: 'code',
        scope: GOOGLE_SCOPES.join(' '),
        access_type: 'offline',
        // Zonder consent-prompt levert een tweede koppelpoging voor hetzelfde
        // account géén refresh_token — Google geeft die alleen bij de eerste klik.
        prompt: 'consent',
        state,
      })}`
    : `https://login.microsoftonline.com/${process.env.OUTLOOK_TENANT_ID || 'common'}/oauth2/v2.0/authorize?${new URLSearchParams({
        client_id: clientId,
        redirect_uri: redirectUriValue,
        response_type: 'code',
        scope: MS_SCOPES.join(' '),
        prompt: 'select_account',
        state,
      })}`;
  redirect(res, url);
}

async function callback(req, res, provider) {
  const code = String((req.query && req.query.code) || '');
  const state = String((req.query && req.query.state) || '');
  const authError = String((req.query && req.query.error) || '');

  const rows = state
    ? await sql`DELETE FROM oauth_state WHERE state = ${state} AND provider = ${provider} RETURNING tenant, site_id`
    : [];
  if (!rows.length) {
    // Geen (of verlopen/al gebruikte) state — nergens een site_id om naar
    // terug te sturen, dus een kale foutpagina i.p.v. een gok-redirect.
    res.status(400).end('Ongeldige of verlopen koppelpoging. Probeer opnieuw vanuit de wizard.');
    return;
  }
  const { site_id: siteId, tenant } = rows[0];

  if (authError) {
    return redirect(res, wizardUrl(req, { site: siteId, step: '3', connect_error: authError }));
  }

  try {
    const { accessToken, refreshToken, expiresIn, email } = await exchangeCode(req, provider, code);
    if (!refreshToken) {
      throw new Error(
        `${provider === 'google' ? 'Google' : 'Microsoft'} gaf geen refresh-token terug — dit account was `
        + 'mogelijk al eerder gekoppeld. Trek de bestaande koppeling in en probeer opnieuw.'
      );
    }
    const scopes = provider === 'google' ? GOOGLE_SCOPES : MS_SCOPES;
    const credentials = {
      access_token: accessToken,
      refresh_token: refreshToken,
      expiry: new Date(Date.now() + (expiresIn || 3600) * 1000).toISOString(),
      scopes,
    };
    const key = `oauth:${provider}:${siteId}`;
    await sql`
      INSERT INTO decisions (tenant, item_key, item_kind, item_id, action, payload)
      VALUES (${tenant}, ${key}, 'command', 'oauth_token_relay', 'oauth_token_relay',
              ${JSON.stringify({ site_id: siteId, provider, account_email: email, credentials, scopes })}::jsonb)
      ON CONFLICT (tenant, item_key) WHERE status = 'pending' DO NOTHING`;
    redirect(res, wizardUrl(req, { site: siteId, step: '3', connecting: provider }));
  } catch (e) {
    console.error('oauth callback exchange failed', e);
    redirect(res, wizardUrl(req, { site: siteId, step: '3', connect_error: String(e.message || e).slice(0, 200) }));
  }
}

async function exchangeCode(req, provider, code) {
  const redirectUriValue = redirectUri(req).replace('PROVIDER', provider);
  if (provider === 'google') {
    const resp = await fetch(GOOGLE_TOKEN, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: process.env.GOOGLE_OAUTH_CLIENT_ID,
        client_secret: process.env.GOOGLE_OAUTH_CLIENT_SECRET,
        redirect_uri: redirectUriValue,
        grant_type: 'authorization_code',
      }),
    });
    const payload = await resp.json();
    if (!resp.ok || !payload.access_token) {
      throw new Error(`Google-koppeling mislukt: ${payload.error_description || payload.error || resp.statusText}`);
    }
    const userinfo = await fetch(GOOGLE_USERINFO, {
      headers: { Authorization: `Bearer ${payload.access_token}` },
    }).then((r) => r.json()).catch(() => ({}));
    return {
      accessToken: payload.access_token,
      refreshToken: payload.refresh_token,
      expiresIn: payload.expires_in,
      email: userinfo.email || '',
    };
  }

  const resp = await fetch(
    `https://login.microsoftonline.com/${process.env.OUTLOOK_TENANT_ID || 'common'}/oauth2/v2.0/token`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: process.env.OUTLOOK_CLIENT_ID,
        client_secret: process.env.OUTLOOK_CLIENT_SECRET,
        redirect_uri: redirectUriValue,
        grant_type: 'authorization_code',
        scope: MS_SCOPES.join(' '),
      }),
    }
  );
  const payload = await resp.json();
  if (!resp.ok || !payload.access_token) {
    throw new Error(`Microsoft-koppeling mislukt: ${payload.error_description || payload.error || resp.statusText}`);
  }
  const me = await fetch('https://graph.microsoft.com/v1.0/me', {
    headers: { Authorization: `Bearer ${payload.access_token}` },
  }).then((r) => r.json()).catch(() => ({}));
  return {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresIn: payload.expires_in,
    email: me.mail || me.userPrincipalName || '',
  };
}
