// Live-dashboardvoeding voor de LSP-workshop (lsp-dashboard.html/.js) —
// géén sessie/login zoals de rest van Impact OS Remote: dit scherm gaat op een
// projector zonder dat iemand hoeft in te loggen. In plaats daarvan een
// simpele token-gate (LSP_DASHBOARD_TOKEN) zodat de Vercel-URL niet door een
// willekeurige gast te raden is. `after_id` laat de pagina alleen nieuwe
// rijen ophalen in plaats van bij elke poll alle foto's opnieuw te downloaden.
import { sql, json } from './_lib.js';

export default async function handler(req, res) {
  if (req.method !== 'GET' && req.method !== 'DELETE') { res.statusCode = 405; return res.end('GET or DELETE only'); }

  const expected = process.env.LSP_DASHBOARD_TOKEN;
  const got = (req.query && req.query.token) || '';
  if (!expected || got !== expected) {
    return json(res, 401, { error: 'ongeldig of ontbrekend token' });
  }

  const tenant = (req.query && req.query.tenant) || process.env.LSP_WORKSHOP_TENANT || 'weareimpact';

  // Handmatig een verkeerde/test-inzending opruimen (bv. via curl door Vincent
  // of Iris) — bewust achter hetzelfde token als het scherm zelf, en gescoped
  // op tenant zodat een id nooit een andere klant kan raken.
  if (req.method === 'DELETE') {
    const ids = String((req.query && req.query.ids) || '')
      .split(',').map((s) => Number(s.trim())).filter((n) => Number.isInteger(n) && n > 0);
    if (!ids.length) return json(res, 400, { error: 'geen geldige ids meegegeven (?ids=1,2)' });
    const rows = await sql`
      DELETE FROM lsp_submissions WHERE tenant = ${tenant} AND id = ANY(${ids})
      RETURNING id`;
    return json(res, 200, { deleted: rows.map((r) => r.id) });
  }

  const afterId = Number((req.query && req.query.after_id) || 0) || 0;

  const rows = await sql`
    SELECT id, team_label, agent_type, dashboard_summary, participant_report, image_data_url, created_at
    FROM lsp_submissions
    WHERE tenant = ${tenant} AND id > ${afterId}
    ORDER BY id ASC LIMIT 25`;
  return json(res, 200, { submissions: rows });
}
