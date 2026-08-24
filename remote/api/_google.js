// Live agenda + GSC-trend, rechtstreeks vanuit Vercel — geen ImpactOS nodig.
//
// Spiegelt (bewust minimaal, alleen het leesgedeelte):
//   backend/domains/calendar/service_google.py:get_events_range()
//   backend/domains/bridge/context.py:_free_gaps() / build_agenda()
//   backend/domains/seo/gsc.py:_query() / history.py:site_trend()
//
// Veiligheidsgrens: we vragen bij het token altijd een *readonly*-scope aan,
// ook al is de onderliggende service-account-sleutel breder gerechtigd (hij
// kan lokaal ook blokkeren). Google wijst een schrijfpoging met een
// readonly-token hard af — dezelfde garantie als "de cloud passeert nooit
// een gate", nu afgedwongen door Google zelf i.p.v. alleen door onze code.
//
// Faalt nooit naar boven toe: elke exportfunctie vangt zijn eigen fouten en
// geeft null terug, zodat de aanroeper altijd op de Neon-cache kan terugvallen.
import { JWT } from 'google-auth-library';
import { sql } from './_lib.js';
import { decrypt } from './_crypto.js';

const TZ = 'Europe/Amsterdam';
const DAY_START_HOUR = 8;
const DAY_END_HOUR = 18;
const MIN_GAP_MINUTES = 45;
const CALENDAR_API = 'https://www.googleapis.com/calendar/v3';
const GSC_API = 'https://www.googleapis.com/webmasters/v3';
const FETCH_TIMEOUT_MS = 10000;

// ── Tenant-config ────────────────────────────────────────────────────────

// ── Per-tenant Google-config ──────────────────────────────────────────────
// Twee geldige bronnen, in prioriteitsvolgorde:
//   1. De per-klant gekoppelde OAuth-account uit `oauth_accounts` (de
//      onboarding-wizard schrijft daarheen via de Bridge-relay). Dit is de
//      eigen Google-agenda/-GSC van déze klant — volledig gescheiden van
//      andere tenants. We regelen hier zelf het access-token via de
//      refresh-token (Google OAuth), en cachen dat per token.
//   2. De (legacy) service-account-kolommen in `tenants` (calendar_client_email
//      + calendar_private_key_enc), gevuld door de lokale ImpactOS-push.
// Een lege return = "niet geconfigureerd" → de aanroeper valt terug op cache.
export async function getGoogleTenantConfig(tenant) {
  // 1. Eigen OAuth-account van deze tenant (de echte wereldklasse-route).
  const oa = await getTenantOAuthConfig(tenant);
  if (oa) return oa;

  // 2. Terugval op de service-account-kolommen (bestaand gedrag).
  const rows = await sql`
    SELECT calendar_client_email, calendar_private_key_enc, calendar_calendar_id,
           calendar_busy_ids, calendar_sub, gsc_sites
    FROM tenants WHERE slug = ${tenant}`;
  const row = rows[0];
  if (!row || !row.calendar_client_email || !row.calendar_private_key_enc) return null;
  let privateKey;
  try {
    privateKey = decrypt(row.calendar_private_key_enc);
  } catch (e) {
    console.error('google private key decrypt mislukt', tenant, e);
    return null;
  }
  return {
    clientEmail: row.calendar_client_email,
    privateKey,
    calendarId: row.calendar_calendar_id || 'primary',
    busyIds: String(row.calendar_busy_ids || '').split(',').map((s) => s.trim()).filter(Boolean),
    sub: row.calendar_sub || undefined,
    gscSites: Array.isArray(row.gsc_sites) ? row.gsc_sites : [],
    viaOAuth: false,
  };
}

// Haalt de per-tenant OAuth-credentials uit `oauth_accounts` en regelt een
// vers access-token via de refresh-token (Google OAuth2 token endpoint).
// Retourneert dezelfde cfg-vorm als de service-account-route, plus een
// `accessToken` dat direct aan de Calendar/GSC API meegegeven kan worden.
async function getTenantOAuthConfig(tenant) {
  let rows;
  try {
    rows = await sql`
      SELECT credentials_json, account_email
      FROM oauth_accounts
      WHERE site_id = ${tenant} AND provider = 'google'
      ORDER BY updated_at DESC LIMIT 1`;
  } catch (e) {
    // Tabel bestaat misschien niet in oudere installs — silenced terugval.
    return null;
  }
  if (!rows.length) return null;
  const creds = (() => {
    try { return typeof rows[0].credentials_json === 'string'
      ? JSON.parse(rows[0].credentials_json) : rows[0].credentials_json; }
    catch { return null; }
  })();
  if (!creds || !creds.refresh_token) return null;
  const accessToken = await refreshAccessToken(creds);
  if (!accessToken) return null;
  return {
    clientEmail: rows[0].account_email || creds.account_email || '',
    accessToken,
    calendarId: 'primary',
    busyIds: [],
    sub: undefined,
    gscSites: [],
    viaOAuth: true,
  };
}

// Ververs een Google-OAuth access-token met de refresh-token. Cached kort.
async function refreshAccessToken(creds) {
  const cacheKey = `oauth:${creds.refresh_token.slice(0, 12)}`;
  const hit = tokenCache.get(cacheKey);
  if (hit && hit.exp > Date.now() + 60000) return hit.token;
  try {
    const resp = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_OAUTH_CLIENT_ID || '',
        client_secret: process.env.GOOGLE_OAUTH_CLIENT_SECRET || '',
        refresh_token: creds.refresh_token,
        grant_type: 'refresh_token',
      }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    const data = await resp.json();
    if (!resp.ok || !data.access_token) {
      console.error('google oauth refresh mislukt', creds.account_email || '', data.error_description || data.error || resp.statusText);
      return null;
    }
    const ttl = (data.expires_in || 3600) * 1000;
    tokenCache.set(cacheKey, { token: data.access_token, exp: Date.now() + ttl - 60000 });
    return data.access_token;
  } catch (e) {
    console.error('google oauth refresh exception', e);
    return null;
  }
}

// ── Token-cache (per warme lambda-instance; Google-tokens gelden 1 uur) ───

const tokenCache = new Map();

async function getToken(cfg, scope) {
  // OAuth-route: we hebben al een vers access-token, gebruik dat direct.
  if (cfg.viaOAuth && cfg.accessToken) return cfg.accessToken;
  const key = `${cfg.clientEmail}:${scope}:${cfg.sub || ''}`;
  const hit = tokenCache.get(key);
  if (hit && hit.exp > Date.now() + 60000) return hit.token;
  const client = new JWT({ email: cfg.clientEmail, key: cfg.privateKey, scopes: [scope], subject: cfg.sub });
  const { token } = await client.getAccessToken();
  if (!token) throw new Error('Geen access-token ontvangen');
  tokenCache.set(key, { token, exp: Date.now() + 50 * 60000 });
  return token;
}

async function fetchJson(url, opts = {}) {
  const r = await fetch(url, { ...opts, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Amsterdam wall-clock helpers (spiegelt zoneinfo.ZoneInfo in Python) ──

function zonedTimeToUtc(y, m, d, hh, mm, tz) {
  const guess = new Date(Date.UTC(y, m - 1, d, hh, mm, 0));
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(guess).reduce((a, p) => { a[p.type] = p.value; return a; }, {});
  const hour = parts.hour === '24' ? 0 : Number(parts.hour);
  const asUTC = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), hour, Number(parts.minute), Number(parts.second));
  return new Date(guess.getTime() - (asUTC - guess.getTime()));
}

function amsterdamYMD(date) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(date).reduce((a, p) => { a[p.type] = p.value; return a; }, {});
  return { y: Number(parts.year), m: Number(parts.month), d: Number(parts.day) };
}

function amsterdamDateStr(date) {
  const { y, m, d } = amsterdamYMD(date);
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

function amsterdamHHMM(date) {
  return new Intl.DateTimeFormat('nl-NL', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

function todayMidnightAmsterdam(now) {
  const { y, m, d } = amsterdamYMD(now);
  return zonedTimeToUtc(y, m, d, 0, 0, TZ);
}

// Aaneengesloten vrije blokken ≥45 min binnen de werkdag — zelfde regels als
// context.py:_free_gaps(), inclusief het wegsnijden van het verleden
// (`notBefore`): een gat van 09:00-11:00 om 14:00 tonen als "vrij" is een leugen.
function freeGaps(rows, day, notBefore) {
  const { y, m, d } = amsterdamYMD(day);
  let start = zonedTimeToUtc(y, m, d, DAY_START_HOUR, 0, TZ);
  const end = zonedTimeToUtc(y, m, d, DAY_END_HOUR, 0, TZ);
  if (notBefore && notBefore > start) start = notBefore < end ? notBefore : end;

  const busy = [];
  for (const row of rows) {
    if (row.all_day || row.declined) continue;
    const s = row.start ? new Date(row.start) : null;
    const e = row.end ? new Date(row.end) : null;
    if (s && e && e > start && s < end) {
      busy.push([s < start ? start : s, e > end ? end : e]);
    }
  }
  busy.sort((a, b) => a[0] - b[0]);

  const gaps = [];
  let cursor = start;
  for (const [s, e] of busy) {
    if ((s - cursor) / 60000 >= MIN_GAP_MINUTES) gaps.push({ start: amsterdamHHMM(cursor), end: amsterdamHHMM(s) });
    if (e > cursor) cursor = e;
  }
  if ((end - cursor) / 60000 >= MIN_GAP_MINUTES) gaps.push({ start: amsterdamHHMM(cursor), end: amsterdamHHMM(end) });
  return gaps;
}

// ── Agenda ──────────────────────────────────────────────────────────────

export async function liveAgenda(cfg) {
  if (!cfg) return null;
  try {
    const token = await getToken(cfg, 'https://www.googleapis.com/auth/calendar.readonly');
    const cids = [];
    for (const cid of [cfg.calendarId, ...cfg.busyIds]) {
      if (cid && !cids.includes(cid)) cids.push(cid);
    }

    const now = new Date();
    const day0 = todayMidnightAmsterdam(now);
    const rangeEnd = new Date(day0.getTime() + 8 * 86400000);

    const events = [];
    const unreachable = [];
    for (const cid of cids) {
      try {
        const url = `${CALENDAR_API}/calendars/${encodeURIComponent(cid)}/events`
          + `?timeMin=${encodeURIComponent(day0.toISOString())}&timeMax=${encodeURIComponent(rangeEnd.toISOString())}`
          + '&singleEvents=true&orderBy=startTime&maxResults=100';
        const data = await fetchJson(url, { headers: { Authorization: `Bearer ${token}` } });
        for (const ev of (data.items || [])) {
          if (ev.status === 'cancelled') continue;
          const s = ev.start || {};
          const en = ev.end || {};
          events.push({
            id: ev.id,
            calendar_id: cid,
            summary: ev.summary || '(geen titel)',
            start: s.dateTime || s.date || null,
            end: en.dateTime || en.date || null,
            all_day: !!(s.date && !s.dateTime),
            location: ev.location || '',
            hangout_link: ev.hangoutLink || '',
            html_link: ev.htmlLink || '',
            attendees: (ev.attendees || []).filter((a) => !a.self).map((a) => ({
              name: a.displayName || a.email || '?', email: (a.email || '').toLowerCase(),
            })),
            declined: (ev.attendees || []).some((a) => a.self && a.responseStatus === 'declined'),
          });
        }
      } catch (e) {
        unreachable.push({ id: cid, error: String(e.message || e).slice(0, 300) });
      }
    }
    events.sort((a, b) => String(a.start || '').localeCompare(String(b.start || '')));

    const todayStr = amsterdamDateStr(day0);
    const tomorrowStr = amsterdamDateStr(new Date(day0.getTime() + 86400000));

    const today = [];
    const upcoming = [];
    for (const ev of events) {
      if (!ev.start) continue;
      const s = new Date(ev.start);
      const dateStr = amsterdamDateStr(s);
      const row = {
        summary: ev.summary, start: ev.start, end: ev.end,
        time: ev.all_day ? 'hele dag' : amsterdamHHMM(s),
        location: ev.location, online: !!ev.hangout_link,
        attendees: ev.attendees, declined: ev.declined, date: dateStr, all_day: ev.all_day,
      };
      (dateStr === todayStr ? today : upcoming).push(row);
    }

    const byDay = {};
    for (const row of [...today, ...upcoming]) {
      if (row.declined) continue;
      const entry = byDay[row.date] || (byDay[row.date] = { date: row.date, count: 0, first: null, last: null, titles: [] });
      entry.count += 1;
      entry.titles = [...entry.titles, row.summary].slice(0, 4);
      if (!row.start) continue;
      if (entry.first === null || row.start < entry.first) entry.first = row.start;
      const endOrStart = row.end || row.start;
      if (entry.last === null || endOrStart > entry.last) entry.last = endOrStart;
    }

    const next = today.find((r) => r.start && new Date(r.start) > now && !r.declined) || null;

    return {
      status: 'ok',
      today,
      today_date: todayStr,
      next,
      upcoming: upcoming.slice(0, 20),
      days: Object.keys(byDay).sort().map((k) => byDay[k]),
      free_today: freeGaps(today, day0, now),
      free_tomorrow: freeGaps(upcoming.filter((r) => r.date === tomorrowStr), new Date(day0.getTime() + 86400000)),
      unreachable,
      calendars: cids,
    };
  } catch (e) {
    console.error('liveAgenda mislukt', e);
    return null;
  }
}

// ── GSC-trend ───────────────────────────────────────────────────────────

// Zelfde 7-vs-7-aggregatie als seo/history.py:site_trend(), nu op live
// opgehaalde dagrijen i.p.v. de lokaal opgebouwde gsc_history-tabel.
function siteTrendFromRows(rows) {
  if (!rows.length) return null;
  const recent = rows.slice(0, 7);
  const previous = rows.slice(7, 14);
  const agg = (chunk) => {
    if (!chunk.length) return { clicks: 0, impressions: 0, avg_position: null, days: 0 };
    const positions = chunk.map((c) => c.position).filter((p) => p);
    return {
      clicks: chunk.reduce((s, c) => s + c.clicks, 0),
      impressions: chunk.reduce((s, c) => s + c.impressions, 0),
      avg_position: positions.length
        ? Math.round((positions.reduce((s, p) => s + p, 0) / positions.length) * 10) / 10 : null,
      days: chunk.length,
    };
  };
  const cur = agg(recent);
  const prev = agg(previous);
  const out = {
    last7: cur,
    prev7: prev,
    delta_clicks: prev.days ? cur.clicks - prev.clicks : null,
    delta_impressions: prev.days ? cur.impressions - prev.impressions : null,
    // Positie: lager = beter, dus een negatieve delta is winst (zelfde als Python).
    delta_position: (cur.avg_position != null && prev.avg_position != null)
      ? Math.round((cur.avg_position - prev.avg_position) * 10) / 10 : null,
  };
  out.clicks_pct = (prev.days && prev.clicks)
    ? Math.round(((cur.clicks - prev.clicks) / prev.clicks) * 1000) / 10 : null;
  return out;
}

export async function liveSeoTrend(cfg) {
  if (!cfg || !cfg.gscSites || !cfg.gscSites.length) return null;
  try {
    const token = await getToken(cfg, 'https://www.googleapis.com/auth/webmasters.readonly');
    // GSC-data loopt ~2 dagen achter — zelfde end_offset-logica als seo/gsc.py:_query().
    const end = new Date();
    end.setUTCDate(end.getUTCDate() - 2);
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - 13);
    const fmt = (d) => d.toISOString().slice(0, 10);

    const out = {};
    for (const site of cfg.gscSites) {
      if (!site.gsc_property) continue;
      try {
        const url = `${GSC_API}/sites/${encodeURIComponent(site.gsc_property)}/searchAnalytics/query`;
        const data = await fetchJson(url, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ startDate: fmt(start), endDate: fmt(end), dimensions: ['date'], rowLimit: 14, dataState: 'final' }),
        });
        const rows = (data.rows || [])
          .map((r) => ({
            date: r.keys[0],
            clicks: Number(r.clicks || 0),
            impressions: Number(r.impressions || 0),
            position: Number(r.position || 0),
          }))
          .sort((a, b) => b.date.localeCompare(a.date));
        out[site.site_id] = siteTrendFromRows(rows);
      } catch (e) {
        console.error('gsc-trend mislukt voor site', site.site_id, e);
      }
    }
    return out;
  } catch (e) {
    console.error('liveSeoTrend mislukt', e);
    return null;
  }
}

// ── Samenvoegen in de context-payload ─────────────────────────────────────

export async function attachLive(tenant, payload) {
  const result = { agenda: false, seo: false };
  let cfg;
  try {
    cfg = await getGoogleTenantConfig(tenant);
  } catch (e) {
    console.error('google-tenant-config ophalen mislukt', tenant, e);
    return result;
  }
  if (!cfg) return result;

  const errors = [];

  const agenda = await liveAgenda(cfg);
  if (agenda) {
    // pending_proposals komt niet uit de Calendar-API — uit de cache
    // overnemen i.p.v. een veld te laten verdwijnen dat de UI toont.
    agenda.pending_proposals = payload.agenda?.pending_proposals ?? null;
    payload.agenda = agenda;
    result.agenda = true;
  } else {
    errors.push("agenda: alle agenda's onbereikbaar of niet geconfigureerd");
  }

  if (cfg.gscSites.length) {
    const trends = await liveSeoTrend(cfg);
    const entries = trends ? Object.entries(trends).filter(([, t]) => t) : [];
    if (entries.length) {
      const sites = Array.isArray(payload.seo?.sites) ? [...payload.seo.sites] : [];
      for (const [siteId, trend] of entries) {
        const idx = sites.findIndex((s) => s.site_id === siteId);
        if (idx >= 0) {
          sites[idx] = { ...sites[idx], trend, trend_live: true };
        } else {
          const cfgSite = cfg.gscSites.find((s) => s.site_id === siteId);
          sites.push({
            site_id: siteId, name: cfgSite?.name || siteId, base_url: '',
            trend, risers: [], fallers: [], top_pages: [], trend_live: true,
          });
        }
      }
      payload.seo = { ...(payload.seo || {}), sites };
      result.seo = true;
    } else {
      errors.push('gsc: geen enkele site leverde een trend op');
    }
  }

  if (result.agenda || result.seo) {
    await sql`UPDATE tenants SET google_last_error = NULL, google_last_error_at = NULL
              WHERE slug = ${tenant} AND google_last_error IS NOT NULL`.catch(() => {});
  } else if (errors.length) {
    await sql`UPDATE tenants SET google_last_error = ${errors.join(' | ').slice(0, 300)},
              google_last_error_at = now() WHERE slug = ${tenant}`.catch(() => {});
  }

  return result;
}
