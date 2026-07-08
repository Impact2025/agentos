// ── Agent OS — tabs: Content, Kansen, Wachtrij, Concurrentie, Keywords, Technisch, Activiteit, Doelen
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// ═══════════════════════════════════════════════════════════════════
//  CONTENT TAB
// ═══════════════════════════════════════════════════════════════════
async function renderContentTab(el) {
  if (currentProject === 'Finance Expert') { el.innerHTML = '<div class="empty-state">Geen content</div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Content laden...</p></div>';
  try { var d = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  var html = '<div class="grid-2">';
  var gsc = d.gsc_pages||[];
  html += '<div class="section-card"><h3>Live pagina\'s (' + gsc.length + ')</h3>' + (gsc.length ? '<table class="data-table"><thead><tr><th>Pagina</th><th class="num">Clicks</th><th class="num">Positie</th></tr></thead><tbody>' + gsc.slice(0,20).map(function(p){return '<tr><td class="url-cell"><span class="badge badge-live">live</span> ' + escHtml(p.title) + '</td><td class="num">' + p.clicks + '</td><td class="num">' + (typeof p.position==='number'?p.position.toFixed(1):p.position) + '</td></tr>';}).join('') + '</tbody></table>' + (gsc.length>20?'<p style="font-size:11px;color:#94a3b8;margin-top:6px">+ nog ' + (gsc.length-20) + '</p>':''):'<p style="color:#94a3b8;font-size:12px;padding:16px;text-align:center">Geen GSC-data</p>') + '</div>';
  var cf=d.content_files||[], le=d.log_entries||[], zf=d.zzp_opdrachten||[];
  function fileRow(kind, badgeClass, badgeLabel, f) {
    return '<div onclick="openContentFile(\''+kind+'\',\''+encodeURIComponent(f.name)+'\')" style="font-size:12px;padding:3px 0;cursor:pointer;color:#2563eb" onmouseover="this.style.textDecoration=\'underline\'" onmouseout="this.style.textDecoration=\'none\'"><span class="badge '+badgeClass+'" style="color:inherit;text-decoration:none">'+badgeLabel+'</span> '+escHtml(f.name.replace(/-/g,' '))+'</div>';
  }
  html += '<div class="section-card"><h3>Lokale bestanden (' + (cf.length+le.length+zf.length) + ')</h3>' + (cf.length?'<p style="font-size:11px;color:#64748b;margin-bottom:4px">Concepten (klik om te lezen)</p>'+cf.map(function(f){return fileRow('content','badge-draft','concept',f);}).join(''):'') + (le.length?'<p style="font-size:11px;color:#64748b;margin:8px 0 4px">Logboek</p>'+le.map(function(f){return fileRow('log','badge-log','log',f);}).join(''):'') + (zf.length?'<p style="font-size:11px;color:#64748b;margin:8px 0 4px">ZZP</p>'+zf.map(function(f){return fileRow('zzp','badge-zzp','zzp',f);}).join(''):'') + (!cf.length&&!le.length&&!zf.length?'<p style="color:#94a3b8;font-size:12px;padding:16px;text-align:center">Geen lokale bestanden</p>':'') + '</div></div>';
  html += '<div class="section-card"><h3>Blog suggesties</h3><div id="sug-container">' + (weSuggestions.length ? renderSuggestions() : '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Klik op "Genereer suggesties" voor AI-blog-onderwerpen.</p>') + '</div><button onclick="generateSuggestions()" id="sug-btn" style="margin-top:10px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">Genereer suggesties</button></div>';
  el.innerHTML = html;
}
async function openContentFile(kind, encodedName) {
  var name = decodeURIComponent(encodedName);
  var overlay = document.createElement('div');
  overlay.id = 'file-modal-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.5);display:flex;align-items:center;justify-content:center;z-index:1000;padding:24px';
  var footer = kind === 'content' ? (
    '<div style="display:flex;gap:8px;flex-wrap:wrap;padding:10px 16px;border-top:1px solid #e2e8f0;background:#f8fafc">' +
    '<button onclick="analyzeContentFile(\''+kind+'\',\''+encodedName+'\')" style="padding:6px 14px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">🔍 Analyseer als SEO-expert</button>' +
    '<button onclick="generateSocialCopyForFile(\''+kind+'\',\''+encodedName+'\')" style="padding:6px 14px;background:#0891b2;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">📣 Maak social media teksten</button>' +
    '</div>'
  ) : '';
  overlay.innerHTML = '<div style="background:#fff;border-radius:10px;max-width:800px;width:100%;max-height:85vh;display:flex;flex-direction:column;overflow:hidden">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #e2e8f0"><h3 style="font-size:14px;font-weight:700">'+escHtml(name.replace(/-/g,' '))+'</h3><button onclick="closeContentFile()" style="background:none;border:none;font-size:18px;cursor:pointer;color:#64748b;line-height:1">✕</button></div>' +
    '<div style="overflow:auto;flex:1"><div id="file-modal-body" style="padding:16px;font-size:13px;line-height:1.6">Laden...</div><div id="file-modal-results" style="padding:0 16px 16px"></div></div>' +
    footer + '</div>';
  overlay.addEventListener('click', function(e){ if (e.target === overlay) closeContentFile(); });
  document.body.appendChild(overlay);
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(name));
    var data = await res.json();
    var body = document.getElementById('file-modal-body');
    if (!res.ok) { body.innerHTML = '<p style="color:#dc2626">'+escHtml(data.detail||'Kon bestand niet laden')+'</p>'; return; }
    if (data.extension === '.html') {
      body.innerHTML = data.content.replace(/^---[\s\S]*?---\n*/, '');
    } else {
      body.innerHTML = '<pre style="white-space:pre-wrap;font-family:inherit">'+escHtml(data.content)+'</pre>';
    }
  } catch(e) {
    var body = document.getElementById('file-modal-body'); if (body) body.innerHTML = '<p style="color:#dc2626">Fout: '+escHtml(e.message)+'</p>';
  }
}
function closeContentFile() {
  var overlay = document.getElementById('file-modal-overlay');
  if (overlay) overlay.remove();
}
async function analyzeContentFile(kind, encodedName) {
  var results = document.getElementById('file-modal-results'); if (!results) return;
  results.innerHTML = '<div style="margin-top:10px;padding:10px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b">SEO-expert analyseert...</div>';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/analyze?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">'+escHtml(data.detail||'Analyse mislukt')+'</div>'; return; }
    var color = data.score>=85?'#16a34a':(data.score>=60?'#d97706':'#dc2626');
    results.innerHTML = '<div style="margin-top:10px;padding:12px;border:1px solid #e2e8f0;border-radius:8px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">' +
      '<span style="font-size:13px;font-weight:700;color:'+color+'">Score: '+data.score+'/100'+(data.score>=85?' 🏆':'')+'</span>' +
      (data.score<85?'<button onclick="applyContentFileFeedback(this,\''+kind+'\',\''+encodedName+'\')" style="padding:5px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">✓ Pas toe</button>':'') +
      '</div>' +
      '<div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(data.feedback||'(geen feedback)')+'</div></div>';
  } catch(e) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">Fout: '+escHtml(e.message)+'</div>'; }
}
async function applyContentFileFeedback(btn, kind, encodedName) {
  var results = document.getElementById('file-modal-results');
  var origLabel = btn.textContent;
  btn.disabled = true; btn.textContent = 'Toepassen (kan ~30-60s duren)...';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/optimize?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { alert('Toepassen mislukt: ' + (data.detail||'onbekende fout')); btn.disabled = false; btn.textContent = origLabel; return; }
    // Ververs zowel het artikel als de score met de nieuwe, opgeslagen versie
    var bodyEl = document.getElementById('file-modal-body');
    if (bodyEl) bodyEl.innerHTML = data.extension === '.html' ? data.content.replace(/^---[\s\S]*?---\n*/, '') : '<pre style="white-space:pre-wrap;font-family:inherit">'+escHtml(data.content)+'</pre>';
    var color = data.score>=85?'#16a34a':(data.score>=60?'#d97706':'#dc2626');
    if (results) results.innerHTML = '<div style="margin-top:10px;padding:12px;border:1px solid #e2e8f0;border-radius:8px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">' +
      '<span style="font-size:13px;font-weight:700;color:'+color+'">Nieuwe score: '+data.score+'/100'+(data.score>=85?' 🏆':'')+' (na '+data.rounds+' ronde'+(data.rounds!==1?'n':'')+')</span>' +
      (data.score<85?'<button onclick="applyContentFileFeedback(this,\''+kind+'\',\''+encodedName+'\')" style="padding:5px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">✓ Nogmaals toepassen</button>':'') +
      '</div><div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(data.feedback||'(geen feedback)')+'</div></div>';
  } catch(e) { alert('Fout: ' + e.message); btn.disabled = false; btn.textContent = origLabel; }
}
async function generateSocialCopyForFile(kind, encodedName) {
  var results = document.getElementById('file-modal-results'); if (!results) return;
  results.innerHTML = '<div style="margin-top:10px;padding:10px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b">Social teksten genereren...</div>';
  try {
    var res = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-file/social-copy?kind=' + encodeURIComponent(kind) + '&file=' + encodeURIComponent(decodeURIComponent(encodedName)), {method:'POST'});
    var data = await res.json();
    if (!res.ok) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">'+escHtml(data.detail||'Genereren mislukt')+'</div>'; return; }
    window._fileSocialCopy = data.social_copy || {};
    var platforms = [['linkedin','LinkedIn'],['facebook','Facebook'],['instagram','Instagram'],['twitter','X / Twitter']];
    results.innerHTML = '<div style="margin-top:10px;display:flex;flex-direction:column;gap:8px">' + platforms.map(function(p){
      var text = window._fileSocialCopy[p[0]] || '(geen tekst)';
      return '<div style="border:1px solid #e2e8f0;border-radius:8px;padding:10px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-size:11px;font-weight:700;color:#475569">'+p[1]+'</span><button onclick="copySocialText(this,\''+p[0]+'\')" style="padding:2px 8px;background:#fff;border:1px solid #cbd5e1;border-radius:4px;font-size:10px;cursor:pointer">Kopieer</button></div><div style="font-size:12px;color:#334155;white-space:pre-wrap">'+escHtml(text)+'</div></div>';
    }).join('') + '</div>';
  } catch(e) { results.innerHTML = '<div style="margin-top:10px;color:#dc2626;font-size:12px">Fout: '+escHtml(e.message)+'</div>'; }
}
function copySocialText(btn, platform) {
  var text = (window._fileSocialCopy || {})[platform] || '';
  navigator.clipboard.writeText(text).then(function(){
    var orig = btn.textContent; btn.textContent = 'Gekopieerd ✓';
    setTimeout(function(){ btn.textContent = orig; }, 1500);
  });
}
function renderSuggestions() {
  if (!weSuggestions.length) return '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen suggesties</p>';
  return weSuggestions.map(function(sug,i){return '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:10px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:flex-start"><div><p style="font-weight:600;font-size:13px">' + escHtml(sug.title) + '</p><p style="font-size:11px;color:#64748b;margin-top:2px">' + escHtml(sug.rationale) + '</p><div style="display:flex;gap:6px;margin-top:4px"><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.keyword) + '</span><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.estimated_hours||'?') + '</span></div></div><button onclick="publishSuggestion(this,' + i + ')" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:4px;font-size:11px;cursor:pointer">Schrijf &amp; publiceer</button></div></div>';}).join('');
}
async function generateSuggestions() {
  var btn = document.getElementById('sug-btn'); if (!btn) return;
  btn.disabled = true; btn.textContent = 'Genereren...';
  try {
    var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/suggest-blogs?days=28', {method:'POST'})).json();
    weSuggestions = data.suggestions || [];
    var cont = document.getElementById('sug-container'); if (cont) cont.innerHTML = renderSuggestions();
  } catch(e) { alert('Fout: ' + e.message); }
  btn.disabled = false; btn.textContent = 'Genereer suggesties';
}
async function publishSuggestion(btn, index) {
  var sug = weSuggestions[index]; if (!sug) return;
  if (!confirm('Schrijf artikel: "' + sug.title + '"?')) return;
  try {
    var result = await runArticlePipeline({title:sug.title, rationale:sug.rationale, keyword:sug.keyword}, btn);
    if (result.success) {
      weSuggestions.splice(index,1); var cont = document.getElementById('sug-container'); if (cont) cont.innerHTML = renderSuggestions();
      var msg = 'Artikel opgeslagen als ' + result.local_path;
      if (result.word_count) msg += ' (' + result.word_count + ' woorden)';
      msg += '\n' + formatSeoResultMsg(result);
      if (result.ping_results) { var pings=[]; for(var k in result.ping_results) pings.push(k+': '+(result.ping_results[k]===200?'OK':result.ping_results[k])); if(pings.length) msg+='\nPings: '+pings.join(', '); }
      alert(msg);
    } else alert('Mislukt: ' + (result.detail||'onbekend'));
  } catch(e) { alert('Fout: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════════════
//  KANSEN TAB
// ═══════════════════════════════════════════════════════════════════
function renderOppStepper(status) {
  if (status === 'dismissed') {
    return '<div style="display:flex;align-items:center;gap:6px;margin:8px 0;padding:4px 8px;background:#f8fafc;border-radius:6px;font-size:11px;color:#94a3b8">⊘ Genegeerd — niet in behandeling</div>';
  }
  var steps = [['new','Nieuw'],['in_progress','In behandeling'],['published','Gepubliceerd']];
  var idx = 0;
  steps.forEach(function(s,i){ if (s[0]===status) idx=i; });
  var html = '<div style="display:flex;align-items:flex-start;margin:10px 0 8px">';
  steps.forEach(function(s,i){
    var done = i < idx, current = i === idx;
    var circleBg = done ? '#4f46e5' : '#fff';
    var circleBorder = (done||current) ? '#4f46e5' : '#e2e8f0';
    var inner = done ? '<span style="color:#fff;font-size:9px;line-height:1">✓</span>' : (current ? '<span style="width:6px;height:6px;border-radius:50%;background:#4f46e5;display:block"></span>' : '');
    html += '<div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:64px">' +
      '<div style="width:16px;height:16px;border-radius:50%;background:'+circleBg+';border:2px solid '+circleBorder+';display:flex;align-items:center;justify-content:center">'+inner+'</div>' +
      '<span style="font-size:10px;font-weight:'+(current?'700':'500')+';color:'+(current?'#1e293b':(done?'#475569':'#94a3b8'))+'">'+s[1]+'</span>' +
      '</div>';
    if (i < steps.length-1) html += '<div style="flex:1;height:2px;background:'+(i<idx?'#4f46e5':'#e2e8f0')+';margin:7px 2px 0"></div>';
  });
  html += '</div>';
  return html;
}
async function renderKansenTab(el) {
  if (scanningInProgress) { el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Scan bezig... GSC-data ophalen + AI-analyse (20-60 sec).</p></div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Kansen laden...</p></div>';
  try { var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen' + (oppStatusFilter?'?status='+oppStatusFilter:''))).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (data.error) { el.innerHTML = '<div class="empty-state">' + escHtml(data.error) + '</div>'; return; }
  var kansen = data.kansen || [];
  window._kansenData = kansen;
  var newCount = kansen.filter(function(o){return o.status==='new';}).length;
  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px"><div><h3 style="font-size:15px;font-weight:700">Striking distance kansen (' + kansen.length + ')</h3>' + (newCount>0?'<p style="font-size:11px;color:#64748b;margin-top:2px">' + newCount + ' nieuwe kansen</p>':'') + '</div><div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<select id="kansen-filter" onchange="oppStatusFilter=this.value;renderKansenTab(document.getElementById(\'tab-content\'))" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff">' +
    '<option value="">Alle</option><option value="new">Nieuw (' + kansen.filter(function(o){return o.status==='new';}).length + ')</option><option value="in_progress">In behandeling</option><option value="published">Gepubliceerd</option><option value="dismissed">Genegeerd</option></select>' +
    (newCount>=2?'<button onclick="writeAllNewKansen(this)" style="padding:4px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Schrijf alle ' + newCount + '</button>':'') +
    '<button onclick="runDemandScan()" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Scan uitvoeren</button></div></div>';
  if (!kansen.length) { el.innerHTML = html + '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen kansen</p><p style="color:#94a3b8">Voer een scan uit</p></div>'; return; }
  kansen.forEach(function(opp, idx) {
    var sc = ({new:'#dbeafe',in_progress:'#fef3c7',published:'#dcfce7',dismissed:'#f1f5f9'})[opp.status]||'#f1f5f9';
    var st = ({new:'Nieuw',in_progress:'In behandeling',published:'Gepubliceerd',dismissed:'Genegeerd'})[opp.status]||opp.status;
    var score = typeof opp.opportunity_score==='number'?opp.opportunity_score.toFixed(0):opp.opportunity_score;
    var pos = typeof opp.position==='number'?opp.position:10;
    var GOAL_POS = 3;
    var posPct = function(p){ return Math.max(0, Math.min(100, ((20-p)/19)*100)); };
    var curPct = posPct(pos), goalPct = posPct(GOAL_POS), atGoal = pos <= GOAL_POS;
    var barColor = atGoal ? '#16a34a' : (pos<=10?'#4f46e5':'#d97706');
    html += '<div class="opp-card" style="'+(opp.status==='new'?'border-left:3px solid #4f46e5;':'')+'"><div class="opp-header"><div style="flex:1"><div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><p class="opp-query">'+escHtml(opp.query)+'</p><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+sc+';font-weight:600">'+st+'</span><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+(opp.action==='re-optimaliseren'?'#fef3c7':'#dbeafe')+'">'+(opp.action==='re-optimaliseren'?'Heroptimaliseren':'Nieuwe content')+'</span></div>' +
    '<div class="opp-meta"><span style="color:#16a34a;font-weight:600">'+opp.clicks+' clicks</span><span>'+opp.impressions+' impressies</span><span>Pos. '+pos.toFixed(1)+'</span><span style="font-weight:600">Score '+score+'</span></div></div></div>' +
    (opp.angle?'<div class="opp-angle" style="margin-top:6px">'+escHtml(opp.angle)+'</div>':'') + (opp.rationale?'<div class="opp-rationale" style="margin-top:4px">'+escHtml(opp.rationale)+'</div>':'') +
    renderOppStepper(opp.status) +
    '<div style="margin:2px 0 8px">' +
      '<div style="display:flex;align-items:flex-end;margin-bottom:1px">' +
        '<div style="width:50px"></div>' +
        '<div style="flex:1;position:relative;height:13px">' +
          '<span style="position:absolute;left:'+goalPct+'%;transform:translateX(-50%);font-size:9px;font-weight:700;color:#059669;white-space:nowrap">▼ doel: top 3</span>' +
        '</div>' +
        '<div style="width:64px"></div>' +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:8px">' +
        '<span style="font-size:10px;color:#94a3b8;width:50px">Pos. '+pos.toFixed(1)+'</span>' +
        '<div style="flex:1;position:relative;height:6px;background:#e2e8f0;border-radius:3px">' +
          '<div style="position:absolute;top:0;left:0;height:100%;width:'+curPct+'%;background:'+barColor+';border-radius:3px;transition:width .3s"></div>' +
          '<div style="position:absolute;top:-2px;left:'+goalPct+'%;width:2px;height:10px;background:#059669;transform:translateX(-1px)"></div>' +
        '</div>' +
        '<span style="font-size:10px;width:64px;text-align:right;color:'+(atGoal?'#16a34a':'#94a3b8')+';font-weight:'+(atGoal?'700':'400')+'">'+(atGoal?'✓ doel bereikt':'positie 1 →')+'</span>' +
      '</div>' +
    '</div>' +
    '<div class="opp-actions" style="display:flex;gap:6px;flex-wrap:wrap">' +
    ((opp.status==='new'||opp.status==='in_progress')?'<button onclick="writeArticleFromOpp(this,'+idx+')" style="padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Schrijf artikel</button>':'') +
    (opp.status==='new'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'in_progress\')" style="padding:5px 12px;background:#fff;color:#92400e;border:1.5px solid #f59e0b;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">→ Pak aan</button>':'') +
    (opp.status==='in_progress'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'published\')" style="padding:5px 12px;background:#fff;color:#166534;border:1.5px solid #16a34a;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✓ Markeer gepubliceerd</button>':'') +
    (opp.status!=='dismissed'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'dismissed\')" style="padding:5px 12px;background:#fff;color:#475569;border:1.5px solid #cbd5e1;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✕ Negeren</button>':'') +
    (opp.status==='dismissed'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'new\')" style="padding:5px 12px;background:#fff;color:#1e40af;border:1.5px solid #3b82f6;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">↺ Heropen</button>':'') +
    '</div></div>';
  });
  el.innerHTML = html;
}
async function writeArticleFromOpp(btn, idx) {
  var opp = window._kansenData && window._kansenData[idx]; if (!opp) { alert('Kans niet gevonden'); return; }
  if (!confirm('Schrijf artikel voor kans: "'+opp.query+'"?')) return;
  try {
    var result = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, btn);
    if (result.success) {
      await fetch('/api/demand/opportunities/'+opp.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'in_progress'}) });
      alert('Artikel opgeslagen: '+result.local_path+'\n'+formatSeoResultMsg(result));
      renderKansenTab(document.getElementById('tab-content'));
    } else alert('Mislukt: '+(result.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}
async function writeAllNewKansen(btn) {
  if (!confirm('Schrijf artikelen voor ALLE nieuwe kansen? Dit kan even duren.')) return;
  var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen')).json();
  var newKansen = (data.kansen||[]).filter(function(o){return o.status==='new';});
  if (!newKansen.length) { alert('Geen nieuwe kansen'); return; }
  if (btn) btn.disabled = true;
  var written = 0;
  for (var i=0; i<newKansen.length; i++) {
    var opp = newKansen[i];
    var prefix = 'Artikel ' + (i+1) + '/' + newKansen.length + ': ';
    var fakeBtn = btn ? {
      style: {},
      set textContent(v) { btn.textContent = prefix + v; },
      get textContent() { return btn.textContent; }
    } : null;
    try {
      var wr = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, fakeBtn);
      if (wr.success) { await fetch('/api/demand/opportunities/'+opp.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'in_progress'}) }); written++; }
    } catch(e) {}
  }
  alert(written+'/'+newKansen.length+' artikelen geschreven.'); renderKansenTab(document.getElementById('tab-content'));
}
async function updateOppStatus(oppId, status) {
  try { await fetch('/api/demand/opportunities/'+oppId, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:status}) }); renderKansenTab(document.getElementById('tab-content')); }
  catch(e) { alert('Fout: '+e.message); }
}
async function runDemandScan() {
  try {
    var sites = await (await fetch('/api/sites')).json();
    var norm = function(s){return s.name.toLowerCase().replace(/ /g,'').replace(/-/g,'');};
    var target = norm({name:currentProject});
    var site = sites.find(function(s){return norm(s) === target;});
    if (!site) { alert('Site niet gevonden voor project: ' + currentProject); return; }
    scanningInProgress = true; var el = document.getElementById('tab-content'); if (el) renderKansenTab(el);
    await fetch('/api/demand/scan', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_id:site.id, days:90}) });
    var attempts = 0;
    var poll = setInterval(async function() {
      attempts++;
      try {
        var cd = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen')).json();
        if ((cd.kansen && cd.kansen.length > 0) || attempts >= 12) { clearInterval(poll); scanningInProgress = false; renderKansenTab(document.getElementById('tab-content')); }
        if (scanningInProgress && el) el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Scan bezig... ' + (attempts*5) + 's</p></div>';
      } catch(e) { clearInterval(poll); scanningInProgress = false; renderKansenTab(document.getElementById('tab-content')); }
    }, 5000);
  } catch(e) { alert('Fout: '+e.message); scanningInProgress = false; }
}

// ═══════════════════════════════════════════════════════════════════
//  WACHTRIJ TAB — auto-gegenereerde blog + social-copy, wacht op goedkeuring
//  (2x/week scheduler zet hier concepten klaar; NOOIT automatisch gepost)
// ═══════════════════════════════════════════════════════════════════
var wachtrijStatusFilter = 'pending_review';
var wachtrijPlatformLabels = { linkedin: 'LinkedIn', facebook: 'Facebook', instagram: 'Instagram', twitter: 'X / Twitter' };

async function renderWachtrijTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Wachtrij laden...</p></div>';
  var jobs;
  try { jobs = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue' + (wachtrijStatusFilter ? '?status=' + wachtrijStatusFilter : ''))).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  window._wachtrijJobs = jobs || [];

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">' +
    '<div><h3 style="font-size:15px;font-weight:700">Content-wachtrij</h3>' +
    '<p style="font-size:11px;color:#64748b;margin-top:2px">2x/week (di + vr) zet de scheduler hier automatisch een concept klaar. Niets gaat live zonder jouw goedkeuring.</p></div>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<select id="wachtrij-filter" onchange="wachtrijStatusFilter=this.value;renderWachtrijTab(document.getElementById(\'tab-content\'))" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff">' +
    '<option value="pending_review"' + (wachtrijStatusFilter==='pending_review'?' selected':'') + '>Te reviewen</option>' +
    '<option value="needs_work"' + (wachtrijStatusFilter==='needs_work'?' selected':'') + '>Onder kwaliteitsgrens</option>' +
    '<option value="published"' + (wachtrijStatusFilter==='published'?' selected':'') + '>Gepubliceerd</option>' +
    '<option value="rejected"' + (wachtrijStatusFilter==='rejected'?' selected':'') + '>Afgewezen</option>' +
    '<option value=""' + (wachtrijStatusFilter===''?' selected':'') + '>Alle</option></select>' +
    '<button onclick="runWachtrijNow(this)" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Genereer nu</button></div></div>';

  if (!jobs || !jobs.length) {
    el.innerHTML = html + '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Niets te reviewen</p><p style="color:#94a3b8">Wacht op de volgende scheduler-run (di/vr 09:00) of klik "Genereer nu"</p></div>';
    return;
  }

  jobs.forEach(function(job, idx) {
    var sc = ({pending_review:'#dbeafe',published:'#dcfce7',rejected:'#fee2e2'})[job.status]||'#f1f5f9';
    var st = ({pending_review:'Te reviewen',published:'Gepubliceerd',rejected:'Afgewezen'})[job.status]||job.status;
    var score = typeof job.seo_score==='number'?job.seo_score.toFixed(0):job.seo_score;
    html += '<div class="opp-card"><div class="opp-header"><div style="flex:1"><div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
      '<p class="opp-query">' + escHtml(job.title) + '</p>' +
      '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:' + sc + ';font-weight:600">' + st + '</span></div>' +
      '<div class="opp-meta"><span>Zoekwoord: ' + escHtml(job.keyword||'-') + '</span><span style="font-weight:600">SEO-score ' + score + '/100</span></div></div></div>' +
      renderSeoWorldclassBadge(job.seo_worldclass) +
      renderQcReport(job.qc_report) +
      '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:11px;color:#4f46e5;font-weight:600">Blog-voorbeeld</summary>' +
      '<div class="prose-dark" style="margin-top:6px;padding:10px;background:#f8fafc;border-radius:6px;max-height:260px;overflow:auto;font-size:12px">' + (job.blog_html||'') + '</div></details>' +
      '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px">' +
      Object.keys(wachtrijPlatformLabels).map(function(p) {
        var copy = (job.social_copy||{})[p];
        if (!copy) return '';
        return '<details style="flex:1;min-width:200px;background:#f8fafc;border-radius:6px;padding:8px"><summary style="cursor:pointer;font-size:11px;font-weight:600;color:#475569">' + wachtrijPlatformLabels[p] + '</summary>' +
          '<div style="white-space:pre-wrap;font-size:11px;color:#334155;margin-top:6px">' + escHtml(copy) + '</div></details>';
      }).join('') + '</div>' +
      (job.image_path ? '<img src="data:image/png;base64,' + job.image_path + '" style="margin-top:8px;max-width:180px;border-radius:6px;border:1px solid #e2e8f0" />' : '') +
      (job.status==='pending_review' ? '<div class="opp-actions" style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' +
        '<button onclick="approveWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Goedkeuren &amp; publiceren</button>' +
        '<button onclick="regenerateWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:6px;font-size:11px;cursor:pointer">Opnieuw genereren</button>' +
        '<button onclick="rejectWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Afwijzen</button></div>' : '') +
      (job.status==='published' && job.publish_result ? '<div style="margin-top:8px;font-size:11px;color:#64748b">' + renderPublishResult(job.publish_result) + '</div>' : '') +
      '</div>';
  });
  el.innerHTML = html;
}

function renderSeoWorldclassBadge(wc) {
  if (!wc || typeof wc !== 'object') return '';
  var sc = wc.score || 0;
  var bg = sc >= 85 ? '#dcfce7' : (sc >= 70 ? '#fef3c7' : '#fee2e2');
  var fg = sc >= 85 ? '#166534' : (sc >= 70 ? '#92400e' : '#b91c1c');
  function v(ok, label) {
    return '<span style="font-size:10px;padding:2px 7px;border-radius:6px;background:' +
      (ok ? '#dcfce7' : '#fee2e2') + ';color:' + (ok ? '#166534' : '#b91c1c') +
      ';font-weight:600">' + (ok ? '✓' : '✗') + ' ' + label + '</span>';
  }
  return '<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;align-items:center">' +
    '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:' + bg + ';color:' + fg + ';font-weight:700">Wereldklasse ' + sc + '/100</span>' +
    v(wc.has_direct_answer, 'Direct-antwoord') +
    v((wc.faq_count || 0) > 0, 'FAQ (' + (wc.faq_count || 0) + ')') +
    v((wc.ee_at_issues || []).length === 0, 'E-E-A-T') +
    '</div>';
}

// QC-checklist van de meertraps-generator (outline/secties/links/AI-taal/CTA/keyword)
function renderQcReport(qc) {
  if (!qc || typeof qc !== 'object' || !Object.keys(qc).length) return '';
  var chips = [];
  function chip(ok, label, fixed) {
    var bg = ok ? '#dcfce7' : '#fee2e2', fg = ok ? '#166534' : '#b91c1c';
    return '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:' + bg + ';color:' + fg + ';font-weight:600">' +
      (ok ? '✓' : '✗') + ' ' + label + (fixed ? ' (gefixt)' : '') + '</span>';
  }
  if (qc.staged === false) {
    chips.push('<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:#fef3c7;color:#92400e;font-weight:600">single-shot (pipeline-terugval)</span>');
  } else {
    if (qc.outline_sections) chips.push(chip(true, qc.outline_sections + ' secties'));
    if (qc.ai_language) chips.push(chip(qc.ai_language.pass, 'AI-taal', qc.ai_language.fixed));
    if (qc.cta && qc.cta.configured) chips.push(chip(qc.cta.pass, 'CTA', qc.cta.fixed));
    if (qc.keyword) chips.push(chip(qc.keyword.pass, 'Zoekwoord', qc.keyword.fixed));
    if (qc.links) chips.push(chip((qc.links.internal_added||0) > 0, (qc.links.internal_added||0) + ' interne + ' + (qc.links.external_added||0) + ' externe links' + (qc.links.stripped ? ' · ' + qc.links.stripped + ' verzonnen link(s) verwijderd' : '')));
    if (qc.case_study && qc.case_study.title) chips.push('<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:#e0e7ff;color:#3730a3;font-weight:600">📎 Casestudy: ' + escHtml(qc.case_study.title) + '</span>');
  }
  if (!chips.length) return '';
  return '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px">' + chips.join('') + '</div>';
}

function renderPublishResult(pr) {
  var parts = [];
  if (pr.netlify && pr.netlify.url) parts.push('Netlify: <a href="' + pr.netlify.url + '" target="_blank">' + pr.netlify.url + '</a>');
  if (pr.gsc && pr.gsc.status) parts.push('GSC: ' + pr.gsc.status);
  if (pr.bing && pr.bing.status_code) parts.push('Bing: ' + pr.bing.status_code);
  if (pr.indexnow && pr.indexnow.status) parts.push('IndexNow: ' + pr.indexnow.status);
  if (pr.google_indexing && pr.google_indexing.status) parts.push('Google Indexing: ' + pr.google_indexing.status);
  if (pr.social) {
    Object.keys(pr.social).forEach(function(p) {
      var r = pr.social[p];
      parts.push((wachtrijPlatformLabels[p]||p) + ': ' + (r.success ? 'gepost' : 'mislukt (' + escHtml((r.error||'').slice(0,80)) + ')'));
    });
  }
  return parts.join(' · ');
}

async function runWachtrijNow(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/run-now', { method: 'POST' })).json();
    if (resp.success) { wachtrijStatusFilter = 'pending_review'; renderWachtrijTab(document.getElementById('tab-content')); }
    else alert(resp.detail || 'Geen nieuwe kansen — voer eerst een Demand Engine-scan uit.');
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Genereer nu'; }
}

async function approveWachtrijJob(btn, jobId) {
  if (!confirm('Artikel publiceren op de website. Socials lopen best-effort mee (een falend platform, bv. LinkedIn, blokkeert niet). Doorgaan?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Publiceren...'; }
  try {
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/approve', { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) { alert('Mislukt: ' + (data.detail || 'onbekende fout')); if (btn) btn.disabled = false; return; }
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); if (btn) btn.disabled = false; }
}

async function rejectWachtrijJob(btn, jobId) {
  if (!confirm('Dit concept afwijzen?')) return;
  try {
    await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/reject', { method: 'POST' });
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function regenerateWachtrijJob(btn, jobId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Herschrijven...'; }
  try {
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/regenerate', { method: 'POST' });
    if (!resp.ok) { var data = await resp.json(); alert('Mislukt: ' + (data.detail || 'onbekende fout')); if (btn) btn.disabled = false; return; }
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); if (btn) btn.disabled = false; }
}

// ═══════════════════════════════════════════════════════════════════
//  CONCURRENTIE TAB (Trends + PageSpeed)
// ═══════════════════════════════════════════════════════════════════
async function renderConcurrentieTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Trends laden...</p></div>';
  try {
    var [trendResp, speedResp, gapResp] = await Promise.all([
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/trends?days=28'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/pagespeed?strategy=mobile'),
      fetch('/api/projects/' + encodeURIComponent(currentProject) + '/keyword-gaps?days=28'),
    ]);
    var trends = await trendResp.json(), speed = await speedResp.json(), gaps = await gapResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Concurrentie &amp; Analyse</h3></div>';

  // ── Trend grafieken ──
  html += '<div class="grid-2">';
  html += '<div class="section-card"><h3>Klikken (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-clicks"></canvas></div></div>';
  html += '<div class="section-card"><h3>Impressies (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-impressions"></canvas></div></div>';
  html += '<div class="section-card"><h3>Gemiddelde positie (28 dagen)</h3><div style="position:relative;height:180px"><canvas id="chart-position"></canvas></div></div>';
  html += '</div>';

  // ── PageSpeed Scores ──
  if (speed && speed.scores) {
    html += '<div class="section-card"><h3>Core Web Vitals (homepage - mobile)</h3><div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">';
    var scoreLabels = {performance:'Performance',accessibility:'Toegankelijkheid',seo:'SEO',best_practices:'Best Practices'};
    for (var sk in speed.scores) {
      var sc = speed.scores[sk];
      var color = sc >= 90 ? '#16a34a' : sc >= 50 ? '#d97706' : '#ef4444';
      html += '<div class="kpi-card"><p class="label">' + (scoreLabels[sk]||sk) + '</p><p class="value" style="color:' + color + '">' + (sc !== null ? sc + '' : '-') + '</p></div>';
    }
    html += '</div>';
    // Core Web Vitals metrics
    html += '<table class="data-table" style="margin-top:8px"><thead><tr><th>Metric</th><th class="num">Waarde</th><th>Doel</th></tr></thead><tbody>';
    var metricInfo = {lcp:['LCP (laadtijd)', '≤2.5s'], fcp:['FCP', '≤1.8s'], tbt:['TBT', '≤200ms'], cls:['CLS', '≤0.1'], si:['Speed Index', '≤3.4s']};
    for (var mk in speed.metrics) {
      var mi = metricInfo[mk] || [mk, '-'];
      html += '<tr><td>' + mi[0] + '</td><td class="num">' + speed.metrics[mk] + '</td><td class="num" style="color:#94a3b8">' + mi[1] + '</td></tr>';
    }
    html += '</tbody></table>';
    if (currentTab === 'Concurrentie') { // only show desktop toggle on this tab
      html += '<div style="margin-top:8px"><button onclick="loadDesktopSpeed()" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Desktop test</button></div>';
    }
    html += '</div>';
  }

  // ── Keyword Gaps ──
  if (gaps && gaps.gaps) {
    html += '<div class="section-card"><h3>Kansen: hoge impressies, lage CTR</h3>' +
      (gaps.gaps.length ? '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
        gaps.gaps.slice(0,10).map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num" style="color:#ef4444">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
        '</tbody></table>' : '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen gaps gevonden</p>') +
      '</div>';
  }

  el.innerHTML = html;

  // ── Render Charts ──
  if (trends && trends.daily && trends.daily.length) {
    renderSeriesChart('chart-clicks', trends.daily, 'clicks', 'Klikken', '#4f46e5');
    renderSeriesChart('chart-impressions', trends.daily, 'impressions', 'Impressies', '#d97706');
    renderPositionChart('chart-position', trends.daily, trends.prev_period || []);
  }
  window.loadDesktopSpeed = function() {
    var btn = event.target; btn.disabled = true; btn.textContent = 'Laden...';
    fetch('/api/projects/' + encodeURIComponent(currentProject) + '/pagespeed?strategy=desktop').then(function(r){return r.json();}).then(function(data){
      var html = '<div class="section-card"><h3>Desktop scores</h3><div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">';
      for (var sk in data.scores) {
        var sc = data.scores[sk];
        var color = sc >= 90 ? '#16a34a' : sc >= 50 ? '#d97706' : '#ef4444';
        html += '<div class="kpi-card"><p class="label">'+(scoreLabels[sk]||sk)+'</p><p class="value" style="color:'+color+'">'+(sc!==null?sc:'-')+'</p></div>';
      }
      html += '</div></div>';
      var tc = document.getElementById('tab-content');
      if (tc) tc.innerHTML += html;
      else el.innerHTML += html;
    }).catch(function(){alert('Fout bij laden desktop speed');}).finally(function(){btn.disabled=false;btn.textContent='Desktop test';});
  };
}

// Eén meetreeks per grafiek — de kaarttitel benoemt de reeks, dus geen legenda nodig.
function renderSeriesChart(canvasId, daily, field, label, color) {
  var canvas = document.getElementById(canvasId); if (!canvas) return;
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  var ctx = canvas.getContext('2d');
  chartInstances[canvasId] = new Chart(ctx, {
    type: 'line', data: {
      labels: daily.map(function(d){return d.date.slice(5);}),
      datasets: [{ label: label, data: daily.map(function(d){return d[field];}), borderColor: color, backgroundColor: color + '1a', fill: true, tension: .3, pointRadius: 2 }]
    },
    options: { animation: false, responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, grid: { color: '#f1f5f9' }, ticks: { font: { size: 9 }, precision: 0 } }, x: { grid: { display: false }, ticks: { font: { size: 9 }, maxTicksLimit: 14 } } }
    }
  });
}
function renderPositionChart(canvasId, daily, prev) {
  var canvas = document.getElementById(canvasId); if (!canvas) return;
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  var ctx = canvas.getContext('2d');
  // Reverse position axis (lower = better)
  chartInstances[canvasId] = new Chart(ctx, {
    type: 'line', data: {
      labels: daily.map(function(d){return d.date.slice(5);}),
      datasets: [{ label: 'Positie (lager = beter)', data: daily.map(function(d){return d.position;}), borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,.08)', fill: true, tension: .3, pointRadius: 2,
        segment: { borderColor: function(ctx){return ctx.p0.parsed.y < ctx.p1.parsed.y ? '#ef4444' : '#16a34a';} }
      }]
    },
    options: { animation: false, responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 10 } } } },
      scales: { y: { reverse: true, grid: { color: '#f1f5f9' }, ticks: { font: { size: 9 } } }, x: { grid: { display: false }, ticks: { font: { size: 9 }, maxTicksLimit: 14 } } }
    }
  });
}

// ═══════════════════════════════════════════════════════════════════
//  KEYWORDS TAB
// ═══════════════════════════════════════════════════════════════════
async function renderKeywordsTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Keyword data laden...</p></div>';
  try {
    var gapResp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/keyword-gaps?days=28');
    var gaps = await gapResp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (gaps.error) { el.innerHTML = '<div class="empty-state">' + escHtml(gaps.error) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Keyword Research</h3></div>' +
    '<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">' +
    kpiBox('Totaal queries', gaps.total_queries||0, '', '') +
    kpiBox('Totaal klikken', (gaps.categories&&gaps.categories.clicks)||0, '', '') +
    kpiBox('Gem. CTR', (gaps.categories&&gaps.categories.avg_ctr)+'%'||'', '', '') +
    kpiBox('Gem. positie', (gaps.categories&&gaps.categories.avg_position)||'', '', '') +
    '</div>';

  // Best performers
  if (gaps.best_performers && gaps.best_performers.length) {
    html += '<div class="section-card"><h3>Best presterend</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Clicks</th><th class="num">Impressies</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.best_performers.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num" style="color:#16a34a;font-weight:600">'+q.clicks+'</td><td class="num">'+q.impressions+'</td><td class="num">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  // Gaps (hoge impressies, lage CTR)
  if (gaps.gaps && gaps.gaps.length) {
    html += '<div class="section-card"><h3>Kansen: hoge impressies, lage CTR (' + gaps.gaps.length + ')</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">Clicks</th><th class="num" style="color:#ef4444">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.gaps.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num">'+q.clicks+'</td><td class="num" style="color:#ef4444;font-weight:600">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  // Striking distance
  if (gaps.striking_distance && gaps.striking_distance.length) {
    html += '<div class="section-card"><h3>Striking distance (pos 4-20, veel impressies)</h3>' +
      '<table class="data-table"><thead><tr><th>Zoekwoord</th><th class="num">Impressies</th><th class="num">Clicks</th><th class="num">CTR</th><th class="num">Positie</th></tr></thead><tbody>' +
      gaps.striking_distance.map(function(q){return '<tr><td class="url-cell">'+escHtml(q.query)+'</td><td class="num">'+q.impressions+'</td><td class="num">'+(q.clicks||0)+'</td><td class="num">'+q.ctr+'%</td><td class="num">'+(typeof q.position==='number'?q.position.toFixed(1):q.position)+'</td></tr>';}).join('') +
      '</tbody></table></div>';
  }

  el.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════
//  TECHNISCH TAB
// ═══════════════════════════════════════════════════════════════════
async function renderTechTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Technische data laden...</p></div>';
  try { var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/tech-seo')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (data.error) { el.innerHTML = '<div class="empty-state">' + escHtml(data.error) + '</div>'; return; }
  var ic = data.index_coverage || {};
  var html = '<div class="kpi-grid">' +
    kpiBox('Geindexeerd (7d)', ic.total||'?', ic.change||0, '') +
    kpiBox('Kennisbank', (ic.by_type && ic.by_type.kennisbank)||0, '', '') +
    kpiBox('Blogs', (ic.by_type && ic.by_type.blog)||0, '', '') +
    kpiBox('Overig', (ic.by_type && ic.by_type.overig)||0, '', '') + '</div>' +
    '<div class="grid-2">' +
    '<div class="section-card"><h3>Indexverdeling</h3>' + '<table class="data-table"><thead><tr><th>Type</th><th class="num">Aantal</th></tr></thead><tbody>' +
    (ic.by_type ? Object.entries(ic.by_type).map(function(e){return '<tr><td>' + e[0].charAt(0).toUpperCase()+e[0].slice(1) + '</td><td class="num">' + e[1] + '</td></tr>';}).join('') : '<tr><td colspan="2" style="color:#94a3b8;text-align:center">Geen data</td></tr>') +
    '</tbody></table></div>' +
    '<div class="section-card"><h3>Sitemap</h3>' + (data.sitemap && data.sitemap.url ? '<p style="font-size:13px;margin-bottom:8px">Sitemap URL:</p><p style="font-size:12px;color:#4f46e5;word-break:break-all">' + escHtml(data.sitemap.url) + '</p><button onclick="submitSitemap()" style="margin-top:10px;padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">Indienen bij Google</button>' : '<p style="color:#94a3b8;font-size:12px">Geen sitemap URL</p>') + '</div></div>';
  if (data.top_queries_28d && data.top_queries_28d.length) {
    html += '<div class="section-card"><h3>Top zoekwoorden (28d)</h3>' + tbl(data.top_queries_28d.slice(0,15), ['zoekwoord','query'], ['Clicks','clicks'], ['Impressies','impressions'], ['Positie','position']) + '</div>';
  }
  el.innerHTML = html;
}
async function submitSitemap() {
  try {
    var sites = await (await fetch('/api/sites')).json();
    var site = sites.find(function(s){return s.name.toLowerCase()===currentProject.toLowerCase();});
    if (!site||!site.base_url) { alert('Geen site URL'); return; }
    var url = site.base_url.replace(/\/+$/,'')+'/sitemap.xml';
    var data = await (await fetch('/api/demand/submit-sitemap', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({site_url:site.base_url, sitemap_url:url}) })).json();
    alert(data.status==='ingediend' ? 'Sitemap ingediend!' : 'Fout: '+(data.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}

// ═══════════════════════════════════════════════════════════════════
//  ACTIVITEIT TAB
// ═══════════════════════════════════════════════════════════════════
async function renderActiviteitTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Activiteit laden...</p></div>';
  try { var items = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/activity?limit=50')).json(); }
  catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }
  if (!items||!items.length) { el.innerHTML = '<div class="empty-state"><p style="color:#94a3b8;font-size:13px">Nog geen activiteit</p></div>'; return; }
  el.innerHTML = '<div class="section-card"><h3>Recente activiteit ('+items.length+')</h3>' +
    '<table class="data-table"><thead><tr><th>Tijd</th><th>Actie</th><th>Detail</th></tr></thead><tbody>' +
    items.map(function(a){return '<tr><td style="color:#94a3b8;white-space:nowrap">'+(a.created_at?a.created_at.slice(11,16):'')+'</td><td><span class="badge '+(a.action==='publicatie'?'badge-live':a.action==='suggestie'?'badge-draft':'badge-log')+'">' + escHtml(a.action) + '</span></td><td>' + escHtml(a.detail) + '</td></tr>';}).join('') +
    '</tbody></table></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  DOELEN TAB — Goal Mode
// ═══════════════════════════════════════════════════════════════════
let goalPlanResult = null;
let goalCurrentId = null;

async function renderDoelenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Doelen laden...</p></div>';
  try {
    var resp = await fetch('/api/goals?limit=10');
    var goals = await resp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px"><h3 style="font-size:15px;font-weight:700">Goal Mode</h3>' +
    '<button onclick="showNewGoalForm()" style="padding:6px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">+ Nieuw doel</button></div>';

  if (goalCurrentId) {
    // Toon detail van actieve goal
    html += '<div id="goal-detail"></div>';
  }

  // Lijst goals
  if (goals && goals.length) {
    html += '<div class="section-card"><h3>Recente doelen (' + goals.length + ')</h3>' +
      '<table class="data-table"><thead><tr><th>Titel</th><th>Status</th><th>Voortgang</th><th>Gemaakt</th></tr></thead><tbody>' +
      goals.map(function(g) {
        var statusColors = {draft:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',paused:'#f1f5f9',completed:'#dcfce7',partial:'#fed7aa',failed:'#fecaca'};
        var sc = statusColors[g.status]||'#f1f5f9';
        var total = g.phase_count || 1;
        var done = g.completed_tasks || 0;
        var pct = total > 0 ? Math.round(done/total*100) : 0;
        return '<tr style="cursor:pointer" onclick="loadGoalDetail(\'' + g.id + '\')"><td><span class="badge" style="background:' + sc + '">' + escHtml(g.status) + '</span> ' + escHtml(g.title) + (g.status==='failed'?' <button onclick="event.stopPropagation();retryFailedGoal(\'' + g.id + '\')" style="padding:2px 8px;background:#ef4444;color:#fff;border:none;border-radius:4px;font-size:10px;cursor:pointer">\u2728 Los het op met AI</button>':'') + '</td>' +
          '<td>' + escHtml(g.status) + '</td><td><div style="display:flex;align-items:center;gap:6px"><div style="flex:1;height:4px;background:#e2e8f0;border-radius:2px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + (g.status==='completed'?'#16a34a':g.status==='failed'?'#ef4444':'#4f46e5') + ';border-radius:2px"></div></div><span style="font-size:10px;color:#64748b">' + done + '/' + total + '</span></div></td>' +
          '<td style="font-size:11px;color:#94a3b8">' + (g.created_at ? g.created_at.slice(0,10) : '') + '</td></tr>';
      }).join('') +
      '</tbody></table></div>';
  } else {
    html += '<div class="section-card"><div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen doelen</p>' +
      '<p style="color:#94a3b8">Stel een langetermijndoel in. Hermes splitst het op in taken en voert ze autonoom uit.</p></div></div>';
  }

  el.innerHTML = html;
}

function showNewGoalForm() {
  var el = document.getElementById('tab-content'); if (!el) return;
  el.innerHTML = '<div class="section-card" style="max-width:600px;margin:0 auto"><h3 style="margin-bottom:16px">Nieuw langetermijndoel</h3>' +
    '<div style="margin-bottom:12px"><label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px">Titel</label>' +
    '<input id="goal-title" style="width:100%;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px" placeholder="Bijv. Lanceer marketingcampagne SaaS"></div>' +
    '<div style="margin-bottom:12px"><label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px">Doelstelling (uitgebreid)</label>' +
    '<textarea id="goal-objective" rows="4" style="width:100%;padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px;resize:vertical" placeholder="Beschrijf het overkoepelende doel. Bijv.: Lanceer een marketingcampagne voor een SaaS-product dat zorginstellingen helpt met digitaal vrijwilligersmanagement. Doel: 50 leads in 30 dagen."></textarea></div>' +
    '<button onclick="generateGoalPlan()" style="padding:8px 20px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:13px;cursor:pointer">Plan genereren (AI decompositie)</button></div>';
}

async function generateGoalPlan() {
  var title = document.getElementById('goal-title'); if (!title) return;
  var objective = document.getElementById('goal-objective'); if (!objective) return;
  if (!title.value.trim() || !objective.value.trim()) { alert('Vul zowel titel als doelstelling in.'); return; }

  var btn = document.querySelector('button[onclick*=\"generateGoalPlan\"]'); 
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig met decompositie...'; }
  try {
    var resp = await fetch('/api/goals/plan', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({title: title.value.trim(), objective: objective.value.trim(), project: currentProject||'WeAreImpact'}),
    });
    goalPlanResult = await resp.json();
    showGoalPlan();
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = 'Plan genereren (AI decompositie)'; }
}

function showGoalPlan() {
  var el = document.getElementById('tab-content'); if (!el) return;
  var plan = goalPlanResult.plan;
  if (!plan || !plan.phases) { alert('Geen plan ontvangen'); return; }

  var html = '<div class="section-card"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
    '<div><h3 style="font-size:15px;font-weight:700">' + escHtml(goalPlanResult.title) + '</h3>' +
    '<p style="font-size:12px;color:#64748b;margin-top:2px">' + escHtml(plan.plan_summary||'') + '</p></div>' +
    '<button onclick="confirmAndStartGoal()" style="padding:8px 20px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600">Plan goedkeuren &amp; starten</button></div>' +
    '<p style="font-size:11px;color:#94a3b8;margin-bottom:12px">Geschatte duur: ' + escHtml(plan.estimated_duration||'onbekend') + '</p>';

  plan.phases.forEach(function(phase, pidx) {
    html += '<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:8px;overflow:hidden">' +
      '<div style="background:#f8fafc;padding:8px 12px;font-weight:600;font-size:13px;border-bottom:1px solid #e2e8f0">Fase ' + (pidx+1) + ': ' + escHtml(phase.title) + '</div>' +
      (phase.description ? '<div style="padding:6px 12px;font-size:11px;color:#64748b;border-bottom:1px solid #e2e8f0">' + escHtml(phase.description) + '</div>' : '') +
      '<div style="padding:6px 12px">';
    (phase.tasks||[]).forEach(function(task, tidx) {
      html += '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px">' +
        '<span style="width:18px;height:18px;border-radius:4px;background:#dbeafe;color:#1e40af;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0">' + (tidx+1) + '</span>' +
        '<span style="flex:1">' + escHtml(task.title) + '</span>' +
        '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#475569">' + (task.skill||'?') + '</span>' +
        (task.dependencies && task.dependencies.length ? '<span style="font-size:10px;color:#94a3b8">na ' + task.dependencies.map(function(d){return '#'+d;}).join(', ') + '</span>' : '') +
        '</div>';
    });
    html += '</div></div>';
  });

  html += '</div>';
  el.innerHTML = html;
}

async function confirmAndStartGoal() {
  if (!goalPlanResult) return;
  try {
    var confirmResp = await fetch('/api/goals/confirm', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalPlanResult.goal_id}),
    });
    var confirmData = await confirmResp.json();
    var startResp = await fetch('/api/goals/start', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalPlanResult.goal_id}),
    });
    var startData = await startResp.json();
    goalCurrentId = goalPlanResult.goal_id;
    alert('Doel gestart! ' + confirmData.phase_count + ' fasen, ' + confirmData.task_count + ' taken.');
    renderDoelenTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); }
}

async function loadGoalDetail(goalId) {
  try {
    var resp = await fetch('/api/goals/' + goalId);
    var goal = await resp.json();
    goalCurrentId = goalId;
    renderGoalDetail(goal);
  } catch(e) { alert('Fout: ' + e.message); }
}

function renderGoalDetail(goal) {
  var el = document.getElementById('goal-detail'); if (!el) return;
  var total = goal.task_count || 1;
  var done = goal.completed_tasks || 0;
  var failed = goal.failed_tasks || 0;
  var pct = total > 0 ? Math.round(done/total*100) : 0;

  var html = '<div class="section-card" style="border-left:3px solid ' + (goal.status==='running'?'#4f46e5':goal.status==='completed'?'#16a34a':goal.status==='failed'?'#ef4444':'#e2e8f0') + '">' +
    '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">' +
    '<div><h4 style="font-size:14px;font-weight:700">' + escHtml(goal.title) + '</h4>' +
    '<p style="font-size:11px;color:#64748b">' + escHtml(goal.objective) + '</p></div>' +
    '<div style="display:flex;gap:4px">' +
    (goal.status === 'running' ? '<button onclick="pauseGoal()" style="padding:4px 10px;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:6px;font-size:10px;cursor:pointer">Pauzeer</button>' : '') +
    (goal.status === 'paused' ? '<button onclick="resumeGoal()" style="padding:4px 10px;background:#dbeafe;color:#1e40af;border:1px solid #bfdbfe;border-radius:6px;font-size:10px;cursor:pointer">Hervat</button>' : '') +
    (goal.status === 'failed' ? '<button onclick="retryFailedGoal(\'' + goal.id + '\')" style="padding:4px 10px;background:#ef4444;color:#fff;border:1px solid #fca5a5;border-radius:6px;font-size:10px;cursor:pointer">\u2728 Los het op met AI</button>' : '') +
    '<button onclick="goalCurrentId=null;renderDoelenTab(document.getElementById(\'tab-content\'))" style="padding:4px 10px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:10px;cursor:pointer">Terug</button>' +
    '</div></div>' +
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">' +
    '<span class="badge" style="background:' + ({draft:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',paused:'#f1f5f9',completed:'#dcfce7',partial:'#fed7aa',failed:'#fecaca'}[goal.status]||'#f1f5f9') + '">' + escHtml(goal.status) + '</span>' +
    '<div style="flex:1;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + (goal.status==='completed'?'#16a34a':goal.status==='failed'?'#ef4444':'#4f46e5') + ';border-radius:3px;transition:width .5s"></div></div>' +
    '<span style="font-size:11px;color:#64748b;white-space:nowrap">' + done + '/' + total + ' taken</span>' +
    (failed>0?'<span style="font-size:11px;color:#ef4444;white-space:nowrap">' + failed + ' mislukt</span>':'') +
    '</div>';

  (goal.phases||[]).forEach(function(phase) {
    var phaseColors = {pending:'#f1f5f9',running:'#fef3c7',completed:'#dcfce7',failed:'#fecaca',skipped:'#f1f5f9'};
    html += '<div style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:6px;overflow:hidden">' +
      '<div style="background:#f8fafc;padding:6px 12px;font-weight:600;font-size:12px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:6px">' +
      '<span class="badge" style="background:' + (phaseColors[phase.status]||'#f1f5f9') + '">' + escHtml(phase.status) + '</span>' +
      escHtml(phase.title) + '</div>';

    (phase.tasks||[]).forEach(function(task) {
      var taskColors = {pending:'#f1f5f9',ready:'#dbeafe',running:'#fef3c7',completed:'#dcfce7',failed:'#fecaca',skipped:'#f1f5f9'};
      var taskIcons = {pending:'o',ready:'&rarr;',running:'&bull;',completed:'&check;',failed:'x',skipped:'-'};
      html += '<div style="display:flex;align-items:center;gap:6px;padding:5px 12px;border-bottom:1px solid #f1f5f9;font-size:12px">' +
        '<span style="width:16px;height:16px;border-radius:3px;background:' + (taskColors[task.status]||'#f1f5f9') + ';display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;flex-shrink:0">' + (taskIcons[task.status]||'?') + '</span>' +
        '<span style="flex:1">' + escHtml(task.title) + '</span>' +
        '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#475569">' + escHtml(task.skill||'') + '</span>' +
        (task.duration_ms ? '<span style="font-size:10px;color:#94a3b8">' + (task.duration_ms > 1000 ? (task.duration_ms/1000).toFixed(0)+'s' : task.duration_ms+'ms') + '</span>' : '') +
        (task.status==='running'?'<span class="spinner" style="width:10px;height:10px;border-width:1.5px"></span>':'') +
        '</div>';
    });
    html += '</div>';
  });

  html += '</div>';
  el.innerHTML = html;
}

async function pauseGoal() {
  if (!goalCurrentId) return;
  try {
    await fetch('/api/goals/pause', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id: goalCurrentId}) });
    loadGoalDetail(goalCurrentId);
  } catch(e) { alert('Fout: ' + e.message); }
}
async function resumeGoal() {
  if (!goalCurrentId) return;
  try {
    await fetch('/api/goals/resume', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id: goalCurrentId}) });
    loadGoalDetail(goalCurrentId);
  } catch(e) { alert('Fout: ' + e.message); }
}

// ── Retry failed goal ("Los het op met AI") ──
async function retryFailedGoal(goalId) {
  if (!goalId) return;
  var btn = event && event.target || document.querySelector('[onclick*="' + goalId + '"]');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  try {
    var resp = await fetch('/api/goals/retry-failed', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({goal_id: goalId}),
    });
    var data = await resp.json();
    if (data.error) { alert('Fout: ' + data.error); return; }
    alert('✅ Doel herstart! Hermes probeert het opnieuw.');
    // Refresh current view
    if (goalCurrentId === goalId) {
      loadGoalDetail(goalId);
    } else {
      renderDoelenTab(document.getElementById('tab-content'));
    }
    pollAgentStatus();
  } catch(e) { alert('Fout: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '✨ Los het op met AI'; }
}

