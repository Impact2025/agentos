// ── WeAreImpact — Control Room dashboard ───────────────────────────────────
// Een WeAreImpact-specifieke Dashboard-tab die alles bundelt wat Vincent voor
// dit project in één oogopslag wil zien:
//   1. Iris-analyse  — wat is op dit moment de beste taak om te doen, en
//      waaróm (gebouwd op de bestaande /api/projects/WeAreImpact/advice).
//   2. WhatsApp-agent — volume, escalaties en open gesprekken (via de
//      bridge-proxy naar het remote-systeem, waar de WA-data staat).
//   3. Chat "Wat kan ik voor je betekenen" — de bestaande streaming chat,
//      ingebed in het dashboard zelf (geen aparte tab nodig).
//
// Laadt na shell.js (zie index.html) en deelt de globale scope: het gebruikt
// _chatSessionId/ensureChatSession/sendChat/mdToHtmlSimple/renderAdviceBanner/
// _acKindMeta/_pillBorderColor/escHtml exact zoals de rest van de SPA.
let _waiAdviceTimer = null;
let _waiWaTimer = null;
let _waiPulseTimer = null;

function renderWeAreImpactDashboard(el) {
  if (!currentProject || currentProject !== 'WeAreImpact') { renderDashboardTabFallback(el); return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>WeAreImpact Control Room laden...</p></div>';
  try {
    var proj = encodeURIComponent(currentProject);
    Promise.all([
      fetch('/api/projects/' + proj + '/advice?days=28').then(function(r){return r.json();}).catch(function(){return {};}),
      fetch('/api/projects/' + proj + '/activity?limit=12').then(function(r){return r.json();}).catch(function(){return [];}),
      fetch('/api/action-center?project=' + proj).then(function(r){return r.json();}).catch(function(){return {items:[]};}),
      fetch('/api/bridge/whatsapp-stats').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
      fetch('/api/bridge/whatsapp').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
      fetch('/api/bridge/whatsapp-conversations').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
      fetch('/api/radar/news-briefing').then(function(r){return r.json();}).catch(function(){return {items:[]};}),
    ]).then(function(res){
      var advice = res[0] || {};
      var activity = res[1] || [];
      var ac = res[2] || { items: [] };
      var wa = res[3] || { ok: false };
      var waEsc = (res[4] && res[4].escalations) || [];
      var waConvos = (res[5] && res[5].conversations) || [];
      var news = res[6] || { items: [] };
      var html = '';
      html += '<div id="wai-hero-host"></div>';
      html += '<div id="wai-pulse-host"></div>';
      html += '<div id="wai-banner">' + (typeof renderAdviceBanner === 'function' ? renderAdviceBanner(advice) : '') + '</div>';
      html += waiIrisAnalysis(advice);
      html += '<div id="wai-news-host">' + waiNewsBlock(news) + '</div>';
      html += '<div id="wai-whatsapp-host">' + waiWhatsAppBlock(wa, waEsc, waConvos) + '</div>';
      html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><h3 style="font-size:14px;font-weight:700;color:var(--text)">Wacht op jou — WeAreImpact (' + (ac.items||[]).length + ')</h3><span style="font-size:11px;color:#94a3b8">klik = klaar</span></div>' +
              '<div id="wai-ac"></div></div>';
      html += waiChatBlock();
      html += '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><h3 style="font-size:13px;font-weight:700;color:var(--text)">Recente activiteit</h3><button onclick="waiLoadActivity()" class="btn btn-sm btn-ghost">Ververs</button></div>' +
              '<div id="wai-activity" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:200px;overflow-y:auto"></div></div>';
      el.innerHTML = html;
      waiRenderActivity(activity);
      waiRenderActionCenter(ac);
      waiStartAdvicePoll(proj);
      waiStartWaPoll();
      waiLoadPulse();
      waiStartPulsePoll();
    }).catch(function(e){
      el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
    });
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
  }
}

// Terugval als iemand de functie aanroept terwijl een ander project geselecteerd
// is — roep de generieke dashboard-render aan zonder oneindige lus.
function renderDashboardTabFallback(el) {
  // Kopieer de eerste helft van renderDashboardTab niet; roep simpelweg de
  // bestaande generieke flow aan door currentProject tijdelijk te ontzien.
  if (typeof renderDashboardTabCore === 'function') renderDashboardTabCore(el);
  else el.innerHTML = '<div class="empty-state">Dashboard niet beschikbaar voor dit project.</div>';
}

// ── 0. Control-Room-stijl: hero-KPI's + "Iris deze week", gefilterd op
// WeAreImpact — zelfde tegels/opmaak als de globale Control Room (home.js),
// hier gevoed door dezelfde /api/action-center/pulse-call met ?project=. ──
function waiHeroKpiRow(hero) {
  if (!hero) return '';
  var tiles = [
    { label: 'Fouten (24u)', value: hero.errors_24h || 0, tone: (hero.errors_24h||0) > 0 ? 'tone-danger' : 'tone-ok' },
    { label: 'Wacht op review', value: hero.pending_review_total || 0, tone: (hero.pending_review_total||0) > 0 ? '' : 'tone-ok' },
    { label: 'Opgeleverd (24u)', value: hero.delivered_24h || 0, tone: 'tone-ok' },
    { label: 'Quick wins deze week', value: hero.quick_wins || 0, tone: (hero.quick_wins||0) > 0 ? 'tone-warn' : '' },
    { label: 'Actieve doelen', value: hero.running_goals || 0, tone: '' },
  ];
  return '<div class="hero-kpi-grid" style="margin-bottom:16px">' + tiles.map(function(t){
    return '<div class="hero-kpi ' + t.tone + '"><p class="label">' + escHtml(t.label) + '</p><p class="value">' + t.value + '</p></div>';
  }).join('') + '</div>';
}

function waiLoadPulse() {
  fetch('/api/action-center/pulse?project=' + encodeURIComponent(currentProject)).then(function(r){return r.json();}).then(function(p){
    if (currentProject !== 'WeAreImpact') return;
    var heroHost = document.getElementById('wai-hero-host');
    if (heroHost) heroHost.innerHTML = waiHeroKpiRow(p.hero);
    var pulseHost = document.getElementById('wai-pulse-host');
    if (pulseHost && typeof renderIrisPulse === 'function') pulseHost.innerHTML = renderIrisPulse(p);
  }).catch(function(){});
}

function waiStartPulsePoll() {
  if (_waiPulseTimer) clearInterval(_waiPulseTimer);
  _waiPulseTimer = setInterval(function(){
    if (currentProject !== 'WeAreImpact' || currentTab !== 'Dashboard') { clearInterval(_waiPulseTimer); _waiPulseTimer = null; return; }
    waiLoadPulse();
  }, 60000);
}

// ── 1. Iris-analyse: beste taak nu ─────────────────────────────────────────
function waiIrisAnalysis(advice) {
  var next = (advice && advice.next_step) || '';
  var banner = (advice && advice.banner) || null;
  // "Beste taak" = de next_step uit het advies. Als die er niet is, val terug
  // op de actieve doel-banner ("Bezig: ...") of een neutrale uitnodiging.
  var taskText = next || (banner && banner.text) || 'Geen open taak — alles loopt. Vraag Iris hieronder wat zinvol is.';
  var action = (advice && advice.next_step_action) || (banner && banner.action) || '';
  var quick = (advice && advice.quick_actions) || [];

  var html = '<div class="section-card" style="margin:0 0 16px;background:linear-gradient(135deg,#eef2ff,#f8fafc);border:1px solid #e0e7ff">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:8px">' +
    '<h3 style="font-size:14px;font-weight:700;color:var(--accent);margin:0">🧭 Iris analyse — beste taak nu</h3>' +
    '<div style="display:flex;gap:6px">' +
    '<button onclick="waiRunIris()" id="wai-iris-btn" class="btn btn-ghost btn-sm">Iris opnieuw analyseren</button>' +
    '</div></div>' +
    '<p style="font-size:13px;color:var(--text);line-height:1.5;margin:0 0 10px" id="wai-best-task">' + escHtml(taskText) + '</p>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center" id="wai-best-actions">';
  if (action) {
    html += '<button type="button" data-advice-action="' + escAttr(action) + '" class="btn btn-sm btn-primary" onclick="waiRunBestTask(this)">Nu uitvoeren</button>';
  }
  quick.forEach(function(qa){
    html += '<button type="button" data-advice-action="' + escAttr(qa.action) + '" class="btn btn-sm ' + (qa.primary ? 'btn-primary' : 'btn-ghost') + '" onclick="waiRunBestTask(this)">' + escHtml(qa.label) + '</button>';
  });
  html += '</div></div>';
  return html;
}

function waiRunIris() {
  var btn = document.getElementById('wai-iris-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Iris analyseert...'; }
  fetch('/api/iris/run-now', { method: 'POST' }).then(function(){
    // Ververs advies + banner
    return fetch('/api/projects/' + encodeURIComponent(currentProject) + '/advice?days=28');
  }).then(function(r){return r.json();}).then(function(advice){
    if (document.getElementById('wai-banner') && typeof renderAdviceBanner === 'function') {
      document.getElementById('wai-banner').innerHTML = renderAdviceBanner(advice);
    }
    var t = document.getElementById('wai-best-task');
    if (t) t.textContent = (advice && advice.next_step) || 'Klaar — geen open taak.';
    waiRebuildBestActions(advice);
  }).catch(function(e){
    if (btn) btn.textContent = 'Mislukt: ' + escHtml(e.message);
  }).finally(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'Iris opnieuw analyseren'; }
  });
}

function waiRebuildBestActions(advice) {
  var wrap = document.getElementById('wai-best-actions');
  if (!wrap) return;
  var action = (advice && advice.next_step_action) || (advice && advice.banner && advice.banner.action) || '';
  var quick = (advice && advice.quick_actions) || [];
  var html = '';
  if (action) html += '<button type="button" data-advice-action="' + escAttr(action) + '" class="btn btn-sm btn-primary" onclick="waiRunBestTask(this)">Nu uitvoeren</button>';
  quick.forEach(function(qa){
    html += '<button type="button" data-advice-action="' + escAttr(qa.action) + '" class="btn btn-sm ' + (qa.primary ? 'btn-primary' : 'btn-ghost') + '" onclick="waiRunBestTask(this)">' + escHtml(qa.label) + '</button>';
  });
  wrap.innerHTML = html;
}

// Voert de "beste taak" of een quick-action uit via de bestaande advice-action
// handler (handleAdviceAction bestaat al in de SPA: open_tab, write_article,
// new_goal, retry_goal, ...). Als de actie niet bekend is, toon een melding.
function waiRunBestTask(btn) {
  var action = btn.getAttribute('data-advice-action');
  if (!action) return;
  if (typeof handleAdviceAction === 'function') {
    try { handleAdviceAction(action); return; }
    catch (e) { /* val door naar melding hieronder */ }
  }
  // Geen handler beschikbaar — meld wat het was zodat het nooit "stil" faalt.
  var t = document.getElementById('wai-best-task');
  if (t) t.innerHTML = '<span style="color:var(--amber)">Actie niet direct uitvoerbaar vanuit dit scherm:</span> ' + escHtml(action) + '. Open de relevante tab via de zijbalk.';
}

// ── 1b. Nieuwsagent — dagelijkse pro-analyse (sector/concurrent/algemeen) ──
// Draait 06:20 (backend/scheduler.py:_weareimpact_news_briefing_job), voedt
// ook Iris' briefing (radar/newsroom.py:prompt_block). Hier alleen tonen +
// een handmatige trigger — nooit publiceert/verstuurt dit iets zelf.
var _WAI_NEWS_CAT_LABEL = {
  sector: 'Sector — sociaal domein, zorg & AI',
  concurrent: 'Concurrentie & vakmedia',
  algemeen: 'Algemeen AI- & ondernemersnieuws',
};

function waiNewsBlock(data) {
  var items = (data && data.items) || [];
  var date = data && data.date;
  var byCat = {};
  items.forEach(function(it){ (byCat[it.categorie] = byCat[it.categorie] || []).push(it); });

  var body;
  if (!date) {
    body = '<p style="font-size:12px;color:#64748b">Nog geen briefing gedraaid. Klik "Nu draaien" voor de eerste analyse.</p>';
  } else if (!items.length) {
    body = '<p style="font-size:12px;color:#64748b">Geen nieuws op ' + escHtml(date) + ' dat de relevantiepoort haalde.</p>';
  } else {
    body = ['sector', 'concurrent', 'algemeen'].filter(function(c){ return byCat[c]; }).map(function(cat){
      return '<div style="margin-top:8px"><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:4px">' + escHtml(_WAI_NEWS_CAT_LABEL[cat] || cat) + '</p>' +
        byCat[cat].map(waiNewsItemCard).join('') + '</div>';
    }).join('');
  }

  return '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px">' +
    '<h3 style="font-size:14px;font-weight:700;color:var(--text)">📰 Nieuwsagent' + (date ? ' — ' + escHtml(date) : '') + '</h3>' +
    '<button onclick="waiRunNewsBriefing(this)" class="btn btn-ghost btn-sm">Nu draaien</button></div>' +
    body + '</div>';
}

function waiNewsItemCard(it) {
  var actie = it.actie || '';
  var geenActie = actie.toLowerCase().indexOf('geen actie') === 0;
  return '<div style="padding:6px 0;border-top:1px solid var(--card-border)">' +
    '<a href="' + escAttr(it.url || '#') + '" target="_blank" rel="noopener" style="font-size:12px;font-weight:600;color:var(--text);text-decoration:none">' + escHtml(it.title || '(zonder titel)') + '</a>' +
    '<p style="font-size:11px;color:var(--text-dim);margin-top:2px">' + escHtml(it.relevantie || it.samenvatting || '') + '</p>' +
    (geenActie ? '' : '<p style="font-size:11px;color:var(--accent);margin-top:2px">→ ' + escHtml(actie) + '</p>') +
    '</div>';
}

function waiLoadNews() {
  fetch('/api/radar/news-briefing').then(function(r){return r.json();}).then(function(data){
    var host = document.getElementById('wai-news-host');
    if (host) host.outerHTML = '<div id="wai-news-host">' + waiNewsBlock(data) + '</div>';
  }).catch(function(){});
}

function waiRunNewsBriefing(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Analyseren...'; }
  fetch('/api/radar/news-briefing/run', { method: 'POST' }).then(function(r){return r.json();}).then(function(){
    waiLoadNews();
  }).catch(function(e){
    if (btn) btn.textContent = 'Mislukt: ' + escHtml(e.message);
  }).finally(function(){
    if (btn) { btn.disabled = false; if (btn.textContent.indexOf('Mislukt') !== 0) btn.textContent = 'Nu draaien'; }
  });
}

// ── 2. WhatsApp-agent-overzicht ────────────────────────────────────────────
// Sinds 22 aug 2026 niet alleen cijfers: hetzelfde Communicatie-overzicht dat
// Iris Remote toont (wacht op jou / nieuwe contacten / alle gesprekken), nu
// ook op :1250 — via de bridge-proxy naar het remote-systeem (Neon), want de
// WhatsApp-data leeft daar en dit dashboard draait op SQLite. Reply/dismiss
// gaan hier ook doorheen: het antwoord verstuurt Vercel zelf naar Meta.
function waiWhatsAppBlock(wa, escalations, conversations) {
  if (!wa || wa.ok === false) {
    var why = wa && wa.detail ? escHtml(wa.detail) : 'bridge niet geconfigureerd of remote onbereikbaar';
    return '<div class="section-card" style="margin-bottom:16px;background:var(--warn-bg);border-color:var(--warn-border)">' +
      '<h3 style="font-size:14px;font-weight:700;color:var(--text);margin-bottom:6px">📱 Communicatie</h3>' +
      '<p style="font-size:12px;color:var(--text-dim)">Overzicht nu niet beschikbaar: ' + why + '.</p></div>';
  }
  var esc = wa.escalations || {};
  var openProjects = wa.open_by_project || [];
  var near = wa.near_limit || [];
  escalations = escalations || [];
  conversations = conversations || [];
  var nieuwe = conversations.filter(function(c){ return c.is_new; });

  var tiles = '';
  tiles += waiWaTile('Berichten vandaag', wa.messages_today || 0, wa.messages_7d ? (wa.messages_7d + ' in 7d') : '');
  tiles += waiWaTile('Gesprekken (7d)', wa.active_conversations_7d || 0, 'actieve klanten');
  tiles += waiWaTile('Nieuw (7d)', wa.new_contacts_7d || 0, 'nieuwe contacten');
  tiles += waiWaTile('Escalaties open', esc.open || 0, esc.created_7d ? (esc.created_7d + ' nieuw/7d') : '', (esc.open||0) > 0 ? 'tone-danger' : 'tone-ok');
  tiles += waiWaTile('Beantwoord (7d)', esc.answered_7d || 0, esc.avg_response_seconds ? _waiFmtResp(esc.avg_response_seconds) + ' gem. reactie' : '');

  var openHtml = openProjects.length
    ? openProjects.map(function(p){
        return '<li style="display:flex;justify-content:space-between;font-size:12px;padding:2px 0"><span>' + escHtml(p.project) + '</span><span class="pill ' + (p.open > 0 ? 'pill-danger' : 'pill-neutral') + '">' + p.open + ' open</span></li>';
      }).join('')
    : '<li style="font-size:12px;color:#64748b">Geen open escalaties per project.</li>';

  var nearHtml = near.length
    ? '<div style="margin-top:8px;font-size:11px;color:var(--amber)">⚠ Rate-limit nadert: ' +
      near.map(function(n){ return escHtml(n.wa_id) + ' (' + n.count + '/' + (wa.daily_limit||40) + ')'; }).join(', ') + '</div>'
    : '';

  var escHtmlList = escalations.length
    ? escalations.map(waiEscalationCard).join('')
    : '<p style="font-size:12px;color:#64748b">Niets wacht op jou.</p>';

  var nieuweHtml = nieuwe.length
    ? '<ul style="list-style:none;padding:0;margin:0">' + nieuwe.map(waiConversationRow).join('') + '</ul>'
    : '<p style="font-size:12px;color:#64748b">Geen nieuwe contacten deze week.</p>';

  var allHtml = conversations.length
    ? '<ul style="list-style:none;padding:0;margin:0">' + conversations.map(waiConversationRow).join('') + '</ul>'
    : '<p style="font-size:12px;color:#64748b">Nog geen klantgesprekken via WhatsApp.</p>';

  return '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h3 style="font-size:14px;font-weight:700;color:var(--text)">📱 Communicatie — WhatsApp</h3>' +
    '<button onclick="waiLoadWa()" class="btn btn-ghost btn-sm">Ververs</button></div>' +
    '<div class="hero-kpi-grid" style="margin-bottom:8px">' + tiles + '</div>' +
    '<div style="display:flex;gap:16px;flex-wrap:wrap"><div style="flex:1;min-width:180px"><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:4px">Open per project</p><ul style="list-style:none;padding:0;margin:0">' + openHtml + '</ul></div></div>' +
    nearHtml +
    '<div style="margin-top:12px"><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:6px">Wacht op jou (' + escalations.length + ')</p>' + escHtmlList + '</div>' +
    (nieuwe.length ? '<div style="margin-top:12px"><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:6px">Nieuwe contacten — 7d (' + nieuwe.length + ')</p>' + nieuweHtml + '</div>' : '') +
    '<details style="margin-top:12px"><summary style="font-size:11px;font-weight:700;color:var(--text-dim);cursor:pointer">Alle gesprekken — 30d (' + conversations.length + ')</summary><div style="margin-top:6px">' + allHtml + '</div></details>' +
    '</div>';
}

function waiWaTile(label, value, sub, tone) {
  return '<div class="hero-kpi ' + (tone||'') + '"><p class="label">' + escHtml(label) + '</p><p class="value">' + value + '</p>' +
    (sub ? '<p style="font-size:10px;color:#94a3b8;margin-top:2px">' + escHtml(sub) + '</p>' : '') + '</div>';
}

function _waiFmtResp(sec) {
  if (!sec) return '';
  if (sec < 60) return Math.round(sec) + 's';
  if (sec < 3600) return Math.round(sec/60) + 'm';
  return (sec/3600).toFixed(1) + 'u';
}

function _waiFmtDate(v) {
  if (!v) return '';
  try {
    return new Date(v).toLocaleString('nl-NL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return String(v).slice(0, 16).replace('T', ' '); }
}

// Eén escalatiekaart — vraag + reden + antwoordveld. `data-wa-esc-id` draagt
// het id waarop waiWaReply/waiWaDismiss terugvallen (`this.closest(...)`),
// zodat er geen losse state buiten de DOM bijgehouden hoeft te worden.
function waiEscalationCard(it) {
  var id = escAttr(String(it.id));
  return '<div class="section-card" style="margin-bottom:8px" data-wa-esc-id="' + id + '">' +
    '<p style="font-size:10px;font-weight:700;color:var(--accent);text-transform:uppercase">' + escHtml(it.project || 'Onbekend project') + ' &middot; ' + escHtml(_waiFmtDate(it.created_at)) + '</p>' +
    '<p style="font-size:13px;color:var(--text);margin-top:4px">' + escHtml(it.question || '') + '</p>' +
    '<p style="font-size:11px;color:var(--text-dim);margin-top:4px">Iris kon dit niet zelf beantwoorden: ' + escHtml(it.reason || '') + '</p>' +
    '<textarea data-wa-esc-text rows="2" placeholder="Typ je antwoord..." style="width:100%;margin-top:6px;padding:8px;border:1px solid var(--card-border);border-radius:8px;font-size:13px;box-sizing:border-box"></textarea>' +
    '<div style="display:flex;gap:8px;margin-top:6px">' +
    '<button class="btn btn-sm btn-primary" style="flex:1" onclick="waiWaReply(this)">Versturen</button>' +
    '<button class="btn btn-sm btn-ghost" onclick="waiWaDismiss(this)">Negeren</button></div></div>';
}

function waiWaReply(btn) {
  var card = btn.closest('[data-wa-esc-id]');
  var ta = card.querySelector('[data-wa-esc-text]');
  var text = (ta.value || '').trim();
  if (!text) { ta.focus(); return; }
  btn.disabled = true; btn.textContent = '…';
  fetch('/api/bridge/whatsapp-reply', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: card.getAttribute('data-wa-esc-id'), text: text }),
  }).then(function(r){ return r.json(); }).then(function(d){
    if (d && d.ok === false) throw new Error(d.detail || d.error || 'mislukt');
    waiLoadWa();
  }).catch(function(e){
    btn.disabled = false; btn.textContent = 'Versturen';
    alert('Versturen mislukt: ' + e.message);
  });
}

function waiWaDismiss(btn) {
  var card = btn.closest('[data-wa-esc-id]');
  btn.disabled = true;
  fetch('/api/bridge/whatsapp-dismiss', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: card.getAttribute('data-wa-esc-id') }),
  }).then(function(r){ return r.json(); }).then(function(d){
    if (d && d.ok === false) throw new Error(d.detail || d.error || 'mislukt');
    waiLoadWa();
  }).catch(function(e){
    btn.disabled = false;
    alert('Negeren mislukt: ' + e.message);
  });
}

// Eén gesprek kan zowel in "Nieuwe contacten" als in "Alle gesprekken" staan
// (zelfde wa_id, twee secties) — de transcript-toggle klapt daarom altijd de
// eerstvolgende <li> open, nooit een lookup op wa_id, anders opent een tik op
// de tweede rij het transcript van de eerste (zelfde valkuil als Iris Remote,
// zie CLAUDE.md 14h).
function waiConversationRow(c) {
  var who = c.contact_name || c.wa_id;
  return '<li style="border-top:1px solid var(--card-border);padding:6px 0;cursor:pointer" onclick="waiWaToggleThread(this)" data-wa-id="' + escAttr(c.wa_id) + '">' +
    '<div style="display:flex;justify-content:space-between;gap:8px"><span style="font-size:12px;font-weight:600;color:var(--text)">' + escHtml(who) + '</span><span style="font-size:11px;color:#64748b">' + escHtml(_waiFmtDate(c.updated_at)) + '</span></div>' +
    '<div style="font-size:11px;color:var(--text-dim);margin-top:2px">' + escHtml(c.project || 'Onbekend project') + (c.is_new ? ' &middot; <span style="color:var(--accent)">nieuw contact</span>' : '') + '</div>' +
    '<div style="font-size:11px;color:#64748b;margin-top:2px">' + c.message_count + ' bericht' + (c.message_count === 1 ? '' : 'en') + (c.open_escalations ? ' &middot; ' + c.open_escalations + ' wacht op jou' : '') + '</div>' +
    '</li><li class="wai-wa-thread" style="display:none;padding:4px 0 8px"></li>';
}

function waiWaToggleThread(row) {
  var slot = row.nextElementSibling;
  if (!slot) return;
  if (slot.style.display !== 'none') { slot.style.display = 'none'; return; }
  slot.style.display = 'block';
  slot.innerHTML = '<p style="font-size:11px;color:#64748b">Laden…</p>';
  fetch('/api/bridge/whatsapp-thread?wa_id=' + encodeURIComponent(row.getAttribute('data-wa-id')))
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d && d.ok === false) throw new Error(d.detail || 'mislukt');
      slot.innerHTML = waiThreadMessagesHtml(d.thread || {});
    }).catch(function(e){
      slot.innerHTML = '<p style="font-size:11px;color:var(--danger-fg)">' + escHtml(e.message) + '</p>';
    });
}

function waiThreadMessagesHtml(thread) {
  var msgs = thread.messages || [];
  if (!msgs.length) return '<p style="font-size:11px;color:#64748b">Nog geen berichten opgeslagen.</p>';
  return msgs.map(function(m){
    var mine = m.role !== 'user';
    var body = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
    return '<div style="background:' + (mine ? 'rgba(124,111,232,0.10)' : 'rgba(255,255,255,0.04)') + ';border-radius:8px;padding:6px 8px;margin-top:4px">' +
      '<p style="font-size:9px;font-weight:700;color:#64748b;text-transform:uppercase">' + (mine ? 'Iris' : escHtml(thread.contact_name || thread.wa_id)) + '</p>' +
      '<p style="font-size:12px;color:var(--text);margin-top:2px;white-space:pre-wrap">' + escHtml(body) + '</p></div>';
  }).join('');
}

function waiLoadWa() {
  Promise.all([
    fetch('/api/bridge/whatsapp-stats').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
    fetch('/api/bridge/whatsapp').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
    fetch('/api/bridge/whatsapp-conversations').then(function(r){return r.json();}).catch(function(){return {ok:false};}),
  ]).then(function(res){
    var host = document.getElementById('wai-whatsapp-host');
    if (host) host.outerHTML = '<div id="wai-whatsapp-host">' + waiWhatsAppBlock(res[0], (res[1] && res[1].escalations) || [], (res[2] && res[2].conversations) || []) + '</div>';
  }).catch(function(e){
    var host = document.getElementById('wai-whatsapp-host');
    if (host) host.outerHTML = '<div id="wai-whatsapp-host"><div class="section-card" style="background:var(--danger-bg);border-color:var(--danger-border)"><span style="font-size:12px;color:var(--danger-fg)">WhatsApp-overzicht laden mislukt: ' + escHtml(e.message) + '</span></div></div>';
  });
}

function waiStartWaPoll() {
  if (_waiWaTimer) clearInterval(_waiWaTimer);
  _waiWaTimer = setInterval(function(){
    if (currentProject !== 'WeAreImpact' || currentTab !== 'Dashboard') { clearInterval(_waiWaTimer); _waiWaTimer = null; return; }
    waiLoadWa();
  }, 60000);
}

// ── 3. Chat "Wat kan ik voor je betekenen" ─────────────────────────────────
function waiChatBlock() {
  return '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
    '<h3 style="font-size:14px;font-weight:700;color:var(--text)">💬 Wat kan ik voor je betekenen?</h3>' +
    '<span style="font-size:11px;color:#94a3b8">Iris · WeAreImpact</span></div>' +
    '<div id="wai-chat-messages" style="max-height:280px;overflow-y:auto;padding:8px;background:#0f172a;border-radius:8px;margin-bottom:8px;font-size:13px;line-height:1.5">' +
    '<div class="chat-msg assistant" id="wai-chat-greeting">Hallo Vincent. Ik ben je WeAreImpact-assistent. Vraag me om een taak uit te voeren, iets te analyseren, of een concept te maken.</div></div>' +
    '<div style="display:flex;gap:6px"><input id="wai-chat-input" placeholder="Typ je vraag of opdracht..." onkeydown="if(event.key===\'Enter\')waiSendChat()" style="flex:1;padding:8px;border:1px solid var(--card-border);border-radius:8px;font-size:13px">' +
    '<button onclick="waiSendChat()" class="btn btn-primary">Verstuur</button></div>' +
    '</div>';
}

async function waiSendChat() {
  var input = document.getElementById('wai-chat-input');
  var msg = input ? input.value.trim() : '';
  if (!msg) return;
  if (input) input.value = '';
  var box = document.getElementById('wai-chat-messages');
  if (!box) return;
  box.innerHTML += '<div class="chat-msg user">' + escHtml(msg) + '</div><div class="chat-msg assistant" id="wai-chat-pending"><em>Iris denkt...</em></div>';
  box.scrollTop = box.scrollHeight;

  var sid = _chatSessionId;
  if (!sid) sid = await ensureChatSession();
  if (!sid) {
    var p = document.getElementById('wai-chat-pending');
    if (p) p.outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Kon geen chatsessie starten.</div>';
    return;
  }
  try {
    var resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sid, message: msg, agent: 'claude', use_obsidian: true }),
    });
    if (!resp.ok) {
      var errText = await resp.text();
      var pe = document.getElementById('wai-chat-pending');
      if (pe) pe.outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Fout: ' + escHtml(errText.slice(0,200)) + '</div>';
      return;
    }
    var pending = document.getElementById('wai-chat-pending');
    if (!pending) return;
    pending.outerHTML = '<div class="chat-msg assistant" id="wai-chat-streaming"><em>Antwoord ontvangen...</em></div>';
    var streamingEl = document.getElementById('wai-chat-streaming');
    if (!streamingEl) return;
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var fullText = '';
    streamingEl.innerHTML = '';
    while (true) {
      var _r = await reader.read();
      if (_r.done) break;
      var chunk = decoder.decode(_r.value, { stream: true });
      var lines = chunk.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li].trim();
        if (!line || line === ':' || line.startsWith(':keepalive')) continue;
        if (line === '[DONE]' || line === 'data: [DONE]') { streamingEl.innerHTML = fullText ? mdToHtmlSimple(fullText) : '(geen antwoord)'; break; }
        if (line.startsWith('data: ')) {
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text' || evt.type === 'thought') {
              fullText += evt.text || '';
              streamingEl.innerHTML = mdToHtmlSimple(fullText);
              box.scrollTop = box.scrollHeight;
            } else if (evt.type === 'error') {
              streamingEl.innerHTML += '<div style="color:var(--red);margin-top:8px">Fout: ' + escHtml(evt.message||'') + '</div>';
            } else if (evt.type === 'tool_start') {
              streamingEl.innerHTML += '<div style="color:var(--text-dim);font-size:11px;margin:4px 0">Gebruik: ' + escHtml(evt.name||'') + '...</div>';
            } else if (evt.type === 'tool_result') {
              streamingEl.innerHTML += '<div style="color:var(--text-muted);font-size:10px;margin:2px 0">' + escHtml(evt.name||'') + ' klaar</div>';
            }
          } catch (e) { /* non-JSON SSE, skip */ }
        }
      }
    }
    streamingEl.id = '';
  } catch (e) {
    var pp = document.getElementById('wai-chat-pending') || document.getElementById('wai-chat-streaming');
    if (pp) pp.outerHTML = '<div class="chat-msg assistant" style="color:var(--red)">Fout: ' + escHtml(e.message) + '</div>';
  }
}

// ── Actiecentrum + activiteit (lokaal op dit scherm) ──────────────────────
function waiRenderActionCenter(data) {
  var el = document.getElementById('wai-ac');
  if (!el) return;
  var items = (data && data.items) || [];
  if (!items.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--ok-fg);background:var(--ok-bg);border:1px solid var(--ok-border);padding:8px 10px;border-radius:8px">Niets wacht op jou voor WeAreImpact. Alles draait.</div>';
    return;
  }
  el.innerHTML = items.slice(0, 12).map(function(it, idx){
    var meta = (_acKindMeta && _acKindMeta[it.kind]) || { pill: 'pill-neutral', label: it.kind };
    var border = (typeof _pillBorderColor === 'function') ? _pillBorderColor(meta.pill) : 'var(--card-border)';
    var actions = (it.actions || []).map(function(a){
      var cls = a.danger ? 'btn-danger-outline' : (a.accent ? 'btn-primary' : (a.type === 'open_tab' || a.type === 'dismiss') ? 'btn-ghost' : 'btn-primary');
      return '<button onclick=\'acAction(this, ' + JSON.stringify(a).replace(/'/g, '&#39;') + ', ' + JSON.stringify(it.project || currentProject) + ')\' class="btn btn-sm ' + cls + '">' + escHtml(a.label) + '</button>';
    }).join('');
    return '<div style="padding:8px 4px 8px 12px;border-bottom:1px solid #f1f5f9;border-left:3px solid ' + border + '">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px"><span class="pill ' + meta.pill + '">' + ((_acKindMeta && _acKindMeta[it.kind]) ? _acKindMeta[it.kind].icon + ' ' : '') + escHtml(meta.label) + '</span></div>' +
      '<p style="font-size:13px;font-weight:600;color:var(--text);margin:2px 0">' + escHtml(it.title) + '</p>' +
      '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(it.summary || '') + '</p>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' + actions + '</div></div>';
  }).join('');
}

function waiRenderActivity(rows) {
  var el = document.getElementById('wai-activity');
  if (!el) return;
  if (!rows || !rows.length) { el.innerHTML = '<div style="color:#64748b;text-align:center;padding:12px">Nog geen activiteit</div>'; return; }
  el.innerHTML = rows.map(function(l){
    var time = (l.created_at||'').slice(11,19);
    var icon = '○', color = '#64748b';
    if (l.action === 'task_done' || l.action === 'goal_done' || l.action === 'phase_done' || l.action === 'live') { icon = '✓'; color = '#22c55e'; }
    else if (l.action === 'task_failed' || l.action === 'goal_error') { icon = '✗'; color = '#ef4444'; }
    else if (l.action === 'task_start' || l.action === 'goal_start' || l.action === 'phase_start') { icon = '▶'; color = '#60a5fa'; }
    return '<div style="display:flex;align-items:flex-start;gap:6px;padding:3px 6px;border-bottom:1px solid #1e293b;line-height:1.5"><span style="color:' + color + ';flex-shrink:0;width:14px;text-align:center">' + icon + '</span><span style="color:#64748b;flex-shrink:0;width:50px">' + time + '</span><span style="color:#94a3b8;word-break:break-word">' + escHtml((l.detail||'').slice(0,120)) + '</span></div>';
  }).join('');
}

function waiLoadActivity() {
  fetch('/api/projects/' + encodeURIComponent(currentProject) + '/activity?limit=12').then(function(r){return r.json();}).then(waiRenderActivity).catch(function(){});
}

function waiStartAdvicePoll(proj) {
  if (_waiAdviceTimer) clearInterval(_waiAdviceTimer);
  _waiAdviceTimer = setInterval(function(){
    if (currentProject !== 'WeAreImpact' || currentTab !== 'Dashboard') { clearInterval(_waiAdviceTimer); _waiAdviceTimer = null; return; }
    fetch('/api/projects/' + proj + '/advice?days=28').then(function(r){return r.json();}).then(function(advice){
      if (currentProject !== 'WeAreImpact') return;
      var b = document.getElementById('wai-banner');
      if (b && typeof renderAdviceBanner === 'function') b.innerHTML = renderAdviceBanner(advice);
      var t = document.getElementById('wai-best-task');
      if (t) t.textContent = (advice && advice.next_step) || 'Klaar — geen open taak.';
      waiRebuildBestActions(advice);
    }).catch(function(){});
  }, 30000);
}
