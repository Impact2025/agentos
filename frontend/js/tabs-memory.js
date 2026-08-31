// ── Impact OS — tabs: Geheugen + Memory Galaxy (3D-sterrenkaart)
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// ═══════════════════════════════════════════════════════════════════
//  GEHEUGEN — Infinite Context Engine tab
// ═══════════════════════════════════════════════════════════════════
async function renderGeheugenTab(el) {
  var sub = window._memSubtab || 'galaxy';
  var btn = function(id, label) {
    var active = sub === id;
    return '<button onclick="switchMemSubtab(\'' + id + '\')" class="btn btn-sm ' + (active ? 'btn-primary' : 'btn-ghost') + '">' + label + '</button>';
  };
  el.innerHTML = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<h3 style="font-size:15px;font-weight:700">Geheugen</h3>' +
    '<div style="display:flex;gap:6px">' + btn('galaxy', 'Galaxy') + btn('overzicht', 'Overzicht') + '</div></div>' +
    '<div id="mem-sub-content"></div>';
  var subEl = document.getElementById('mem-sub-content');
  if (sub === 'galaxy') await renderMemoryGalaxy(subEl);
  else await renderGeheugenOverzicht(subEl);
}
function switchMemSubtab(id) {
  window._memSubtab = id;
  var el = document.getElementById('tab-content');
  if (el) renderGeheugenTab(el);
}

async function renderGeheugenOverzicht(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Geheugen laden...</p></div>';
  try {
    var resp = await fetch('/api/infinite-context/status');
    var data = await resp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Infinite Context Engine — Oneindige Geheugenlus</h3>';

  if (!data.configured && !data.omi_configured) {
    html += '<div class="empty-state" style="margin-bottom:16px">' +
      '<p style="margin-bottom:8px">De Infinite Context Engine is niet aangesloten.</p>' +
      '<p style="font-size:12px;color:var(--text-muted)">Stel OBSIDIAN_VAULT_PATH of OMI_API_KEY in .env in om de Oneindige Loop te starten.</p></div>';
    el.innerHTML = html;
    return;
  }

  // ── Statuskaarten ──
  html += '<div class="kpi-grid">' +
    kpiBox('Obsidian vault', data.configured ? 'Actief' : 'Uit', '', data.vault_path || '') +
    kpiBox('OMI', data.omi_configured ? 'Actief' : 'Uit') +
    kpiBox('Notities', data.note_count || 0, '', 'totaal in vault') +
    kpiBox('Vandaag', data.today_session_count || 0, '', 'agent-sessies') +
  '</div>';

  // ── The Loop uitleg ──
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">De Oneindige Loop (The Loop)</h4>' +
    '<div style="font-size:12px;line-height:1.7;color:var(--text-dim)">' +
    '<div style="display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap;margin:8px 0;padding:12px;background:var(--neutral-bg);border-radius:8px;font-size:11px">' +
    '<span class="pill pill-info">READ</span>' +
    '<span style="color:var(--text-muted)">\u{2192}</span>' +
    '<span class="pill pill-ok">ACT</span>' +
    '<span style="color:var(--text-muted)">\u{2192}</span>' +
    '<span class="pill pill-warn">WRITE</span>' +
    '<span style="color:var(--text-muted)">\u{2192}</span>' +
    '<span class="pill pill-neutral" style="font-style:italic">elke dag slimmer</span>' +
    '</div>' +
    '<ul style="padding-left:16px;margin:0">' +
    '<li><strong>READ</strong> \u{2192} Laadt context uit Obsidian + OMI v\u00f3\u00f3r elke agent-run</li>' +
    '<li><strong>ACT</strong> \u{2192} Agent voert taak uit met rijke context in system prompt</li>' +
    '<li><strong>WRITE</strong> \u{2192} Resultaten terug naar Obsidian (dagboek, taken, doelen) + OMI</li>' +
    '</ul></div></div>';

  // ── ImpactOS folder statistieken ──
  if (data.folders && data.folders.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">ImpactOS in Obsidian</h4>' +
      '<div class="kpi-grid">';
    data.folders.forEach(function(f) {
      html += kpiBox(f.folder.split('/').pop(), f.count, '', f.count > 0 ? f.recent_files.map(function(x){return x.name;}).join(', ').substring(0,40) + (f.count>5 ? '...' : '') : 'leeg');
    });
    html += '</div></div>';
  }

  // ── Dagboek vandaag ──
  if (data.daily_log_preview) {
    html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Dagboek vandaag</h4>' +
      '<pre style="font-size:11px;line-height:1.5;color:var(--text-dim);white-space:pre-wrap;max-height:400px;overflow-y:auto;padding:8px;background:var(--neutral-bg);border-radius:6px">' +
      escHtml(data.daily_log_preview) +
      '</pre></div>';
  }

  // ── OMI status ──
  if (data.omi_configured) {
    html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">OMI (Open Memory Interface)</h4>' +
      '<p style="font-size:12px;color:var(--text-dim)">OMI is actief. Memories en conversaties worden automatisch meegestuurd context voor alle agent-runs. Resultaten worden teruggeschreven als OMI-memories.</p></div>';
  } else {
    html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">OMI (Open Memory Interface)</h4>' +
      '<p style="font-size:12px;color:var(--text-muted)">OMI is niet geconfigureerd. Stel OMI_API_KEY in .env in om real-time gesprekscontext uit OMI te gebruiken.</p></div>';
  }

  el.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════
//  OPTIMALISATIE — SEO Optimizer: interne links, CTR-kansen, refresh
//  Verzilvert rankings die de site al heeft (GSC-data + live site)
// ═══════════════════════════════════════════════════════════════════
function optShortUrl(u) {
  try { var p = new URL(u); return p.pathname === '/' ? p.hostname : p.pathname; } catch (e) { return u; }
}

async function renderOptimalisatieTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Optimalisatiekansen laden...</p></div>';
  var data;
  try {
    var resp = await fetch('/api/seo-optimizer/' + encodeURIComponent(currentProject) + '/suggestions');
    if (!resp.ok) { var err = await resp.json().catch(function(){return {};}); throw new Error(err.detail || resp.status); }
    data = await resp.json();
  } catch (e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var sugs = data.suggestions || [];
  var byType = { internal_link: [], ctr: [], refresh: [] };
  sugs.forEach(function(s) { if (byType[s.type]) byType[s.type].push(s); });
  var missedClicks = byType.ctr.reduce(function(a, s) { return a + (s.data.missed_clicks_per_period || 0); }, 0);

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<div><h3 style="font-size:15px;font-weight:700">SEO Optimizer</h3>' +
    '<p style="font-size:11px;color:var(--text-muted);margin-top:2px">Verzilvert rankings die je al hebt: interne links, CTR en content-refresh. Scant automatisch elke maandag 07:45.</p></div>' +
    '<button id="opt-scan-btn" onclick="runOptimizerScan(this)" class="btn btn-primary">Scan nu</button></div>';

  html += '<div class="kpi-grid" style="margin-bottom:16px">' +
    kpiBox('Interne linkkansen', byType.internal_link.length, '', 'ontbrekende links') +
    kpiBox('CTR-kansen', byType.ctr.length, '', '~' + Math.round(missedClicks) + ' gemiste klikken/28d') +
    kpiBox('Refresh-kandidaten', byType.refresh.length, '', 'wegzakkende pagina’s') +
    '</div>';

  if (!sugs.length) {
    html += '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:var(--text-dim);margin-bottom:6px">Nog geen openstaande kansen</p>' +
      '<p style="color:var(--text-muted);font-size:12px">Klik op “Scan nu” om de site en Search Console-data te analyseren.</p></div>';
    el.innerHTML = html;
    return;
  }

  var actBtns = function(s) {
    return '<button onclick="optSuggestionAction(\'' + s.id + '\',\'done\',this)" title="Gedaan" class="btn btn-sm" style="background:var(--ok-bg);color:var(--ok-fg);border-color:var(--ok-border)">Gedaan</button>' +
      '<button onclick="optSuggestionAction(\'' + s.id + '\',\'dismissed\',this)" title="Verwerpen" class="btn btn-sm btn-ghost">Verwerp</button>';
  };

  // ── CTR-kansen (grootste directe winst) ──
  if (byType.ctr.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">CTR-kansen — je ranking is er al, verzilver hem</h3>' +
      '<p style="font-size:11px;color:var(--text-muted);margin-bottom:10px">Pagina’s die veel minder klikken krijgen dan normaal voor hun positie. Betere title/meta = directe traffic zonder linkbuilding.</p>';
    byType.ctr.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid var(--card-border);border-radius:var(--radius-md);margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<a href="' + escHtml(s.page) + '" target="_blank" style="font-size:12px;font-weight:600;color:var(--text);text-decoration:none;flex:1;min-width:200px">' + escHtml(optShortUrl(s.page)) + '</a>' +
        '<span class="pill pill-warn">CTR ' + d.ctr + '% → benchmark ' + d.expected_ctr + '%</span>' +
        '<span style="font-size:11px;color:var(--text-muted)">pos ' + d.position + ' · ' + d.impressions + ' imp · ~' + d.missed_clicks_per_period + ' gemiste klikken</span>' +
        '<button onclick="optGenerateVariants(\'' + s.id + '\',this)" class="btn btn-sm btn-primary">Title/meta-varianten</button>' +
        actBtns(s) + '</div>' +
        (s.query ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">zoekwoord: ' + escHtml(s.query) + '</div>' : '') +
        '<div id="opt-variants-' + s.id + '">' + (d.variants ? renderOptVariants(d, s.id) : '') + '</div>' +
        '</div>';
    });
    html += '</div>';
  }

  // ── Interne links ──
  if (byType.internal_link.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">Ontbrekende interne links</h3>' +
      '<p style="font-size:11px;color:var(--text-muted);margin-bottom:10px">De ankertekst staat al letterlijk op de bronpagina — alleen de link ontbreekt nog. Plaats de link in je CMS en markeer als gedaan.</p>';
    byType.internal_link.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid var(--card-border);border-radius:var(--radius-md);margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<span style="font-size:12px;color:var(--text);flex:1;min-width:220px"><a href="' + escHtml(d.from) + '" target="_blank" style="color:var(--accent);text-decoration:none">' + escHtml(optShortUrl(d.from)) + '</a>' +
        ' <span style="color:var(--text-muted)">→</span> <a href="' + escHtml(d.to) + '" target="_blank" style="color:var(--green);text-decoration:none">' + escHtml(optShortUrl(d.to)) + '</a></span>' +
        '<span style="font-size:11px;color:var(--text-muted)">' + (d.target_impressions || 0) + ' imp/28d</span>' + actBtns(s) + '</div>' +
        '<div style="font-size:11px;color:var(--text-dim);margin-top:6px;background:var(--neutral-bg);padding:6px 8px;border-radius:6px">…' + escHtml((d.context || '').replace(d.anchor, '')).slice(0, 60) + '<strong style="background:var(--warn-bg);padding:0 3px;border-radius:3px">' + escHtml(d.anchor) + '</strong>…</div>' +
        '</div>';
    });
    html += '</div>';
  }

  // ── Refresh-kandidaten ──
  if (byType.refresh.length) {
    html += '<div class="section-card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">Content-refresh — wegzakkende pagina’s</h3>' +
      '<p style="font-size:11px;color:var(--text-muted);margin-bottom:10px">De agent haalt de pagina + huidige top-resultaten op, verrijkt het artikel en zet het in de Wachtrij ter review.</p>';
    byType.refresh.forEach(function(s) {
      var d = s.data;
      html += '<div id="opt-' + s.id + '" style="padding:10px;border:1px solid var(--card-border);border-radius:var(--radius-md);margin-bottom:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<a href="' + escHtml(s.page) + '" target="_blank" style="font-size:12px;font-weight:600;color:var(--text);text-decoration:none;flex:1;min-width:200px">' + escHtml(optShortUrl(s.page)) + '</a>' +
        '<span class="pill pill-danger">' + escHtml(s.title) + '</span>' +
        '<button onclick="optRefresh(\'' + s.id + '\',this)" class="btn btn-sm" style="background:var(--ok-bg);color:var(--ok-fg);border-color:var(--ok-border)">Ververs → Wachtrij</button>' +
        actBtns(s) + '</div>' +
        (s.query ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">zoekwoord: ' + escHtml(s.query) + '</div>' : '') +
        '</div>';
    });
    html += '</div>';
  }

  // ── Maandelijkse SEO Loop (was tot 22 aug 2026 een eigen tab) ──
  // Zelfde onderliggende vraag als hierboven — welke bestaande pagina
  // verdient aandacht — maar op maandritme met een objectieve KPI-verify in
  // plaats van losse suggesties. Eigen sectie i.p.v. een los scherm, zodat
  // je nog maar één plek hebt voor "bestaande pagina's verbeteren".
  html += '<div class="section-card" style="margin-top:8px"><div id="seoloop-embed"></div></div>';

  el.innerHTML = html;
  renderSeoLoopSection(document.getElementById('seoloop-embed'));
}

function renderOptVariants(d, sid) {
  if (!d.variants || !d.variants.length) return '';
  var html = '<div style="margin-top:8px;border-top:1px dashed var(--card-border);padding-top:8px">' +
    (d.current_title ? '<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">Nu: <em>' + escHtml(d.current_title) + '</em></div>' : '');
  d.variants.forEach(function(v, i) {
    html += '<div id="opt-variant-' + sid + '-' + i + '" style="padding:6px 8px;background:var(--neutral-bg);border-radius:6px;margin-bottom:5px;display:flex;align-items:flex-start;gap:8px">' +
      '<div style="flex:1;min-width:0">' +
      '<div style="font-size:12px;font-weight:600;color:var(--text)">' + (i + 1) + '. ' + escHtml(v.title || '') + '</div>' +
      '<div style="font-size:11px;color:var(--text-dim);margin-top:2px">' + escHtml(v.meta || '') + '</div>' +
      (v.waarom ? '<div style="font-size:10px;color:var(--text-muted);margin-top:2px;font-style:italic">' + escHtml(v.waarom) + '</div>' : '') +
      '</div>' +
      (sid ? '<button onclick="optApplyVariant(\'' + sid + '\',' + i + ',this)" class="btn btn-sm btn-primary" style="white-space:nowrap">Zet live</button>' : '') +
      '</div>';
  });
  return html + '</div>';
}

// Zet een gekozen title/meta-variant daadwerkelijk op de live pagina — het
// stapje na "Title/meta-varianten" dat eerder ontbrak (Vincent moest het zelf
// in het CMS overtypen). Loopt server-side via dezelfde publicatieroute en
// wordt daar ook geverifieerd op de live pagina.
async function optApplyVariant(sid, idx, btn) {
  var row = document.getElementById('opt-variant-' + sid + '-' + idx);
  var siblingBtns = row ? row.parentElement.querySelectorAll('button') : [btn];
  siblingBtns.forEach(function(b) { b.disabled = true; });
  var orig = btn.textContent;
  btn.textContent = 'Live zetten...';
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid + '/apply-variant?variant_index=' + idx, { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) {
      // Toon de échte fout (reden + detail), niet een afgekapte 'Fout: ...'
      var msg = res.reden || res.detail || res.message || 'mislukt';
      if (res.detail) msg = res.reden + ' — ' + res.detail;
      throw new Error(msg);
    }
    btn.textContent = 'Live ✓';
    btn.style.background = 'var(--green)';
    var card = document.getElementById('opt-' + sid);
    if (card) { card.style.opacity = '0.5'; }
  } catch (e) {
    // Volledige foutmelding, niet afgekapt op 40 tekens.
    btn.textContent = 'Fout';
    btn.title = (e.message || '').slice(0, 300);
    siblingBtns.forEach(function(b) { b.disabled = false; });
    if (btn.parentElement) {
      var note = document.createElement('div');
      note.style.cssText = 'font-size:10px;color:var(--danger-fg);margin-top:4px';
      note.textContent = (e.message || '').slice(0, 200);
      btn.parentElement.appendChild(note);
    }
    setTimeout(function() { btn.textContent = orig; }, 6000);
  }
}

async function runOptimizerScan(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Scannen... (±30s)';
  try {
    var resp = await fetch('/api/seo-optimizer/' + encodeURIComponent(currentProject) + '/scan', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'Scan mislukt');
    var c = res.counts || {};
    btn.textContent = ((c.internal_link || 0) + (c.ctr || 0) + (c.refresh || 0)) + ' nieuwe kansen';
    setTimeout(function() { var el = document.getElementById('tab-content'); if (el && currentTab === 'Optimalisatie') renderOptimalisatieTab(el); }, 900);
  } catch (e) {
    btn.textContent = 'Fout: ' + e.message.slice(0, 40);
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 3500);
  }
}

async function optSuggestionAction(sid, status, btn) {
  btn.disabled = true;
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: status })
    });
    if (!resp.ok) throw new Error('mislukt');
    var card = document.getElementById('opt-' + sid);
    if (card) { card.style.opacity = '0.35'; card.style.pointerEvents = 'none'; }
  } catch (e) { btn.disabled = false; }
}

async function optGenerateVariants(sid, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Schrijven...';
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid + '/ctr-variants', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'mislukt');
    var box = document.getElementById('opt-variants-' + sid);
    if (box) box.innerHTML = renderOptVariants({ variants: res.variants }, sid);
    btn.textContent = '3 varianten';
  } catch (e) {
    btn.textContent = 'Fout';
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 3000);
  }
}

async function optRefresh(sid, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Agent schrijft... (±1 min)';
  try {
    var resp = await fetch('/api/seo-optimizer/suggestions/' + sid + '/refresh', { method: 'POST' });
    var res = await resp.json();
    if (!resp.ok) throw new Error(res.detail || 'mislukt');
    btn.textContent = 'In Wachtrij';
    btn.onclick = function() { switchView('Wachtrij'); };
    btn.style.background = 'var(--green)';
  } catch (e) {
    btn.textContent = 'Fout: ' + (e.message || '').slice(0, 30);
    setTimeout(function() { btn.disabled = false; btn.textContent = orig; }, 4000);
  }
}

// ═══════════════════════════════════════════════════════════════════
//  MEMORY GALAXY — 3D sterrenkaart van de Obsidian vault
//  Elke notitie = een ster · wikilinks = verbindingen
//  Slepen = draaien · scrollen = zoomen · klik = detail · dubbelklik = pauze
// ═══════════════════════════════════════════════════════════════════

// Gevalideerd categorisch palet voor donkere achtergrond (#0a0e1c)
var GALAXY_PALETTE = ['#3987e5', '#199e70', '#c98500', '#008300', '#9085e9', '#e66767', '#d55181', '#d95926'];
var GALAXY_OTHER_COLOR = '#8b93a7';
var GALAXY_MAX_GROUPS = 8;

var _galaxy = null; // actieve galaxy-state (één tegelijk)

async function renderMemoryGalaxy(el) {
  if (_galaxy && _galaxy.raf) { cancelAnimationFrame(_galaxy.raf); _galaxy = null; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Sterrenkaart laden...</p></div>';

  var data;
  try {
    var resp = await fetch('/api/infinite-context/graph');
    data = await resp.json();
  } catch (e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  if (!data.nodes || !data.nodes.length) {
    el.innerHTML = '<div class="empty-state"><p style="margin-bottom:8px">Geen notities gevonden.</p>' +
      '<p style="font-size:12px;color:var(--text-muted)">Stel OBSIDIAN_VAULT_PATH in .env in om je vault als sterrenkaart te zien.</p></div>';
    return;
  }

  // ── Groepen → kleuren (top-groepen krijgen een eigen kleur, rest = Overig)
  var groupColor = {};
  var legendGroups = [];
  var groupCounts = {};
  data.nodes.forEach(function(n) { groupCounts[n.group] = (groupCounts[n.group] || 0) + 1; });
  (data.groups || []).forEach(function(g, i) {
    if (i < GALAXY_MAX_GROUPS) { groupColor[g] = GALAXY_PALETTE[i]; legendGroups.push({ name: g, color: GALAXY_PALETTE[i], count: groupCounts[g] || 0 }); }
    else { groupColor[g] = GALAXY_OTHER_COLOR; }
  });
  var otherCount = data.nodes.filter(function(n) { return groupColor[n.group] === GALAXY_OTHER_COLOR; }).length;
  if (otherCount) legendGroups.push({ name: 'Overig', color: GALAXY_OTHER_COLOR, count: otherCount });

  // ── HTML skelet: canvas + overlays
  el.innerHTML =
    '<div id="galaxy-card" style="position:relative;background:#0a0e1c;border-radius:14px;overflow:hidden;height:calc(100vh - 210px);min-height:520px;box-shadow:0 4px 24px rgba(2,6,23,.35)">' +
      '<canvas id="galaxy-canvas" style="position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab"></canvas>' +
      // Titelblok
      '<div style="position:absolute;top:16px;left:18px;pointer-events:none;user-select:none">' +
        '<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:#cbd5e1">MEMORY GALAXY</div>' +
        '<div style="font-size:11px;color:#64748b;margin-top:3px">' +
          (data.sampled
            ? data.note_count + ' van ' + data.total_note_count + ' sterren getoond (meest verbonden + recent) \u{B7} ' + data.link_count + ' links'
            : data.note_count + ' sterren \u{B7} ' + data.link_count + ' links') +
        '</div>' +
        '<div style="font-size:10px;color:#475569;margin-top:2px">sleep om te draaien \u{B7} scroll om te zoomen \u{B7} klik een ster \u{B7} dubbelklik pauzeert de vlucht</div>' +
        '<div style="font-size:10px;color:#475569">feller &amp; witter = recenter bijgewerkt</div>' +
      '</div>' +
      // Zoekveld
      '<div style="position:absolute;top:14px;right:16px;width:260px">' +
        '<input id="galaxy-search" type="text" placeholder="Zoek in ' + (data.total_note_count || data.note_count) + ' notities..." autocomplete="off" ' +
          'style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#e2e8f0;font-size:12px;outline:none" />' +
        '<div id="galaxy-search-results" style="display:none;margin-top:6px;max-height:300px;overflow-y:auto;background:rgba(15,23,42,.95);border:1px solid #1e293b;border-radius:8px"></div>' +
      '</div>' +
      // Legenda (direct gelabeld — kleur draagt nooit alleen betekenis)
      '<div id="galaxy-legend" style="position:absolute;left:18px;bottom:14px;display:flex;flex-wrap:wrap;gap:6px;max-width:55%">' +
        legendGroups.map(function(g) {
          return '<button class="galaxy-legend-chip" data-group="' + escHtml(g.name) + '" onclick="galaxyToggleGroup(this)" ' +
            'style="display:flex;align-items:center;gap:5px;padding:3px 9px;border-radius:99px;border:1px solid #1e293b;background:rgba(15,23,42,.7);color:#94a3b8;font-size:10px;cursor:pointer">' +
            '<span style="width:7px;height:7px;border-radius:99px;background:' + g.color + ';display:inline-block"></span>' +
            escHtml(g.name) + ' <span style="color:#475569">' + g.count + '</span></button>';
        }).join('') +
      '</div>' +
      // Besturing
      '<div style="position:absolute;right:16px;bottom:14px;display:flex;gap:6px">' +
        '<button id="galaxy-flight-btn" onclick="galaxyToggleFlight()" title="Vlucht pauzeren/hervatten" style="padding:6px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#94a3b8;font-size:11px;cursor:pointer">Pauze</button>' +
        '<button onclick="galaxyResetView()" title="Beeld terugzetten" style="padding:6px 12px;border-radius:8px;border:1px solid #1e293b;background:rgba(15,23,42,.85);color:#94a3b8;font-size:11px;cursor:pointer">Reset</button>' +
      '</div>' +
      // Tooltip + detailpaneel
      '<div id="galaxy-tooltip" style="display:none;position:absolute;pointer-events:none;background:rgba(15,23,42,.95);border:1px solid #334155;border-radius:6px;padding:5px 10px;font-size:11px;color:#e2e8f0;z-index:5;max-width:260px"></div>' +
      '<div id="galaxy-detail" style="display:none;position:absolute;top:60px;right:16px;bottom:52px;width:300px;background:rgba(10,14,28,.95);border:1px solid #1e293b;border-radius:12px;padding:14px;overflow-y:auto;z-index:6"></div>' +
    '</div>';

  // ── State opbouwen
  var canvas = document.getElementById('galaxy-canvas');
  var g = {
    nodes: data.nodes, links: data.links, groupColor: groupColor,
    canvas: canvas, ctx: canvas.getContext('2d'),
    yaw: 0.4, pitch: 0.18, zoom: 1, autoRotate: true,
    targetYaw: null, targetPitch: null, targetZoom: null,
    dragging: false, lastX: 0, lastY: 0, velYaw: 0, velPitch: 0,
    hover: -1, selected: -1, dimGroups: {}, searchHits: null,
    simIter: 0, simMax: 220, raf: null, sprites: {}, bgStars: []
  };
  _galaxy = g;

  // Startposities: elke groep krijgt een clusterkern op een bol, sterren eromheen
  var R = 300;
  var centers = {};
  var gi = 0, gTotal = (data.groups || []).length || 1;
  (data.groups || []).forEach(function(grp) {
    var phi = Math.acos(1 - 2 * (gi + 0.5) / gTotal);
    var theta = Math.PI * (1 + Math.sqrt(5)) * gi; // fibonacci-verdeling
    centers[grp] = [R * 0.55 * Math.sin(phi) * Math.cos(theta), R * 0.55 * Math.sin(phi) * Math.sin(theta), R * 0.55 * Math.cos(phi)];
    gi++;
  });
  g.centers = centers;
  g.nodes.forEach(function(n) {
    var c = centers[n.group] || [0, 0, 0];
    n.x = c[0] + (Math.random() - 0.5) * 150;
    n.y = c[1] + (Math.random() - 0.5) * 150;
    n.z = c[2] + (Math.random() - 0.5) * 150;
    n.vx = 0; n.vy = 0; n.vz = 0;
    // Helderheid: recent bijgewerkt = fel wit, oud = gedimd
    n.bright = Math.max(0.35, Math.min(1, 1.15 - (n.days || 0) / 90));
    n.r = 1.6 + Math.sqrt(n.deg || 0) * 1.1; // straal ∝ aantal verbindingen
  });

  // Achtergrondsterretjes (puur decoratief, statisch)
  for (var b = 0; b < 130; b++) g.bgStars.push([Math.random(), Math.random(), Math.random() * 0.8 + 0.2]);

  // Labels voor de belangrijkste knopen (hoogste degree)
  var byDeg = g.nodes.map(function(n, i) { return [n.deg, i]; }).sort(function(a, b2) { return b2[0] - a[0]; });
  g.labeled = {};
  for (var li = 0; li < Math.min(14, byDeg.length); li++) if (byDeg[li][0] > 0) g.labeled[byDeg[li][1]] = true;

  galaxyBindEvents(g);
  galaxyLoop(g);
}

// ── Fysica: force-directed layout in 3D (draait de eerste seconden warm)
function galaxySimStep(g) {
  var nodes = g.nodes, links = g.links, n = nodes.length;
  var i, j, a, b2, dx, dy, dz, d2, d, f;
  // Afstoting (O(n²), maar n≈500 dus prima)
  for (i = 0; i < n; i++) {
    a = nodes[i];
    for (j = i + 1; j < n; j++) {
      b2 = nodes[j];
      dx = a.x - b2.x; dy = a.y - b2.y; dz = a.z - b2.z;
      d2 = dx * dx + dy * dy + dz * dz + 0.01;
      if (d2 > 22500) continue; // >150 eenheden: verwaarloosbaar
      f = 260 / d2;
      dx *= f; dy *= f; dz *= f;
      a.vx += dx; a.vy += dy; a.vz += dz;
      b2.vx -= dx; b2.vy -= dy; b2.vz -= dz;
    }
  }
  // Veren langs links
  for (i = 0; i < links.length; i++) {
    a = nodes[links[i][0]]; b2 = nodes[links[i][1]];
    dx = b2.x - a.x; dy = b2.y - a.y; dz = b2.z - a.z;
    d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
    f = (d - 55) * 0.012 / d;
    dx *= f; dy *= f; dz *= f;
    a.vx += dx; a.vy += dy; a.vz += dz;
    b2.vx -= dx; b2.vy -= dy; b2.vz -= dz;
  }
  // Zwaartekracht naar clusterkern + integratie
  for (i = 0; i < n; i++) {
    a = nodes[i];
    var c = g.centers[a.group];
    if (c) { a.vx += (c[0] - a.x) * 0.004; a.vy += (c[1] - a.y) * 0.004; a.vz += (c[2] - a.z) * 0.004; }
    a.vx *= 0.82; a.vy *= 0.82; a.vz *= 0.82;
    a.x += a.vx; a.y += a.vy; a.z += a.vz;
  }
}

// ── Glow-sprite per kleur (gecachet — veel sneller dan gradients per frame)
function galaxySprite(g, color) {
  if (g.sprites[color]) return g.sprites[color];
  var s = document.createElement('canvas'); s.width = 64; s.height = 64;
  var c = s.getContext('2d');
  var grad = c.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, '#ffffff');
  grad.addColorStop(0.25, color);
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  c.fillStyle = grad; c.fillRect(0, 0, 64, 64);
  g.sprites[color] = s;
  return s;
}

function galaxyProject(g, n, w, h) {
  var cy = Math.cos(g.yaw), sy = Math.sin(g.yaw), cp = Math.cos(g.pitch), sp = Math.sin(g.pitch);
  var x1 = n.x * cy + n.z * sy, z1 = -n.x * sy + n.z * cy;
  var y1 = n.y * cp - z1 * sp, z2 = n.y * sp + z1 * cp;
  var scale = 900 / (900 + z2);
  return [w / 2 + x1 * scale * g.zoom, h / 2 + y1 * scale * g.zoom, scale, z2];
}

function galaxyLoop(g) {
  if (!g.canvas.isConnected || _galaxy !== g) { g.raf = null; return; }
  var canvas = g.canvas, ctx = g.ctx;
  var dpr = window.devicePixelRatio || 1;
  var w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Fysica warmdraaien (6 stappen per frame tot de layout stabiel is)
  if (g.simIter < g.simMax) { for (var s = 0; s < 6; s++) galaxySimStep(g); g.simIter += 6; }

  // Camera: trage vlucht + soepel naar doel vliegen + inertie
  if (g.targetYaw !== null) {
    g.yaw += (g.targetYaw - g.yaw) * 0.08; g.pitch += (g.targetPitch - g.pitch) * 0.08;
    if (g.targetZoom !== null) g.zoom += (g.targetZoom - g.zoom) * 0.08;
    if (Math.abs(g.targetYaw - g.yaw) < 0.002) { g.targetYaw = null; g.targetPitch = null; g.targetZoom = null; }
  } else if (g.autoRotate && !g.dragging) {
    g.yaw += 0.0016;
  }
  if (!g.dragging && g.targetYaw === null) { g.yaw += g.velYaw; g.pitch += g.velPitch; g.velYaw *= 0.92; g.velPitch *= 0.92; }
  g.pitch = Math.max(-1.4, Math.min(1.4, g.pitch));

  // ── Achtergrond
  ctx.fillStyle = '#0a0e1c'; ctx.fillRect(0, 0, w, h);
  var bg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
  bg.addColorStop(0, 'rgba(49,46,129,0.16)'); bg.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(148,163,184,0.35)';
  for (var bs = 0; bs < g.bgStars.length; bs++) {
    var st = g.bgStars[bs];
    ctx.fillRect(st[0] * w, st[1] * h, st[2], st[2]);
  }

  // ── Projecteer alle sterren
  var proj = new Array(g.nodes.length);
  for (var i = 0; i < g.nodes.length; i++) proj[i] = galaxyProject(g, g.nodes[i], w, h);

  var isDimmed = function(idx) {
    var n = g.nodes[idx];
    if (g.dimGroups[n.group]) return true;
    if (g.searchHits && !g.searchHits[idx]) return true;
    return false;
  };

  // ── Links (diepte bepaalt zichtbaarheid)
  ctx.lineWidth = 0.5;
  for (var l = 0; l < g.links.length; l++) {
    var a = g.links[l][0], b = g.links[l][1];
    var pa = proj[a], pb = proj[b];
    var dim = isDimmed(a) || isDimmed(b);
    var sel = g.selected === a || g.selected === b;
    var alpha = sel ? 0.5 : (dim ? 0.02 : 0.09 * Math.min(pa[2], pb[2]));
    ctx.strokeStyle = sel ? 'rgba(165,180,252,' + alpha + ')' : 'rgba(148,163,184,' + alpha + ')';
    ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
  }

  // ── Sterren (glow-sprites, additief gemengd)
  ctx.globalCompositeOperation = 'lighter';
  for (var k = 0; k < g.nodes.length; k++) {
    var n = g.nodes[k], p = proj[k];
    if (p[0] < -30 || p[0] > w + 30 || p[1] < -30 || p[1] > h + 30) continue;
    var color = g.groupColor[n.group] || GALAXY_OTHER_COLOR;
    var size = (n.r * 3.2 + 3) * p[2] * Math.sqrt(g.zoom);
    var alpha2 = n.bright * p[2];
    if (isDimmed(k)) alpha2 *= 0.08;
    if (g.searchHits && g.searchHits[k]) { alpha2 = Math.min(1, alpha2 * 1.6); size *= 1.25; }
    if (k === g.hover || k === g.selected) { alpha2 = 1; size *= 1.35; }
    ctx.globalAlpha = Math.min(1, alpha2);
    ctx.drawImage(galaxySprite(g, color), p[0] - size / 2, p[1] - size / 2, size, size);
  }
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;

  // ── Selectiering + labels van belangrijkste knopen
  if (g.selected >= 0) {
    var ps = proj[g.selected];
    ctx.strokeStyle = 'rgba(199,210,254,0.9)'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(ps[0], ps[1], 11 * Math.sqrt(g.zoom), 0, Math.PI * 2); ctx.stroke();
  }
  ctx.font = '10px Inter, sans-serif'; ctx.textAlign = 'left';
  for (var lb in g.labeled) {
    var lidx = +lb;
    if (isDimmed(lidx)) continue;
    var pl = proj[lidx];
    if (pl[3] > 150 || pl[0] < 0 || pl[0] > w) continue; // alleen labels vooraan
    ctx.fillStyle = 'rgba(203,213,225,' + (0.55 * pl[2]) + ')';
    ctx.fillText(g.nodes[lidx].name.slice(0, 26), pl[0] + 8, pl[1] + 3);
  }

  g.proj = proj;
  g.raf = requestAnimationFrame(function() { galaxyLoop(g); });
}

// ── Interactie ──────────────────────────────────────────────────────
function galaxyNodeAt(g, mx, my) {
  if (!g.proj) return -1;
  var best = -1, bestD = 144; // 12px zoekradius
  for (var i = 0; i < g.proj.length; i++) {
    var p = g.proj[i];
    var dx = p[0] - mx, dy = p[1] - my;
    var d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

function galaxyBindEvents(g) {
  var canvas = g.canvas;
  var moved = false;
  canvas.addEventListener('mousedown', function(e) {
    g.dragging = true; moved = false; g.lastX = e.offsetX; g.lastY = e.offsetY; canvas.style.cursor = 'grabbing';
  });
  canvas.addEventListener('mousemove', function(e) {
    if (g.dragging) {
      var dx = e.offsetX - g.lastX, dy = e.offsetY - g.lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      g.yaw += dx * 0.005; g.pitch += dy * 0.005;
      g.velYaw = dx * 0.0012; g.velPitch = dy * 0.0012;
      g.targetYaw = null; g.targetPitch = null; g.targetZoom = null;
      g.lastX = e.offsetX; g.lastY = e.offsetY;
      return;
    }
    var idx = galaxyNodeAt(g, e.offsetX, e.offsetY);
    g.hover = idx;
    canvas.style.cursor = idx >= 0 ? 'pointer' : 'grab';
    var tip = document.getElementById('galaxy-tooltip');
    if (!tip) return;
    if (idx >= 0) {
      var n = g.nodes[idx];
      tip.innerHTML = '<strong>' + escHtml(n.name) + '</strong><br><span style="color:#94a3b8">' + escHtml(n.group) + ' \u{B7} ' + n.deg + ' links \u{B7} ' + Math.round(n.days) + 'd geleden</span>';
      tip.style.display = 'block';
      tip.style.left = Math.min(e.offsetX + 14, canvas.clientWidth - 270) + 'px';
      tip.style.top = (e.offsetY + 14) + 'px';
    } else tip.style.display = 'none';
  });
  window.addEventListener('mouseup', function() { if (g.dragging) { g.dragging = false; if (g.canvas.isConnected) g.canvas.style.cursor = 'grab'; } });
  canvas.addEventListener('click', function(e) {
    if (moved) return;
    var idx = galaxyNodeAt(g, e.offsetX, e.offsetY);
    if (idx >= 0) galaxySelect(idx);
    else { g.selected = -1; var d = document.getElementById('galaxy-detail'); if (d) d.style.display = 'none'; }
  });
  canvas.addEventListener('dblclick', function() { galaxyToggleFlight(); });
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    g.zoom = Math.max(0.3, Math.min(5, g.zoom * (e.deltaY > 0 ? 0.92 : 1.09)));
  }, { passive: false });

  // Zoeken: direct op naam, na een korte pauze ook op inhoud (backend)
  var input = document.getElementById('galaxy-search');
  var resBox = document.getElementById('galaxy-search-results');
  var debounce = null;
  input.addEventListener('input', function() {
    var q = input.value.trim().toLowerCase();
    if (debounce) clearTimeout(debounce);
    if (!q) { g.searchHits = null; resBox.style.display = 'none'; return; }
    // Naam-matches: highlight in de galaxy + lijst
    var hits = {}, list = [];
    g.nodes.forEach(function(n, i) { if (n.name.toLowerCase().indexOf(q) >= 0) { hits[i] = true; list.push(i); } });
    g.searchHits = hits;
    var html = list.slice(0, 12).map(function(i) {
      var n = g.nodes[i];
      return '<div onclick="galaxySelect(' + i + ')" style="padding:7px 10px;font-size:11px;color:#e2e8f0;cursor:pointer;border-bottom:1px solid #1e293b">' + escHtml(n.name) +
        ' <span style="color:#64748b">\u{B7} ' + escHtml(n.group) + '</span></div>';
    }).join('');
    resBox.innerHTML = html || '<div style="padding:8px 10px;font-size:11px;color:#64748b">Geen naam-matches \u{2014} inhoud doorzoeken...</div>';
    resBox.style.display = 'block';
    // Inhoudelijke zoekresultaten erbij (debounced)
    debounce = setTimeout(function() {
      fetch('/api/infinite-context/search?q=' + encodeURIComponent(input.value.trim())).then(function(r) { return r.json(); }).then(function(sr) {
        if (input.value.trim().toLowerCase() !== q || !sr.results || !sr.results.length) return;
        var extra = '<div style="padding:5px 10px;font-size:9px;letter-spacing:1px;color:#64748b;border-bottom:1px solid #1e293b">OP INHOUD</div>';
        sr.results.slice(0, 8).forEach(function(r2) {
          var i = g.nodes.findIndex(function(n) { return n.id === (r2.path || '').replace(/\\/g, '/'); });
          if (i >= 0) g.searchHits[i] = true;
          extra += '<div onclick="' + (i >= 0 ? 'galaxySelect(' + i + ')' : '') + '" style="padding:7px 10px;font-size:11px;color:#cbd5e1;cursor:pointer;border-bottom:1px solid #1e293b">' +
            escHtml(r2.file || '') + '<div style="color:#64748b;font-size:10px;margin-top:2px">' + escHtml((r2.snippet || '').slice(0, 90)) + '...</div></div>';
        });
        resBox.innerHTML = (html || '') + extra;
      }).catch(function() {});
    }, 350);
  });
}

function galaxySelect(idx) {
  var g = _galaxy; if (!g) return;
  g.selected = idx;
  var n = g.nodes[idx];
  var box = document.getElementById('galaxy-search-results');
  if (box) box.style.display = 'none';
  // Vlieg ernaartoe: draai de camera zó dat de ster in het midden vooraan komt
  // (yaw zodanig dat x'=0 en de ster vóór de camera staat, pitch zodat y'=0)
  var rxz = Math.sqrt(n.x * n.x + n.z * n.z) || 0.01;
  var ty = Math.atan2(-n.x, n.z) + Math.PI;
  // Kies de draairichting met de kortste weg vanaf de huidige yaw
  while (ty - g.yaw > Math.PI) ty -= 2 * Math.PI;
  while (ty - g.yaw < -Math.PI) ty += 2 * Math.PI;
  g.targetYaw = ty;
  g.targetPitch = Math.max(-1.2, Math.min(1.2, Math.atan2(-n.y, rxz)));
  g.targetZoom = Math.max(g.zoom, 1.6);
  // Pauzeer de vlucht zodat de ster in beeld blijft (hervatten kan met ▶)
  if (g.autoRotate) galaxyToggleFlight();

  var d = document.getElementById('galaxy-detail');
  if (!d) return;
  var color = g.groupColor[n.group] || GALAXY_OTHER_COLOR;
  d.style.display = 'block';
  d.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">' +
    '<div style="font-size:13px;font-weight:700;color:#f1f5f9;line-height:1.4">' + escHtml(n.name) + '</div>' +
    '<button onclick="document.getElementById(\'galaxy-detail\').style.display=\'none\';if(_galaxy)_galaxy.selected=-1" style="background:none;border:none;color:#64748b;font-size:15px;cursor:pointer;line-height:1">\u{2715}</button></div>' +
    '<div style="display:flex;align-items:center;gap:6px;margin:7px 0"><span style="width:8px;height:8px;border-radius:99px;background:' + color + '"></span>' +
    '<span style="font-size:10px;color:#94a3b8">' + escHtml(n.group) + ' \u{B7} ' + n.deg + ' links \u{B7} ' + Math.round(n.days) + ' dagen geleden</span></div>' +
    '<div id="galaxy-note-body" style="font-size:11px;color:#94a3b8">Laden...</div>';

  fetch('/api/infinite-context/note?path=' + encodeURIComponent(n.id)).then(function(r) { return r.json(); }).then(function(note) {
    var body = document.getElementById('galaxy-note-body');
    if (!body || !_galaxy || _galaxy.selected !== idx) return;
    var linkChip = function(name) {
      var i = _galaxy.nodes.findIndex(function(x) { return x.name === name; });
      return '<span ' + (i >= 0 ? 'onclick="galaxySelect(' + i + ')" style="cursor:pointer;color:#a5b4fc;"' : 'style="color:#64748b"') +
        ' class="galaxy-link-chip">[[' + escHtml(name) + ']]</span>';
    };
    var html = '';
    if (note.backlinks && note.backlinks.length) html += '<div style="margin-bottom:8px"><div style="font-size:9px;letter-spacing:1px;color:#64748b;margin-bottom:4px">VERBONDEN MET</div><div style="display:flex;flex-wrap:wrap;gap:4px;font-size:10px">' + note.backlinks.map(linkChip).join(' ') + '</div></div>';
    html += '<div style="font-size:9px;letter-spacing:1px;color:#64748b;margin-bottom:4px">INHOUD \u{B7} ' + escHtml(note.modified || '') + '</div>' +
      '<pre style="white-space:pre-wrap;font-size:11px;line-height:1.6;color:#cbd5e1;font-family:Inter,sans-serif;margin:0">' + escHtml((note.content || '').slice(0, 2500)) + (note.truncated || (note.content || '').length > 2500 ? '\n\u{2026}' : '') + '</pre>';
    body.innerHTML = html;
  }).catch(function() {
    var body = document.getElementById('galaxy-note-body');
    if (body) body.innerHTML = '<span style="color:var(--red)">Kon notitie niet laden</span>';
  });
}

function galaxyToggleFlight() {
  var g = _galaxy; if (!g) return;
  g.autoRotate = !g.autoRotate;
  var btn = document.getElementById('galaxy-flight-btn');
  if (btn) btn.innerHTML = g.autoRotate ? 'Pauze' : 'Vlucht';
}
function galaxyResetView() {
  var g = _galaxy; if (!g) return;
  g.targetYaw = 0.4; g.targetPitch = 0.18; g.targetZoom = 1;
  g.selected = -1; g.searchHits = null; g.dimGroups = {};
  var d = document.getElementById('galaxy-detail'); if (d) d.style.display = 'none';
  var s = document.getElementById('galaxy-search'); if (s) s.value = '';
  var r = document.getElementById('galaxy-search-results'); if (r) r.style.display = 'none';
  document.querySelectorAll('.galaxy-legend-chip').forEach(function(c) { c.style.opacity = '1'; });
}
function galaxyToggleGroup(chip) {
  var g = _galaxy; if (!g) return;
  var grp = chip.getAttribute('data-group');
  // 'Overig' = alle groepen zonder eigen kleur
  var targets = grp === 'Overig'
    ? Object.keys(g.nodes.reduce(function(acc, n) { if ((g.groupColor[n.group] || GALAXY_OTHER_COLOR) === GALAXY_OTHER_COLOR) acc[n.group] = 1; return acc; }, {}))
    : [grp];
  var nowDimmed = !g.dimGroups[targets[0]];
  targets.forEach(function(t) { if (nowDimmed) g.dimGroups[t] = true; else delete g.dimGroups[t]; });
  chip.style.opacity = nowDimmed ? '0.35' : '1';
}

