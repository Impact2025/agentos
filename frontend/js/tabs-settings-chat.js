// ── Impact OS — tabs: Instellingen, Chat, Finance Expert + INIT
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// ═══════════════════════════════════════════════════════════════════
//  INSTELLINGEN — MCP Manager & Agent Profielen
// ═══════════════════════════════════════════════════════════════════
async function renderInstellingenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Instellingen laden...</p></div>';
  try {
    var [profilesResp, goalsResp] = await Promise.all([
      fetch('/api/agents'),
      fetch('/api/goals?limit=1'),
    ]);
    var profiles = await profilesResp.json();
    var skillsEndp = await fetch('/api/strategist/control-room');
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<h3 style="font-size:15px;font-weight:700;margin-bottom:16px">Instellingen &amp; Beheer</h3>';

  if (typeof domainOn === 'undefined' || domainOn('outlook_legacy')) html += renderOutlookSection();
  if (typeof domainOn === 'undefined' || domainOn('calendar')) html += await renderAgendaSettings();
  if (typeof renderTourSettings === 'function') html += renderTourSettings();

  html += await renderSitePublishSettings();
  html += await renderKennisbankSettings();

  // ── Agent Profielen tabel ──
  html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Agent Profielen (' + (profiles||[]).length + ')</h4>' +
    '<table class="data-table"><thead><tr><th>Naam</th><th>Model</th><th>MCP Servers</th><th>Aangemaakt</th></tr></thead><tbody>';
  (profiles||[]).forEach(function(p){
    var mcpStr = (p.mcp_servers||[]).join(', ') || '-';
    var created = (p.created_at||'').slice(0,10);
    html += '<tr><td><span style="font-weight:600">' + escHtml(p.name) + '</span></td>' +
      '<td style="font-size:11px;color:var(--text-dim)">' + escHtml(p.model||'-') + '</td>' +
      '<td style="font-size:11px;color:var(--text-dim)">' + escHtml(mcpStr) + '</td>' +
      '<td style="font-size:11px;color:var(--text-muted)">' + escHtml(created) + '</td></tr>';
  });
  html += '</tbody></table></div>';

  // ── Skills overzicht ──
  var skills = [
    ['research', 'SEO Specialist', 'Onderzoek'],
    ['content-writer', 'Content Writer', 'Schrijven'],
    ['content-editor', 'Content Editor', 'Eindredactie'],
    ['content-judge', 'Content Judge', 'Beoordeling'],
    ['seo', 'SEO Specialist', 'SEO'],
    ['video-builder', 'Video Creator', 'Video script'],
    ['video-director', 'Video Director', 'Video regie'],
    ['outreach', 'Outreach Agent', 'Lead generatie'],
    ['publisher', 'Content Writer', 'Publiceren'],
    ['analyst', 'SEO Specialist', 'Analyse'],
    ['designer', 'Content Writer', 'Design'],
  ];
  html += '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Skills &#8594; Profiel mapping</h4>' +
    '<table class="data-table"><thead><tr><th>Skill</th><th>Profiel</th><th>Type</th></tr></thead><tbody>';
  skills.forEach(function(s){
    html += '<tr><td><code style="font-size:11px;padding:1px 5px;background:#f1f5f9;border-radius:3px">' + escHtml(s[0]) + '</code></td>' +
      '<td><span class="badge badge-draft">' + escHtml(s[1]) + '</span></td>' +
      '<td style="color:#64748b;font-size:11px">' + escHtml(s[2]) + '</td></tr>';
  });
  html += '</tbody></table></div>';

  // ── Live model-swap (zonder herstart) ──
  html += '<div class="section-card" id="model-swap-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Model-swap (live, geen herstart)</h4>' +
    '<p style="font-size:11px;color:var(--text-dim);margin-bottom:10px">Wissel het actieve brein per direct. Bulk = snel/tool-werk, Smart = denkwerk. Keuze geldt voor nieuwe chat, goals &amp; delegaties.</p>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">' +
      '<label style="display:block"><span style="display:block;font-size:11px;color:var(--text-dim);margin-bottom:2px">Bulk-model (flash)</span>' +
      '<select id="model-bulk" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box"></select></label>' +
      '<label style="display:block"><span style="display:block;font-size:11px;color:var(--text-dim);margin-bottom:2px">Smart-model (denkwerk)</span>' +
      '<select id="model-smart" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box"></select></label>' +
    '</div>' +
    '<button onclick="saveModelSwap(this)" class="btn btn-primary btn-sm">Modellen toepassen</button>' +
    '<span id="model-swap-status" style="font-size:11px;color:var(--text-dim);margin-left:8px"></span>' +
    '</div>';

  // ── Systeeminfo ──
  html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">API Overzicht</h4>' +
    '<table class="data-table"><thead><tr><th>Endpoint</th><th>Method</th><th>Omschrijving</th></tr></thead><tbody>' +
    '<tr><td><code>/api/agents</code></td><td>GET</td><td>Alle profielen</td></tr>' +
    '<tr><td><code>/api/agents</code></td><td>POST</td><td>Nieuw profiel aanmaken</td></tr>' +
    '<tr><td><code>/api/agents/{id}</code></td><td>PATCH</td><td>Profiel bijwerken</td></tr>' +
    '<tr><td><code>/api/agents/{id}</code></td><td>DELETE</td><td>Profiel verwijderen</td></tr>' +
    '<tr><td><code>/api/goals</code></td><td>GET</td><td>Alle doelen</td></tr>' +
    '<tr><td><code>/api/strategist/control-room</code></td><td>GET</td><td>Control Room status</td></tr>' +
    '<tr><td><code>/api/strategist/analyse</code></td><td>POST</td><td>Strategist AI-analyse</td></tr>' +
    '<tr><td><code>/api/infinite-context/status</code></td><td>GET</td><td>ICE status</td></tr>' +
    '</tbody></table></div>';

  el.innerHTML = html;
  if (document.getElementById('outlook-card')) outlookRefreshStatus();
  loadModelSwap();
}

// ── Live model-swap: laad huidige + presets, vul de dropdowns ──────────────
async function loadModelSwap() {
  var card = document.getElementById('model-swap-card');
  if (!card) return;
  try {
    var data = await (await fetch('/api/config/models')).json();
    var bulk = document.getElementById('model-bulk');
    var smart = document.getElementById('model-smart');
    if (!bulk || !smart) return;
    // Bouw opties uit presets; zorg dat het huidige model er altijd bij staat.
    var bulkOpts = data.presets.filter(function (p) { return p.kind === 'bulk'; })
      .map(function (p) { return p.id; });
    var smartOpts = data.presets.filter(function (p) { return p.kind === 'smart'; })
      .map(function (p) { return p.id; });
    if (data.current && data.current.bulk && bulkOpts.indexOf(data.current.bulk) < 0) bulkOpts.push(data.current.bulk);
    if (data.current && data.current.smart && smartOpts.indexOf(data.current.smart) < 0) smartOpts.push(data.current.smart);
    bulk.innerHTML = bulkOpts.map(function (m) { return '<option value="' + escHtml(m) + '">' + escHtml(m) + '</option>'; }).join('');
    smart.innerHTML = smartOpts.map(function (m) { return '<option value="' + escHtml(m) + '">' + escHtml(m) + '</option>'; }).join('');
    if (data.current) { bulk.value = data.current.bulk; smart.value = data.current.smart; }
  } catch (e) {
    var st = document.getElementById('model-swap-status');
    if (st) st.textContent = 'Kon modellen niet laden: ' + e.message;
  }
}

async function saveModelSwap(btn) {
  var bulk = document.getElementById('model-bulk');
  var smart = document.getElementById('model-smart');
  var st = document.getElementById('model-swap-status');
  if (st) st.textContent = 'Bezig…';
  if (btn) { btn.disabled = true; }
  try {
    var r1 = await fetch('/api/config/models', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot: 'bulk', model: bulk.value }),
    });
    var r2 = await fetch('/api/config/models', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot: 'smart', model: smart.value }),
    });
    if (!r1.ok || !r2.ok) throw new Error('swap mislukt');
    var d = await r2.json();
    if (st) st.textContent = 'Toegepast: bulk=' + d.active.bulk + ', smart=' + d.active.smart;
  } catch (e) {
    if (st) st.textContent = 'Fout: ' + e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Agenda koppelen: agenda-ID + lees-agenda's zelf zetten, geen herstart
// nodig (DB-override, zie shared/settings_store.py). ──
async function renderAgendaSettings() {
  var status;
  try { status = await (await fetch('/api/calendar/status')).json(); } catch(e) { return ''; }
  if (status.backend === 'outlook') return ''; // agenda volgt daar de mail-koppeling hierboven
  var reach = status.reachable ? '<span class="pill pill-ok">Verbonden</span> kan de agenda lezen.' :
    (status.configured ? '<span class="pill pill-warn">Niet bereikbaar</span> ' + escHtml(status.error || 'onbekende fout') : '<span class="pill pill-neutral">Niet geconfigureerd</span>');
  return '<div class="section-card" style="margin-bottom:16px" id="agenda-card">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Agenda koppelen (Google)</h4>' +
    (status.client_email ? '<p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">Deel je agenda met dit adres (bewerkrechten): <code style="background:var(--neutral-bg);padding:1px 5px;border-radius:3px">' + escHtml(status.client_email) + '</code></p>' : '') +
    '<div id="agenda-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px">' + reach + '</div>' +
    '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:var(--text-dim);margin-bottom:2px">Agenda-ID (e-mailadres van de gedeelde agenda, of "primary")</span>' +
    '<input id="agenda-calendar-id" value="' + escHtml(status.calendar_id || '') + '" placeholder="jij@voorbeeld.nl" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box" /></label>' +
    '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:var(--text-dim);margin-bottom:2px">Extra lees-agenda\'s voor conflict-detectie (komma-gescheiden, optioneel)</span>' +
    '<input id="agenda-busy-ids" value="' + escHtml((status.busy_calendar_ids || []).join(', ')) + '" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box" /></label>' +
    '<button onclick="saveAgendaSettings(this)" class="btn btn-primary btn-sm">Opslaan &amp; testen</button>' +
    '</div>';
}

async function saveAgendaSettings(btn) {
  var body = {
    calendar_id: document.getElementById('agenda-calendar-id').value.trim(),
    busy_calendar_ids: document.getElementById('agenda-busy-ids').value.trim(),
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/calendar/settings', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    var st = await resp.json();
    var statusEl = document.getElementById('agenda-status');
    if (statusEl) statusEl.innerHTML = st.reachable ? '<span class="pill pill-ok">Verbonden</span> kan de agenda lezen.' :
      '<span class="pill pill-warn">Niet bereikbaar</span> ' + escHtml(st.error || 'onbekende fout') + '. Heb je de agenda al gedeeld?';
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Opslaan & testen'; }
}

// ── Outlook / Microsoft Graph koppel-sectie (device-code flow) ──
function renderOutlookSection() {
  return '<div class="section-card" style="margin-bottom:16px" id="outlook-card">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Outlook / Microsoft Graph &mdash; e-mailverzending</h4>' +
    '<div id="outlook-status" style="font-size:12px;color:var(--text-dim);margin-bottom:8px">Status wordt geladen…</div>' +
    '<div id="outlook-flow" style="display:none;background:var(--neutral-bg);border:1px solid var(--card-border);border-radius:8px;padding:12px;margin-bottom:10px">' +
      '<p style="font-size:12px;margin:0 0 6px">Log in met deze code bij Microsoft:</p>' +
      '<div id="outlook-code" style="font-size:22px;font-weight:800;letter-spacing:2px;color:var(--accent);margin-bottom:6px">••••••••</div>' +
      '<a id="outlook-link" href="#" target="_blank" style="font-size:12px;color:var(--accent)">Open Microsoft login</a>' +
      '<div id="outlook-flow-msg" style="font-size:12px;color:var(--text-dim);margin-top:6px"></div>' +
    '</div>' +
    '<div style="display:flex;gap:8px">' +
      '<button id="outlook-connect-btn" onclick="outlookConnect()" class="btn btn-primary btn-sm">Koppel Outlook-account</button>' +
      '<button id="outlook-logout-btn" onclick="outlookLogout()" class="btn btn-danger-outline btn-sm" style="display:none">Ontkoppelen</button>' +
    '</div>' +
  '</div>';
}

var outlookPollTimer = null;

function outlookRefreshStatus() {
  fetch('/api/outlook/status').then(function(r){ return r.json(); }).then(function(s){
    var el = document.getElementById('outlook-status');
    if (!el) return;
    var connectBtn = document.getElementById('outlook-connect-btn');
    var logoutBtn = document.getElementById('outlook-logout-btn');
    if (!s.configured) {
      el.innerHTML = '<span class="pill pill-neutral">Niet geconfigureerd</span> OUTLOOK_CLIENT_ID ontbreekt in .env.';
      if (connectBtn) connectBtn.style.display = 'none';
      if (logoutBtn) logoutBtn.style.display = 'none';
      return;
    }
    if (s.token_valid) {
      el.innerHTML = '<span class="pill pill-ok">Ingelogd</span> als <b>' + escHtml((s.account && s.account.email) || '') + '</b> — e-mailverzending actief.';
      if (connectBtn) connectBtn.style.display = 'none';
      if (logoutBtn) logoutBtn.style.display = 'inline-block';
    } else if (s.authenticated) {
      el.innerHTML = '<span class="pill pill-warn">Sessie verlopen</span> koppel opnieuw om mail te kunnen versturen.';
      if (connectBtn) connectBtn.style.display = 'inline-block';
      if (logoutBtn) logoutBtn.style.display = 'inline-block';
    } else {
      el.innerHTML = '<span class="pill pill-neutral">Niet gekoppeld</span> koppel je Outlook-account om outreach-mail te versturen.';
      if (connectBtn) connectBtn.style.display = 'inline-block';
      if (logoutBtn) logoutBtn.style.display = 'none';
    }
  }).catch(function(e){
    var el = document.getElementById('outlook-status');
    if (el) el.textContent = 'Status ophalen mislukt: ' + e.message;
  });
}

function outlookConnect() {
  var flowEl = document.getElementById('outlook-flow');
  var codeEl = document.getElementById('outlook-code');
  var linkEl = document.getElementById('outlook-link');
  var msgEl = document.getElementById('outlook-flow-msg');
  fetch('/api/outlook/auth/start', {method:'POST'}).then(function(r){
    if (!r.ok) return r.json().then(function(j){ throw new Error(j.detail || ('HTTP '+r.status)); });
    return r.json();
  }).then(function(flow){
    if (flowEl) flowEl.style.display = 'block';
    if (codeEl) codeEl.textContent = flow.user_code || '—';
    if (linkEl) linkEl.href = flow.verification_uri || 'https://login.microsoft.com/device';
    if (msgEl) msgEl.textContent = 'Wacht op autorisatie… (deze pagina ververst automatisch)';
    if (outlookPollTimer) clearInterval(outlookPollTimer);
    outlookPollTimer = setInterval(outlookPollStatus, 2000);
  }).catch(function(e){
    if (msgEl) msgEl.textContent = 'Kon device-flow niet starten: ' + e.message;
  });
}

function outlookPollStatus() {
  fetch('/api/outlook/auth/status').then(function(r){ return r.json(); }).then(function(st){
    var msgEl = document.getElementById('outlook-flow-msg');
    if (st.status === 'done') {
      if (outlookPollTimer) { clearInterval(outlookPollTimer); outlookPollTimer = null; }
      if (msgEl) msgEl.textContent = 'Ingelogd als ' + (st.email || '') + '.';
      var flowEl = document.getElementById('outlook-flow');
      if (flowEl) setTimeout(function(){ flowEl.style.display = 'none'; }, 1200);
      outlookRefreshStatus();
    } else if (st.status === 'error') {
      if (outlookPollTimer) { clearInterval(outlookPollTimer); outlookPollTimer = null; }
      if (msgEl) msgEl.textContent = 'Fout: ' + (st.error || 'onbekend');
    } else {
      if (msgEl) msgEl.textContent = 'Wacht op autorisatie bij Microsoft…';
    }
  }).catch(function(e){
    if (outlookPollTimer) { clearInterval(outlookPollTimer); outlookPollTimer = null; }
  });
}

function outlookLogout() {
  fetch('/api/outlook/auth', {method:'DELETE'}).then(function(){
    var flowEl = document.getElementById('outlook-flow');
    if (flowEl) flowEl.style.display = 'none';
    outlookRefreshStatus();
  });
}
function _siteField(label, name, value, opts) {
  opts = opts || {};
  var isSecret = !!opts.secret;
  var placeholder = isSecret ? (opts.set ? '•••••••• (ingesteld — laat leeg om te behouden)' : 'niet ingesteld') : (opts.placeholder || '');
  var type = isSecret ? 'password' : (opts.type || 'text');
  return '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:#64748b;margin-bottom:2px">' + label + '</span>' +
    '<input type="' + type + '" data-site-field="' + name + '" value="' + (isSecret ? '' : escHtml(value||'')) + '" placeholder="' + escHtml(placeholder) + '" ' +
    'style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;box-sizing:border-box" /></label>';
}

async function renderSitePublishSettings() {
  var site;
  try {
    var sites = await (await fetch('/api/sites')).json();
    var norm = function(s){return (s||'').toLowerCase().replace(/ /g,'').replace(/-/g,'').replace(/_/g,'');};
    site = (sites||[]).find(function(s){return norm(s.name) === norm(currentProject);});
  } catch(e) { return ''; }
  if (!site) {
    return '<div class="section-card" style="margin-bottom:16px"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Publicatie &amp; Social</h4>' +
      '<p style="font-size:12px;color:#94a3b8">Geen site gevonden voor dit project — maak er eerst één aan via <code>POST /api/sites</code> (zie /docs).</p></div>';
  }
  window._settingsSite = site;
  return '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:4px">Publicatie &amp; Social — ' + escHtml(site.name) + '</h4>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">Tokens worden nooit teruggestuurd naar de browser — laat een veld leeg om de bestaande waarde te behouden.</p>' +
    '<label style="display:flex;align-items:center;gap:6px;margin-bottom:12px;font-size:12px;font-weight:600;color:#334155">' +
    '<input type="checkbox" id="site-auto-content" ' + (site.auto_content_enabled ? 'checked' : '') + ' /> ' +
    '2x/week auto-content aan (schrijft di+vr een concept, wacht op jouw goedkeuring in de Wachtrij-tab)</label>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px">' +
    _siteField('Basis-URL (live site)', 'base_url', site.base_url) +
    _siteField('Search Console property', 'gsc_property', site.gsc_property) +
    _siteField('Netlify site-ID', 'publish_api_url', site.publish_api_url) +
    _siteField('Netlify token', 'publish_api_key', '', {secret:true, set:site.publish_api_key_set}) +
    _siteField('LinkedIn token', 'linkedin_token', '', {secret:true, set:site.linkedin_token_set}) +
    _siteField('LinkedIn user URN/ID', 'linkedin_user_urn', site.linkedin_user_urn) +
    _siteField('Facebook page-ID', 'facebook_page_id', site.facebook_page_id) +
    _siteField('Facebook page-token', 'facebook_page_token', '', {secret:true, set:site.facebook_page_token_set}) +
    _siteField('Instagram business-ID', 'instagram_business_id', site.instagram_business_id) +
    _siteField('X API key', 'twitter_api_key', '', {secret:true, set:site.twitter_api_key_set}) +
    _siteField('X API secret', 'twitter_api_secret', '', {secret:true, set:site.twitter_api_secret_set}) +
    _siteField('X access token', 'twitter_access_token', '', {secret:true, set:site.twitter_access_token_set}) +
    _siteField('X access secret', 'twitter_access_secret', '', {secret:true, set:site.twitter_access_secret_set}) +
    '</div>' +
    '<button onclick="saveSitePublishSettings(this)" class="btn btn-primary btn-sm" style="margin-top:8px">Opslaan</button>' +
    '<span id="site-settings-status" style="margin-left:10px;font-size:11px;color:var(--green)"></span>' +
    '</div>';
}

async function saveSitePublishSettings(btn) {
  var site = window._settingsSite; if (!site) return;
  var body = { auto_content_enabled: !!document.getElementById('site-auto-content').checked };
  document.querySelectorAll('[data-site-field]').forEach(function(input) {
    var v = input.value;
    if (v !== '') body[input.getAttribute('data-site-field')] = v;
  });
  if (btn) { btn.disabled = true; btn.textContent = 'Opslaan...'; }
  try {
    var resp = await fetch('/api/sites/' + site.id, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { var d = await resp.json(); alert('Mislukt: ' + (d.detail || 'onbekende fout')); }
    else { var statusEl = document.getElementById('site-settings-status'); if (statusEl) statusEl.textContent = 'Opgeslagen'; renderInstellingenTab(document.getElementById('tab-content')); }
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Opslaan'; }
}

// ── Kennisbank: profiel/USP's, CTA's, batch-grootte + casestudies per site ──
async function renderKennisbankSettings() {
  var site = window._settingsSite;
  if (!site) return '';
  var studies = [];
  try { studies = await (await fetch('/api/knowledge/' + site.id + '/case-studies')).json(); } catch(e) {}

  var ctas = [];
  try { ctas = JSON.parse(site.ctas || '[]'); } catch(e) {}

  var html = '<div class="section-card" style="margin-bottom:16px">' +
    '<h4 style="font-size:13px;font-weight:600;margin-bottom:4px">Kennisbank — ' + escHtml(site.name) + '</h4>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:10px">Dit gaat in élke schrijfopdracht mee (information gain): wie je bent, je CTA\'s en één passende casestudy als bewijs. Hoe concreter, hoe minder generiek de artikelen.</p>' +
    '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:#64748b;margin-bottom:2px">Profiel &amp; USP\'s (wie ben je, voor wie, waarom jij)</span>' +
    '<textarea id="kb-profile" rows="4" style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;box-sizing:border-box">' + escHtml(site.profile || '') + '</textarea></label>' +
    '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:#64748b;margin-bottom:2px">Call-to-actions (één per regel, bv. "Plan een gratis kennismaking → /contact")</span>' +
    '<textarea id="kb-ctas" rows="3" style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;box-sizing:border-box">' + escHtml(ctas.join('\n')) + '</textarea></label>' +
    '<label style="display:block;margin-bottom:8px"><span style="display:block;font-size:11px;color:#64748b;margin-bottom:2px">Artikelen per auto-content-run (1-10)</span>' +
    '<input type="number" id="kb-batch" min="1" max="10" value="' + (site.content_batch_size || 1) + '" style="width:80px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px" /></label>' +
    '<button onclick="saveKennisbank(this)" class="btn btn-primary btn-sm">Opslaan</button>' +
    '<span id="kb-status" style="margin-left:10px;font-size:11px;color:var(--green)"></span>';

  // Casestudies
  html += '<h5 style="font-size:12px;font-weight:600;margin:16px 0 4px">Casestudies (' + (studies||[]).length + ')</h5>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:8px">Harde data en resultaten van echte projecten. Bij elk artikel wordt automatisch de best passende casestudy gematcht (op tags/titel).</p>';
  (studies||[]).forEach(function(cs){
    html += '<div style="border:1px solid var(--card-border);border-radius:8px;padding:8px 10px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px' + (cs.status === 'archived' ? ';opacity:.55' : '') + '">' +
      '<div style="min-width:0"><div style="font-size:12px;font-weight:600">' + escHtml(cs.title) + (cs.status === 'archived' ? ' <span style="font-weight:400;color:var(--text-muted)">(gearchiveerd)</span>' : '') + '</div>' +
      '<div style="font-size:11px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(cs.summary || '') + '</div>' +
      (cs.tags ? '<div style="font-size:10px;color:var(--text-muted)">tags: ' + escHtml(cs.tags) + '</div>' : '') + '</div>' +
      '<div style="flex-shrink:0;display:flex;gap:4px">' +
      '<button onclick="toggleCaseStudy(\'' + cs.id + '\',\'' + (cs.status === 'archived' ? 'active' : 'archived') + '\')" class="btn btn-ghost btn-sm">' + (cs.status === 'archived' ? 'Activeer' : 'Archiveer') + '</button>' +
      '<button onclick="deleteCaseStudy(\'' + cs.id + '\')" class="btn btn-danger-outline btn-sm">Verwijder</button>' +
      '</div></div>';
  });
  html += '<div style="border:1px dashed var(--card-border);border-radius:8px;padding:10px;margin-top:8px">' +
    '<div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:6px">Nieuwe casestudy</div>' +
    '<input id="cs-title" placeholder="Titel (bv. Webshop X: +140% organisch verkeer in 6 maanden)" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box;margin-bottom:6px" />' +
    '<textarea id="cs-summary" rows="2" placeholder="Korte samenvatting (gaat in de prompt-matching mee)" style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box;margin-bottom:6px"></textarea>' +
    '<textarea id="cs-body" rows="4" placeholder="Details: concrete cijfers, aanpak, resultaten. De AI gebruikt dit letterlijk als bewijs — verzin niets." style="width:100%;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px;box-sizing:border-box;margin-bottom:6px"></textarea>' +
    '<div style="display:flex;gap:6px"><input id="cs-tags" placeholder="Tags, komma-gescheiden (bv. seo, webshop, linkbuilding)" style="flex:1;padding:6px 8px;border:1px solid var(--card-border);border-radius:6px;font-size:12px" />' +
    '<button onclick="addCaseStudy(this)" class="btn btn-primary btn-sm" style="background:var(--green);flex-shrink:0">Toevoegen</button></div>' +
    '</div></div>';
  return html;
}

async function saveKennisbank(btn) {
  var site = window._settingsSite; if (!site) return;
  var ctaLines = (document.getElementById('kb-ctas').value || '').split('\n')
    .map(function(l){ return l.trim(); }).filter(function(l){ return l; });
  var body = {
    profile: document.getElementById('kb-profile').value,
    ctas: JSON.stringify(ctaLines),
    content_batch_size: parseInt(document.getElementById('kb-batch').value, 10) || 1,
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Opslaan...'; }
  try {
    var resp = await fetch('/api/sites/' + site.id, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { var d = await resp.json(); alert('Mislukt: ' + (d.detail || 'onbekende fout')); }
    else { var el = document.getElementById('kb-status'); if (el) el.textContent = 'Opgeslagen'; }
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Opslaan'; }
}

async function addCaseStudy(btn) {
  var site = window._settingsSite; if (!site) return;
  var body = {
    title: document.getElementById('cs-title').value.trim(),
    summary: document.getElementById('cs-summary').value.trim(),
    body: document.getElementById('cs-body').value.trim(),
    tags: document.getElementById('cs-tags').value.trim(),
  };
  if (!body.title) { alert('Titel is verplicht.'); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Toevoegen...'; }
  try {
    var resp = await fetch('/api/knowledge/' + site.id + '/case-studies', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { var d = await resp.json(); alert('Mislukt: ' + (d.detail || 'onbekende fout')); }
    else renderInstellingenTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Toevoegen'; }
}

async function toggleCaseStudy(csId, newStatus) {
  try {
    await fetch('/api/knowledge/case-studies/' + csId, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({status: newStatus}) });
    renderInstellingenTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function deleteCaseStudy(csId) {
  if (!confirm('Casestudy definitief verwijderen? (Archiveren kan ook — dan blijft hij bewaard.)')) return;
  try {
    await fetch('/api/knowledge/case-studies/' + csId, { method: 'DELETE' });
    renderInstellingenTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════════════
//  CHAT — Werkende chat met streaming
// ═══════════════════════════════════════════════════════════════════
var _chatSessionId = null;
var _chatPendingAttachments = [];

async function ensureChatSession() {
  if (_chatSessionId) return _chatSessionId;
  try {
    var resp = await fetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: currentProject + ' chat', agent: 'claude'}),
    });
    var data = await resp.json();
    _chatSessionId = data.id;
    return _chatSessionId;
  } catch(e) {
    // Fallback: create via sessions endpoint
    try {
      var resp2 = await fetch('/api/sessions');
      var existing = await resp2.json();
      if (existing && existing.length) {
        _chatSessionId = existing[0].id;
        return _chatSessionId;
      }
    } catch(e2) {}
    return null;
  }
}

function renderChat(main) {
  _chatPendingAttachments = [];
  main.innerHTML = renderSidebar() + '<div class="main-content"><div class="project-header"><div><h1>Iris</h1><p class="meta">' + escHtml(currentProject||'') + '</p></div><div class="actions"><button onclick="goHome()">Projecten</button></div></div><div class="chat-container">' +
    '<div id="chat-messages" class="chat-messages"><div id="chat-empty-state" class="chat-empty-state">Ik ben er klaar voor.</div></div>' +
    '<div id="chat-attachments" class="chat-attachments" style="display:none"></div>' +
    '<div class="chat-input-pill">' +
      '<input type="file" id="chat-file-input" accept="image/*" multiple style="display:none" onchange="handleChatFileSelect(this)">' +
      '<button type="button" onclick="document.getElementById(\'chat-file-input\').click()" title="Foto toevoegen" class="chat-icon-btn">+</button>' +
      '<input id="chat-input" placeholder="Stel een vraag" onkeydown="if(event.key===\'Enter\')sendChat()">' +
      '<button onclick="sendChat()" title="Verstuur" class="chat-send-btn">&#8593;</button>' +
    '</div>' +
  '</div></div>';
  ensureChatSession();
}

// ── Iris in de ik-vorm: rauwe tool-namen worden nooit getoond. ──────────────
var _CHAT_TOOL_LABELS = {
  calendar_create: { bezig: 'Ik zet dit in je agenda', klaar: 'Ik heb het in je agenda gezet.' },
  fetch_financial_news: { bezig: 'Ik haal het laatste beursnieuws op', klaar: 'Ik heb het beursnieuws erbij.' },
  get_market_data: { bezig: 'Ik haal de actuele koersen op', klaar: 'Ik heb de koersen erbij.' },
  get_analytics: { bezig: 'Ik haal je websitecijfers op', klaar: 'Ik heb de cijfers erbij.' },
  web_search: { bezig: 'Ik zoek dit voor je uit', klaar: 'Ik heb het uitgezocht.' },
  obsidian_search: { bezig: 'Ik zoek dit op in wat we eerder hebben vastgelegd', klaar: 'Ik heb het gevonden.' },
  obsidian_write: { bezig: 'Ik leg dit voor je vast', klaar: 'Ik heb het vastgelegd.' },
  notebooklm_research: { bezig: 'Ik laat dit grondig uitzoeken', klaar: 'Het onderzoek is klaar.' },
  create_task: { bezig: 'Ik maak hier een taak van', klaar: 'Ik heb de taak aangemaakt.' },
  list_tasks: { bezig: 'Ik check je openstaande taken', klaar: 'Ik heb je taken erbij.' },
  delegate: { bezig: 'Ik zet dit uit', klaar: 'Ik heb het uitgezet.' },
  delegation_status: { bezig: 'Ik check hoe ver dat staat', klaar: 'Ik heb de status.' },
};
function _chatToolLabel(name, phase) {
  var entry = _CHAT_TOOL_LABELS[name];
  if (entry) return entry[phase];
  return phase === 'klaar' ? 'Klaar.' : 'Ik ben hier even mee bezig';
}

async function handleChatFileSelect(input) {
  var files = Array.prototype.slice.call(input.files || []);
  input.value = '';
  if (!files.length) return;
  var box = document.getElementById('chat-attachments');
  var uploadingId = 'chat-att-uploading-' + Date.now();
  if (box) {
    box.style.display = 'block';
    box.innerHTML += '<span id="' + uploadingId + '" class="chat-attachment-chip">Uploaden…</span>';
  }
  try {
    var form = new FormData();
    files.forEach(function (f) { form.append('files', f); });
    var resp = await fetch('/api/chat/upload', { method: 'POST', body: form });
    if (!resp.ok) { var d = await resp.json().catch(function(){return {};}); throw new Error(d.detail || ('HTTP ' + resp.status)); }
    var data = await resp.json();
    (data.attachments || []).forEach(function (a) { _chatPendingAttachments.push(a); });
  } catch (e) {
    alert('Upload mislukt: ' + e.message);
  }
  renderChatAttachments();
}

function renderChatAttachments() {
  var box = document.getElementById('chat-attachments');
  if (!box) return;
  if (!_chatPendingAttachments.length) { box.style.display = 'none'; box.innerHTML = ''; return; }
  box.style.display = 'block';
  box.innerHTML = _chatPendingAttachments.map(function (a, i) {
    return '<span class="chat-attachment-chip">' +
      (a.content_type && a.content_type.indexOf('image/') === 0 ? '<img src="' + escAttr(a.url) + '" alt="">' : '') +
      escHtml(a.filename) + ' <a href="#" onclick="removeChatAttachment(' + i + ');return false;">✕</a></span>';
  }).join('');
}

function removeChatAttachment(idx) {
  _chatPendingAttachments.splice(idx, 1);
  renderChatAttachments();
}

async function sendChat() {
  var input = document.getElementById('chat-input');
  var msg = input.value.trim();
  var attachments = _chatPendingAttachments.slice();
  if (!msg && !attachments.length) return;
  input.value = '';
  _chatPendingAttachments = [];
  renderChatAttachments();
  var container = document.getElementById('chat-messages');
  var empty = document.getElementById('chat-empty-state');
  if (empty) empty.remove();
  var userHtml = escHtml(msg);
  attachments.forEach(function (a) {
    if (a.content_type && a.content_type.indexOf('image/') === 0) {
      userHtml += '<br><img src="' + escAttr(a.url) + '" alt="' + escAttr(a.filename) + '" style="max-width:220px;max-height:220px;border-radius:8px;margin-top:6px;display:block">';
    }
  });
  container.innerHTML += '<div class="chat-msg user">' + userHtml + '</div>' +
    '<div class="chat-msg status" id="chat-status"><span class="chat-status-dot"></span><span id="chat-status-text">Ik denk hierover na</span></div>';
  container.scrollTop = container.scrollHeight;

  var sid = _chatSessionId;
  if (!sid) {
    sid = await ensureChatSession();
  }
  if (!sid) {
    document.getElementById('chat-status').outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Ik kan nu geen gesprek starten. Probeer het via Instellingen opnieuw.</div>';
    return;
  }

  // Use the streaming chat endpoint
  try {
    var resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, message: msg, agent: 'claude', use_obsidian: true, attachments: attachments}),
    });
    if (!resp.ok) {
      var errText = await resp.text();
      document.getElementById('chat-status').outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Er ging iets mis: ' + escHtml(errText.slice(0,200)) + '</div>';
      return;
    }

    var statusEl = document.getElementById('chat-status');
    var statusTextEl = document.getElementById('chat-status-text');
    var streamingEl = null;

    function ensureStreamingEl() {
      if (streamingEl) return streamingEl;
      if (statusEl) statusEl.remove();
      container.innerHTML += '<div class="chat-msg assistant" id="chat-streaming"></div>';
      streamingEl = document.getElementById('chat-streaming');
      return streamingEl;
    }

    // Read the stream
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var fullText = '';

    while (true) {
      var {done, value} = await reader.read();
      if (done) break;
      var chunk = decoder.decode(value, {stream: true});
      var lines = chunk.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li].trim();
        if (!line || line === ':' || line.startsWith(':keepalive')) continue;
        if (line === '[DONE]' || line === 'data: [DONE]') {
          if (fullText) { ensureStreamingEl().innerHTML = mdToHtmlSimple(fullText); }
          else if (statusTextEl) { statusTextEl.textContent = 'Ik heb hier geen antwoord op.'; }
          break;
        }
        if (line.startsWith('data: ')) {
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text' || evt.type === 'thought') {
              fullText += evt.text || '';
              ensureStreamingEl().innerHTML = mdToHtmlSimple(fullText);
              container.scrollTop = container.scrollHeight;
            } else if (evt.type === 'error') {
              var errEl = ensureStreamingEl();
              errEl.innerHTML += '<div style="color:var(--red);margin-top:8px">Er ging iets mis: ' + escHtml(evt.message||'') + '</div>';
            } else if (evt.type === 'tool_start') {
              if (statusTextEl && !streamingEl) statusTextEl.textContent = _chatToolLabel(evt.name, 'bezig') + '…';
            } else if (evt.type === 'tool_result') {
              if (statusTextEl && !streamingEl) statusTextEl.textContent = _chatToolLabel(evt.name, 'klaar');
            }
          } catch(e) {
            // Non-JSON SSE line, skip
          }
        }
      }
    }
    if (streamingEl) streamingEl.id = ''; // Remove id after done
  } catch(e) {
    var p = document.getElementById('chat-status') || document.getElementById('chat-streaming');
    if (p) p.outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Er ging iets mis: ' + escHtml(e.message) + '</div>';
  }
}

// Simple markdown renderer for chat (no tables needed)
function mdToHtmlSimple(text) {
  if (!text) return '';
  var t = escHtml(text);
  // Code blocks
  t = t.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
    return '<pre style="background:#1e293b;color:#e2e8f0;padding:10px;border-radius:6px;overflow-x:auto;font-size:11px;line-height:1.5;margin:8px 0"><code>' + code + '</code></pre>';
  });
  // Inline code
  t = t.replace(/`([^`]+)`/g, '<code style="background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:11px">$1</code>');
  // Bold
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  t = t.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Line breaks
  t = t.replace(/\n/g, '<br>');
  return t;
}

// ═══════════════════════════════════════════════════════════════════
//  FINANCE EXPERT (non-GSC)
// ═══════════════════════════════════════════════════════════════════
function renderFinanceExpert(el) {
  el.innerHTML = '<div id="beursmeester-desk"></div>' +
    '<div class="agent-card" style="margin-top:20px"><div class="agent-icon" style="background:var(--accent)">F</div><h2>Finance Expert Agent</h2><p class="desc">Marktanalyse en rapportage. Het dagrapport (07:30) en weekrapport (ma 08:15) adviseren; de Beursmeester hierboven rekent dat advies af.</p>' +
    '<button onclick="switchView(\'chat\')" class="btn btn-primary">Start chat</button></div>';
  // Het volledige beursbureau staat in tabs-invest.js. Bewust één implementatie:
  // twee panelen die dezelfde vraag beantwoorden, lopen uit elkaar.
  renderBeursmeester(document.getElementById('beursmeester-desk'));
}

// ═══════════════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', function() {
  var m = location.hash.match(/project=([^&]+)/);
  if (m) currentProject = decodeURIComponent(m[1]);
  var t = location.hash.match(/tab=([^&]+)/);
  if (t && TABS.indexOf(decodeURIComponent(t[1])) >= 0) currentTab = decodeURIComponent(t[1]);
  checkAuthAndStart();
});
