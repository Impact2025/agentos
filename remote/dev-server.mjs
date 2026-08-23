// Lokale dev-server — bootst Vercel na: statische bestanden + de api/*.js
// functies, tegen de échte Neon-database. Alleen voor lokaal testen:
//   node dev-server.mjs [poort]
// Env komt uit remote/.env.dev.local (DATABASE_URL, APP_PASSWORD, VAPID) en
// ../.env (OPENMODEL_*, BRIDGE_TOKEN). Productie draait dit bestand nooit.
import http from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { join, extname, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('.', import.meta.url));

function loadEnv(path, keys = null) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (!m) continue;
    if (keys && !keys.includes(m[1])) continue;
    if (!process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
}
loadEnv(join(ROOT, '.env.dev.local'));
loadEnv(join(ROOT, '..', '.env'), [
  'OPENMODEL_API_KEY', 'OPENMODEL_BASE_URL', 'OPENMODEL_MODEL', 'BRIDGE_TOKEN',
]);
if (!process.env.DATABASE_URL) {
  console.error('DATABASE_URL ontbreekt (zet hem in remote/.env.dev.local)');
  process.exit(1);
}

const handlers = {
  '/api/bridge': (await import('./api/bridge.js')).default,
  '/api/ui': (await import('./api/ui.js')).default,
  '/api/iris': (await import('./api/iris.js')).default,
  '/api/whatsapp': (await import('./api/whatsapp.js')).default,
};

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json',
  '.sql': 'text/plain', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  // Vercel-adapter: .status(), .query, .body
  res.status = (c) => { res.statusCode = c; return res; };
  req.query = Object.fromEntries(url.searchParams);

  const handler = handlers[url.pathname];
  if (handler) {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    // whatsapp.js leest zelf de raw body (voor de Meta-signature-check) — de
    // echte productie-runtime levert die via bodyParser:false; hier is er geen
    // stream meer over om opnieuw te lezen, dus zetten we 'm alvast klaar.
    req.rawBody = Buffer.from(raw, 'utf8');
    try { req.body = raw ? JSON.parse(raw) : {}; } catch { req.body = {}; }
    try { await handler(req, res); } catch (e) {
      console.error(e);
      if (!res.writableEnded) { res.statusCode = 500; res.end(JSON.stringify({ error: String(e) })); }
    }
    return;
  }

  // Statisch (cleanUrls-gedrag: / → index.html)
  let file = url.pathname === '/' ? '/index.html' : url.pathname;
  file = normalize(file).replace(/^([\\/.])+/, '');
  const path = join(ROOT, file);
  if (!path.startsWith(ROOT) || !existsSync(path)) {
    res.statusCode = 404; res.end('not found'); return;
  }
  res.setHeader('Content-Type', MIME[extname(path)] || 'application/octet-stream');
  res.end(readFileSync(path));
});

const port = Number(process.argv[2] || 3470);
server.listen(port, () => console.log(`Iris Remote dev op http://localhost:${port}`));
