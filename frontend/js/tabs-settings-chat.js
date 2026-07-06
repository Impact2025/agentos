// ── Agent OS — tabs: Instellingen, Chat, Finance Expert + INIT
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

  html += await renderSitePublishSettings();

  // ── Agent Profielen tabel ──
  html += '<div class="section-card"><h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Agent Profielen (' + (profiles||[]).length + ')</h4>' +
    '<table class="data-table"><thead><tr><th>Naam</th><th>Model</th><th>MCP Servers</th><th>Aangemaakt</th></tr></thead><tbody>';
  (profiles||[]).forEach(function(p){
    var mcpStr = (p.mcp_servers||[]).join(', ') || '-';
    var created = (p.created_at||'').slice(0,10);
    html += '<tr><td><span style="font-weight:600">' + escHtml(p.name) + '</span></td>' +
      '<td style="font-size:11px;color:#64748b">' + escHtml(p.model||'-') + '</td>' +
      '<td style="font-size:11px;color:#64748b">' + escHtml(mcpStr) + '</td>' +
      '<td style="font-size:11px;color:#94a3b8">' + escHtml(created) + '</td></tr>';
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
}

// ── Publicatie- & social-instellingen voor de site achter dit project ──
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
    '<button onclick="saveSitePublishSettings(this)" style="margin-top:8px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Opslaan</button>' +
    '<span id="site-settings-status" style="margin-left:10px;font-size:11px;color:#059669"></span>' +
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
    else { var statusEl = document.getElementById('site-settings-status'); if (statusEl) statusEl.textContent = 'Opgeslagen ✓'; renderInstellingenTab(document.getElementById('tab-content')); }
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Opslaan'; }
}

// ═══════════════════════════════════════════════════════════════════
//  CHAT — Werkende chat met streaming
// ═══════════════════════════════════════════════════════════════════
var _chatSessionId = null;

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
  main.innerHTML = renderSidebar() + '<div class="main-content"><div class="project-header"><div><h1>Chat — ' + escHtml(currentProject||'Agent OS') + '</h1></div><div class="actions"><button onclick="goHome()">Projecten</button></div></div><div class="chat-container"><div id="chat-messages" class="chat-messages"><div class="chat-msg assistant">Hallo! Ik ben je AI-assistent voor ' + escHtml(currentProject||'Agent OS') + '. Waar kan ik je mee helpen?</div></div><div class="chat-input"><input id="chat-input" placeholder="Typ je bericht..." onkeydown="if(event.key===\'Enter\')sendChat()"><button onclick="sendChat()">Verstuur</button></div></div></div>';
  ensureChatSession();
}

async function sendChat() {
  var input = document.getElementById('chat-input');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  var container = document.getElementById('chat-messages');
  container.innerHTML += '<div class="chat-msg user">' + escHtml(msg) + '</div><div class="chat-msg assistant" id="chat-pending"><em>Hermes denkt...</em></div>';
  container.scrollTop = container.scrollHeight;

  var sid = _chatSessionId;
  if (!sid) {
    sid = await ensureChatSession();
  }
  if (!sid) {
    document.getElementById('chat-pending').outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Kon geen chatsessie starten. Start eerst een sessie via Instellingen.</div>';
    return;
  }

  // Use the streaming chat endpoint
  try {
    var resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, message: msg, agent: 'claude', use_obsidian: true}),
    });
    if (!resp.ok) {
      var errText = await resp.text();
      document.getElementById('chat-pending').outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Fout: ' + escHtml(errText.slice(0,200)) + '</div>';
      return;
    }

    var pending = document.getElementById('chat-pending');
    if (!pending) return;
    pending.outerHTML = '<div class="chat-msg assistant" id="chat-streaming"><em>Antwoord ontvangen...</em></div>';
    var streamingEl = document.getElementById('chat-streaming');
    if (!streamingEl) return;

    // Read the stream
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var fullText = '';
    streamingEl.innerHTML = '';

    while (true) {
      var {done, value} = await reader.read();
      if (done) break;
      var chunk = decoder.decode(value, {stream: true});
      var lines = chunk.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li].trim();
        if (!line || line === ':' || line.startsWith(':keepalive')) continue;
        if (line === '[DONE]' || line === 'data: [DONE]') {
          streamingEl.innerHTML = fullText ? mdToHtmlSimple(fullText) : '(geen antwoord)';
          break;
        }
        if (line.startsWith('data: ')) {
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text' || evt.type === 'thought') {
              fullText += evt.text || '';
              streamingEl.innerHTML = mdToHtmlSimple(fullText);
              container.scrollTop = container.scrollHeight;
            } else if (evt.type === 'error') {
              streamingEl.innerHTML += '<div style="color:#ef4444;margin-top:8px">❌ Fout: ' + escHtml(evt.message||'') + '</div>';
            } else if (evt.type === 'tool_start') {
              streamingEl.innerHTML += '<div style="color:#64748b;font-size:11px;margin:4px 0">🔧 Gebruik: ' + escHtml(evt.name||'') + '...</div>';
            } else if (evt.type === 'tool_result') {
              streamingEl.innerHTML += '<div style="color:#94a3b8;font-size:10px;margin:2px 0">✓ ' + escHtml(evt.name||'') + ' klaar</div>';
            }
          } catch(e) {
            // Non-JSON SSE line, skip
          }
        }
      }
    }
    streamingEl.id = ''; // Remove id after done
  } catch(e) {
    var p = document.getElementById('chat-pending') || document.getElementById('chat-streaming');
    if (p) p.outerHTML = '<div class="chat-msg assistant" style="color:#ef4444">❌ Fout: ' + escHtml(e.message) + '</div>';
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
  el.innerHTML = '<div class="agent-card"><div class="agent-icon" style="background:linear-gradient(135deg,#f43f5e,#e11d48)">F</div><h2>Finance Expert Agent</h2><p class="desc">Financiele analyse, rapportage en inzicht.</p><div class="cap-grid">' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">1</div><div><p>Dagelijks financieel rapport</p><p class="sub">Automatisch om 09:00.</p></div></div>' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">2</div><div><p>Wekelijkse trendanalyse</p><p class="sub">Inzicht in patronen en budget-bewaking.</p></div></div>' +
    '<div class="cap-item"><div class="num" style="background:#f43f5e">3</div><div><p>Ad-hoc analyses</p><p class="sub">Stel vragen over specifieke periodes.</p></div></div></div>' +
    '<div class="tips"><h3>Tips</h3><ul><li>Vraag naar de dagelijkse financiele samenvatting voor een snel overzicht van je omzet en uitgaven.</li><li>Laat een wekelijks rapport genereren met trends en afwijkingen in je financien.</li><li>Gebruik "vergelijken met vorige maand" om seizoenspatronen te ontdekken.</li></ul></div>' +
    '<button onclick="switchView(\'chat\')" style="padding:10px 28px;background:#f43f5e;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer">Start chat</button></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', function() {
  var m = location.hash.match(/project=([^&]+)/);
  if (m) currentProject = decodeURIComponent(m[1]);
  var t = location.hash.match(/tab=([^&]+)/);
  if (t && TABS.indexOf(decodeURIComponent(t[1])) >= 0) currentTab = decodeURIComponent(t[1]);
  route();
});
