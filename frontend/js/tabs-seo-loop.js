// ── Impact OS — sectie: SEO Loop (Loop Engineering, Greg Isenberg / Ellie-case)
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.
//
// Was tot 22 aug 2026 een eigen tab; is nu een sectie ónderaan Optimalisatie
// (tabs-memory.js:renderOptimalisatieTab) — allebei beantwoordden dezelfde
// vraag ("welke bestaande pagina verdient aandacht") met een andere
// tijdshorizon (wekelijks vs. maandelijks), en dat leverde twee schermen op
// voor één beslissing. De functienaam is mee-hernoemd (was renderSeoLoopTab)
// zodat er geen "Tab" meer heet wat geen tab meer is.
//
// Toont per GSC-site de objectieve KPI (positie/klikken vs. vorig venster),
// de run-geschiedenis als lijn, de striking-distance-targets (Build-input) en
// een "Run nu"-actie. Live-feed via de gedeelde /api/loops/stream (filtert
// op seo_loop_* events).

let _seoLoopES = null;
let _seoLoopChart = null;
// Laatst opgehaalde sites-met-KPI — bewaard op moduleniveau omdat
// loadSeoLoopRuns() de geschiedenisgrafiek van de eerste site tekent en dat
// niet kan zonder te weten wélke site dat is (voorheen een losse `sites`
// die buiten zijn scope werd gelezen — een ReferenceError die stil de hele
// rijenlijst verving door een foutmelding).
let _seoLoopSites = [];

// ── Sectie-entry ───────────────────────────────────────────────────────────
async function renderSeoLoopSection(el) {
  el.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
      '<h3 style="font-size:15px;font-weight:700">SEO Loop</h3>' +
      '<span style="font-size:11px;color:#64748b">Build → objectieve Verify (GSC) → Geheugen → mens-goedkeuring</span>' +
    '</div>' +
    '<div id="seo-loop-sites" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:16px"></div>' +
    '<div style="display:grid;grid-template-columns:1.4fr 1fr;gap:16px">' +
      '<div class="section-card">' +
        '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Run-geschiedenis (KPI-score)</h4>' +
        '<canvas id="seo-loop-chart" height="120"></canvas>' +
        '<div id="seo-loop-feed" style="margin-top:10px;font-size:12px;color:#64748b;max-height:120px;overflow:auto"></div>' +
      '</div>' +
      '<div class="section-card">' +
        '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Striking distance (positie 11-30)</h4>' +
        '<div id="seo-loop-opps" style="font-size:12px"></div>' +
      '</div>' +
    '</div>' +
    '<div class="section-card" style="margin-top:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Recente runs</h4>' +
      '<div id="seo-loop-runs" style="font-size:12px"></div>' +
    '</div>' +
    '<div class="section-card" style="margin-top:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Laatste verbeter-voorstellen</h4>' +
      '<div id="seo-loop-props" style="font-size:12px;white-space:pre-wrap"></div>' +
    '</div>';

  await loadSeoLoopSites(el);
  await loadSeoLoopRuns(el);
  if (typeof __seoFirstSite !== 'undefined' && __seoFirstSite) seoLoopShowProposals(__seoFirstSite);
  startSeoLoopFeed(el);
}

async function loadSeoLoopSites(el) {
  const box = document.getElementById('seo-loop-sites');
  box.innerHTML = '<div style="color:#64748b;padding:8px">Laden…</div>';
  try {
    const sites = await fetch('/api/seo-loops/sites').then(r => r.json());
    _seoLoopSites = sites || [];
    if (!sites.length) { box.innerHTML = '<div style="color:#64748b;padding:8px">Geen GSC-sites gevonden.</div>'; return; }
    window.__seoFirstSite = sites[0].site_id;
    box.innerHTML = sites.map(s => {
      const k = s.kpi || {};
      const posUp = (k.position_delta || 0) < 0;   // lager = beter
      const clkUp = (k.click_delta || 0) > 0;
      return '<div class="section-card" style="margin:0">' +
        '<div style="display:flex;justify-content:space-between;align-items:baseline">' +
          '<strong style="font-size:13px">' + escHtml(s.name) + '</strong>' +
          '<button class="btn-sm" onclick="seoLoopRun(\'' + s.site_id + '\', false)">Run live</button>' +
        '</div>' +
        '<div style="display:flex;gap:14px;margin-top:6px;font-size:12px">' +
          '<div><div style="color:#64748b">Positie</div><b>' + fmt(k.avg_position_cur) + '</b> ' +
            '<span style="color:' + (posUp ? '#16a34a' : '#dc2626') + '">' + (posUp ? '▼' : '▲') + ' ' + fmt(Math.abs(k.position_delta || 0)) + '</span></div>' +
          '<div><div style="color:#64748b">Klikken</div><b>' + (k.clicks_cur || 0) + '</b> ' +
            '<span style="color:' + (clkUp ? '#16a34a' : '#dc2626') + '">' + (clkUp ? '+' : '') + (k.click_delta || 0) + ' (' + (k.click_pct || 0) + '%)</span></div>' +
          '<div><div style="color:#64748b">KPI</div><b style="color:' + ((k.kpi_score || 0) >= 0 ? '#16a34a' : '#dc2626') + '">' + fmt(k.kpi_score) + '</b></div>' +
        '</div>' +
        '<div style="margin-top:8px;display:flex;gap:8px">' +
          '<button class="btn-sm-ghost" onclick="seoLoopSelectSite(\'' + s.site_id + '\')">Toon targets</button>' +
          '<button class="btn-sm-ghost" onclick="seoLoopRun(\'' + s.site_id + '\', true)">Dry-run</button>' +
        '</div>' +
      '</div>';
    }).join('');
  } catch (e) {
    box.innerHTML = '<div style="color:#dc2626;padding:8px">Fout: ' + escHtml(String(e)) + '</div>';
  }
}

async function loadSeoLoopRuns(el) {
  const box = document.getElementById('seo-loop-runs');
  try {
    const runs = await fetch('/api/seo-loops').then(r => r.json());
    if (!runs.length) { box.innerHTML = '<div style="color:#64748b">Nog geen runs.</div>'; return; }
    box.innerHTML = runs.slice(0, 12).map(r =>
      '<div style="padding:6px 0;border-bottom:1px solid var(--card-border)">' +
        '<strong>' + escHtml(r.project) + '</strong> <span style="color:#64748b">' + escHtml((r.created_at || '').slice(0, 16)) + '</span><br>' +
        escHtml(r.detail) + (r.next_step ? '<br><span style="color:#2563eb">' + escHtml(r.next_step) + '</span>' : '') +
      '</div>'
    ).join('');
    if (_seoLoopSites.length) seoLoopDrawHistory(_seoLoopSites[0].site_id);
  } catch (e) {
    box.innerHTML = '<div style="color:#dc2626;padding:8px">Fout: ' + escHtml(String(e)) + '</div>';
  }
}

async function seoLoopSelectSite(siteId) {
  seoLoopLoadOpps(siteId);
  seoLoopDrawHistory(siteId);
  seoLoopShowProposals(siteId);
}

async function seoLoopShowProposals(siteId) {
  const box = document.getElementById('seo-loop-props');
  if (!box) return;
  box.innerHTML = 'Laden…';
  try {
    const runs = await fetch('/api/seo-loops/' + encodeURIComponent(siteId) + '/history').then(r => r.json());
    // Laatste live-run mét proposals.
    const last = runs.filter(r => r.proposals).pop();
    if (!last) { box.innerHTML = '<div style="color:#64748b">Nog geen live-voorstellen. Draai een live-run.</div>'; return; }
    box.innerHTML = '<div style="color:#64748b;margin-bottom:6px">' +
      escHtml((last.ran_at || '').slice(0, 16)) + ' · KPI ' + fmt(last.kpi_score) + '</div>' +
      escHtml(last.proposals);
  } catch (e) {
    box.innerHTML = '<div style="color:#dc2626">Fout: ' + escHtml(String(e)) + '</div>';
  }
}

function seoLoopDrawHistory(siteId) {
  fetch('/api/seo-loops/' + encodeURIComponent(siteId) + '/history').then(r => r.json()).then(runs => {
    const data = runs.map(r => r.kpi_score || 0).reverse();
    const labels = runs.map((r, i) => (r.ran_at || '').slice(0, 10)).reverse();
    const ctx = document.getElementById('seo-loop-chart');
    if (!ctx || !data.length) return;
    if (_seoLoopChart) _seoLoopChart.destroy();
    _seoLoopChart = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets: [{ data, borderColor: '#2563eb', tension: 0.3, fill: false, pointRadius: 3 }] },
      options: { plugins: { legend: { display: false } }, scales: { x: { display: true, ticks: { maxRotation: 0, autoSkip: true } }, y: { beginAtZero: true } } }
    });
  }).catch(() => {});
}

async function seoLoopLoadOpps(siteId) {
  const box = document.getElementById('seo-loop-opps');
  box.innerHTML = 'Laden…';
  try {
    const opps = await fetch('/api/seo-loops/' + encodeURIComponent(siteId) + '/opportunities').then(r => r.json());
    if (!opps.length) { box.innerHTML = '<div style="color:#64748b">Geen striking-distance targets.</div>'; return; }
    box.innerHTML = '<table style="width:100%;border-collapse:collapse">' +
      '<tr style="color:#64748b;text-align:left"><th style="padding:4px">Query</th><th style="padding:4px">Pos</th><th style="padding:4px">Clicks</th></tr>' +
      opps.slice(0, 15).map(o =>
        '<tr style="border-top:1px solid var(--card-border)">' +
          '<td style="padding:4px">' + escHtml(o.query) + '</td>' +
          '<td style="padding:4px">' + fmt(o.position) + '</td>' +
          '<td style="padding:4px">' + (o.clicks || 0) + '</td>' +
        '</tr>'
      ).join('') + '</table>';
  } catch (e) {
    box.innerHTML = '<div style="color:#dc2626">Fout: ' + escHtml(String(e)) + '</div>';
  }
}

async function seoLoopRun(siteId, dryRun) {
  const feed = document.getElementById('seo-loop-feed');
  feed.innerHTML = (feed.innerHTML || '') + '<div>' + (dryRun ? 'Dry-run' : 'Live') + ' gestart…</div>';
  try {
    const res = await post('/api/seo-loops/' + encodeURIComponent(siteId) + '/run', { dry_run: dryRun, window_days: 28, focus_striking_distance: true });
    feed.innerHTML += '<div style="color:#16a34a">✓ ' + escHtml(JSON.stringify(res)) + '</div>';
    // Ververs na afloop (de run logt async naar activity_log). De sectie leeft
    // nu binnen Optimalisatie, dus is de hele tab herladen — niet alleen deze
    // sectie — de juiste manier om ook de optimizer-suggesties te verversen.
    setTimeout(() => {
      if (currentTab === 'Optimalisatie') renderOptimalisatieTab(document.getElementById('tab-content'));
    }, 6000);
  } catch (e) {
    feed.innerHTML += '<div style="color:#dc2626">✗ ' + escHtml(String(e)) + '</div>';
  }
}

function startSeoLoopFeed(el) {
  if (_seoLoopES) return;
  try {
    _seoLoopES = new EventSource('/api/loops/stream');
    _seoLoopES.onmessage = function (ev) {
      let d; try { d = JSON.parse(ev.data); } catch { return; }
      if (!String(d.type || '').startsWith('seo_loop_')) return;
      const feed = document.getElementById('seo-loop-feed');
      if (feed) feed.innerHTML += '<div style="color:#2563eb">' + escHtml(JSON.stringify(d)) + '</div>';
    };
  } catch (e) { /* SSE niet kritiek */ }
}

function fmt(v) { return (v === undefined || v === null) ? '–' : (typeof v === 'number' ? (Math.round(v * 100) / 100) : v); }
function escHtml(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
