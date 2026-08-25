// Live-poller voor het LSP-workshop-scherm (lsp-dashboard.html). Losstaand
// bestand omdat de CSP (vercel.json) geen 'unsafe-inline' op script-src
// toestaat. Open deze pagina als /lsp-dashboard?token=...&tenant=weareimpact
// — het token komt uit LSP_DASHBOARD_TOKEN in Vercel.
(function () {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  const tenant = params.get('tenant') || '';
  const POLL_MS = 4000;

  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const status = document.getElementById('status');

  if (!token) {
    empty.textContent = 'Geen token meegegeven in de URL — voeg ?token=... toe.';
    return;
  }

  let afterId = 0;
  let failCount = 0;

  function fmtTime(iso) {
    try {
      return new Date(iso).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  }

  function addCard(sub) {
    empty.style.display = 'none';
    grid.style.display = 'grid';
    const card = document.createElement('div');
    card.className = 'card';
    const img = document.createElement('img');
    img.src = sub.image_data_url || '';
    img.alt = sub.team_label || 'Bouwwerk';
    card.appendChild(img);
    const body = document.createElement('div');
    body.className = 'body';
    const team = document.createElement('p');
    team.className = 'team';
    team.textContent = sub.team_label || `Inzending #${sub.id}`;
    body.appendChild(team);
    if (sub.agent_type) {
      const badge = document.createElement('span');
      badge.className = 'agent-badge';
      badge.textContent = sub.agent_type;
      body.appendChild(badge);
    }
    const summary = document.createElement('p');
    summary.className = 'summary';
    summary.textContent = sub.dashboard_summary || '';
    const time = document.createElement('p');
    time.className = 'time';
    time.textContent = fmtTime(sub.created_at);
    body.appendChild(summary);
    body.appendChild(time);
    card.appendChild(body);
    grid.appendChild(card);
  }

  async function poll() {
    try {
      const url = `/api/lsp?token=${encodeURIComponent(token)}&after_id=${afterId}`
        + (tenant ? `&tenant=${encodeURIComponent(tenant)}` : '');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const rows = data.submissions || [];
      for (const sub of rows) {
        addCard(sub);
        if (sub.id > afterId) afterId = sub.id;
      }
      failCount = 0;
      status.textContent = '';
    } catch (e) {
      failCount += 1;
      status.textContent = failCount > 2 ? 'Verbinding haperde, ik blijf het proberen...' : '';
    }
    setTimeout(poll, POLL_MS);
  }

  poll();
})();
