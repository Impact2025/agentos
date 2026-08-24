// ── Impact OS — Facebook-tab: analyse (snapshot), instellingen, post-composer, comments
// Wereldklasse Facebook-beheer via de Facebook Agent "Deluxe" (backend/domains/facebook).
// Leest uit de opgeslagen snapshot (instant, geen live Graph API); acties (post/settings/
// comments) raken de live Graph API alleen op expliciete gebruikershandeling.

let _fbState = { site: null, sites: [], snapshot: null };

async function renderFacebookTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Facebook laden...</p></div>';
  // Haal alle sites op; toon ze allemaal (ook zonder FB-pagina — die tonen een
  // heldere 'nog geen FB-pagina aangesloten'-melding in plaats van verborgen te zijn).
  try {
    var resp = await fetch('/api/sites');
    var sites = await resp.json();
    _fbState.sites = (sites || []).map(function(s){
      s.has_fb = !!s.facebook_page_id;
      return s;
    });
  } catch(e) {
    _fbState.sites = [];
  }
  if (!_fbState.sites.length) {
    el.innerHTML = '<div class="empty-state"><p style="font-size:15px;font-weight:600">Nog geen sites</p>' +
      '<p style="color:var(--text-muted)">Voeg projecten toe op de Sites-pagina.</p></div>';
    return;
  }
  if (!_fbState.site) _fbState.site = _fbState.sites[0].name;

  var html = '';
  // Site-selector
  html += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">' +
    '<label style="font-size:12px;color:var(--text-dim)">Pagina:</label>' +
    '<select id="fb-site-select" onchange="fbSelectSite(this.value)" class="select" style="min-width:220px">' +
    _fbState.sites.map(function(s){
      var tag = s.has_fb ? '' : ' (geen FB-pagina)';
      return '<option value="'+escAttr(s.name)+'"'+(s.name===_fbState.site?' selected':'')+'>'+escHtml(s.name)+escHtml(tag)+'</option>';
    }).join('') +
    '</select>' +
    '<button onclick="fbRunSnapshot()" class="btn btn-sm btn-ghost">↻ Snapshot nu trekken</button>' +
    '<button onclick="fbRefresh()" class="btn btn-sm btn-ghost">Ververs</button>' +
    '</div>';

  // Composer
  html += '<div class="section-card" style="margin-bottom:16px">' +
    '<h3>Nieuwe post</h3>' +
    '<div id="fb-ideas" style="margin-bottom:8px"></div>' +
    '<textarea id="fb-post-text" placeholder="Schrijf je bericht..." rows="3" style="width:100%;padding:8px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font:inherit;resize:vertical"></textarea>' +
    '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center">' +
    '<input id="fb-post-link" placeholder="Link (optioneel)" style="flex:1;min-width:160px;padding:6px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font:inherit">' +
    '<button onclick="fbCreatePost()" class="btn btn-sm btn-primary">Plaatsen</button>' +
    '<button onclick="fbLoadIdeas()" class="btn btn-sm btn-ghost">⚡ Ideeën uit data</button>' +
    '</div><div id="fb-post-result" style="font-size:12px;margin-top:6px"></div></div>';

  // Analyse (snapshot)
  html += '<div id="fb-analyse"></div>';

  // Cross-project benchmark
  html += '<div class="section-card" style="margin-top:16px" id="fb-benchmark-card"><h3>Portfolio-benchmark</h3><div id="fb-benchmark">Laden...</div></div>';

  // Instellingen
  html += '<div class="section-card" style="margin-top:16px" id="fb-settings-card"><h3>Pagina-instellingen</h3><div id="fb-settings">Laden...</div></div>';

  // Comments
  html += '<div class="section-card" style="margin-top:16px" id="fb-comments-card"><h3>Recente reacties</h3><div id="fb-comments">Laden...</div></div>';

  // FB→SEO-impact (gesloten meetlus)
  html += '<div class="section-card" style="margin-top:16px" id="fb-impact-card"><h3>FB→SEO-impact</h3><div id="fb-impact">Laden...</div></div>';

  el.innerHTML = html;
  fbLoadSnapshot();
  fbLoadBenchmark();
  fbLoadSettings();
  fbLoadComments();
  fbLoadImpact();
}

function fbSelectSite(name) {
  _fbState.site = name;
  fbLoadSnapshot();
  fbLoadBenchmark();
  fbLoadSettings();
  fbLoadComments();
  fbLoadImpact();
}
function fbRefresh() { renderFacebookTab(document.getElementById('tab-content')); }

async function fbLoadBenchmark() {
  var el = document.getElementById('fb-benchmark');
  if (!el) return;
  el.innerHTML = 'Laden...';
  try {
    var resp = await fetch('/api/facebook/benchmark');
    if (!resp.ok) { el.innerHTML = '<p style="color:var(--text-muted)">'+escHtml((await resp.json()).detail||'niet beschikbaar')+'</p>'; return; }
    var r = await resp.json();
    if (!r.projects || !r.projects.length) {
      el.innerHTML = '<p style="color:var(--text-muted)">Nog geen Facebook-projecten om te vergelijken.</p>';
      return;
    }
    var rows = r.projects.map(function(p){
      var trend = p.trend || 'n/b';
      var tbadge = trend==='stijgend' ? 'pill-ok' : trend==='dalend' ? 'pill-danger' : 'pill-neutral';
      var tlabel = trend==='stijgend' ? '↗ stijgend' : trend==='dalend' ? '↘ dalend' : '→ stabiel';
      var fd = p.fan_delta != null ? (p.fan_delta>0?'+':'')+p.fan_delta : 'n/b';
      var vel = p.fan_velocity_daily != null ? p.fan_velocity_daily+'/dag' : 'n/b';
      var e1k = p.engagement_per_1k_fans != null ? p.engagement_per_1k_fans : 'n/b';
      var isCur = p.site_name === _fbState.site;
      return '<tr'+(isCur?' style="background:var(--row-hl)"':'')+'>'+
        '<td>'+(isCur?'▶ ':'')+escHtml(p.site_name)+'</td>'+
        '<td>'+(p.fans!=null?p.fans:'n/b')+'</td>'+
        '<td>'+escHtml(fd)+'</td>'+
        '<td>'+escHtml(vel)+'</td>'+
        '<td>'+escHtml(e1k)+'</td>'+
        '<td>'+(p.posts_28d!=null?p.posts_28d:'n/b')+'</td>'+
        '<td><span class="pill '+tbadge+'">'+tlabel+'</span></td>'+
        '<td>'+(p.best_posting_day||'n/b')+' · '+(p.best_posting_hour!=null?p.best_posting_hour+':00':'n/b')+'</td>'+
        '</tr>';
    }).join('');
    el.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'+
      '<th>Project</th><th>Fans</th><th>Fan-Δ</th><th>Velocity</th><th>Eng/1k fans</th>'+
      '<th>Posts 28d</th><th>Trend</th><th>Beste dag·uur</th></tr></thead><tbody>'+
      rows+'</tbody></table></div>'+
      '<p style="font-size:11px;color:var(--text-muted);margin-top:6px">Eng/1k fans = eerlijke verhouding tussen kleine en grote pagina\'s. De huidige pagina is gemarkeerd. Trends vullen zich automatisch aan vanaf de 2e dagelijkse snapshot.</p>';
  } catch(e) {
    el.innerHTML = '<p style="color:var(--red)">Fout: '+escHtml(e.message)+'</p>';
  }
}

async function fbLoadImpact() {
  var el = document.getElementById('fb-impact');
  if (!el) return;
  el.innerHTML = 'Laden...';
  try {
    var resp = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/impact');
    if (!resp.ok) { el.innerHTML = '<p style="color:var(--text-muted)">'+escHtml((await resp.json()).detail||'niet beschikbaar')+'</p>'; return; }
    var r = await resp.json();
    if (!r.impact || !r.impact.length) {
      el.innerHTML = '<p style="color:var(--text-muted)">'+escHtml(r.note||'Nog geen FB-posts gelogd — plaats posts met artikel-link om het effect te meten.')+'</p>';
      return;
    }
    var s = r.summary || {};
    var rows = r.impact.map(function(i){
      var v = i.verdict;
      var badge = v==='verbeterd' ? 'pill-ok' : v==='verslechterd' ? 'pill-danger' : 'pill-neutral';
      var label = v==='verbeterd' ? '↑ beter' : v==='verslechterd' ? '↓ slechter' : '– data';
      return '<tr><td>'+(i.query||'(geen query)')+'</td>'+
        '<td>'+(i.gsc_baseline_pos!=null?i.gsc_baseline_pos:'–')+'</td>'+
        '<td>'+(i.gsc_effect_pos!=null?i.gsc_effect_pos:'–')+'</td>'+
        '<td>'+(i.delta_position!=null?i.delta_position:'–')+'</td>'+
        '<td><span class="pill '+badge+'">'+label+'</span></td></tr>';
    }).join('');
    el.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'+
      '<th>Query</th><th>Pos vóór</th><th>Pos ná</th><th>Δ</th><th>Effect</th></tr></thead><tbody>'+
      rows+'</tbody></table></div>'+
      '<p style="font-size:12px;color:var(--text-muted);margin-top:6px">'+escHtml(
        s.verbeterd+' verbeterd, '+s.verslechterd+' verslechterd, '+s.onvoldoende_data+' onvoldoende data. Gem. Δ: '+(s.avg_delta_position||'–'))+
      '</p>';
  } catch(e) {
    el.innerHTML = '<p style="color:var(--red)">Fout: '+escHtml(e.message)+'</p>';
  }
}

async function fbLoadSnapshot() {
  var el = document.getElementById('fb-analyse');
  if (!el) return;
  el.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    var resp = await fetch('/api/facebook/snapshot?site=' + encodeURIComponent(_fbState.site));
    if (!resp.ok) { el.innerHTML = '<div class="empty-state">Geen snapshot — trek er eerst een aan.</div>'; return; }
    var s = await resp.json();
    _fbState.snapshot = s;
    if (s.status !== 'ok' || !s.snapshot) {
      el.innerHTML = '<div class="section-card" style="border-left:4px solid var(--red)">' +
        '<h3>Analyse niet beschikbaar</h3><p style="color:var(--text-dim)">'+escHtml(s.error||'onbekend')+'</p>' +
        '<p style="font-size:12px;color:var(--text-muted)">Snapshot van '+escHtml((s.captured_at||'').slice(0,10))+'</p></div>';
      return;
    }
    var a = s.snapshot;
    var page = a.page || {};
    var html = '<div class="section-card" style="margin-bottom:16px"><div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
      '<h3>Analyse — '+escHtml(page.name||_fbState.site)+'</h3>' +
      '<span style="font-size:11px;color:var(--text-muted)">snapshot '+escHtml((s.captured_at||'').slice(0,16).replace('T',' '))+'</span></div>';
    html += '<div class="kpi-grid">' +
      kpiBox('Fans', page.fan_count != null ? page.fan_count : 'n/b', '', '') +
      kpiBox('Posts (28d)', a.posts_analysed, '', '') +
      kpiBox('Interacties', a.total_engagement, '', 'totaal over venster') +
      kpiBox('Gem. / post', a.avg_engagement_per_post, '', 'engagement') +
      kpiBox('Fan-groei', a.fan_adds_window != null ? a.fan_adds_window : 'n/b', '', '28d') +
      kpiBox('Beste dag', a.best_posting_day || 'n/b', '', 'post-frequentie') +
      kpiBox('Beste uur', a.best_posting_hour != null ? a.best_posting_hour + ':00' : 'n/b', '', 'meeste posts') +
      '</div>';
    // Binnen-snapshot momentum (eerste vs tweede helft van het venster):
    // toont meteen of de recente posting beter loopt dan de oudere.
    var mom = a.window_momentum;
    if (mom && mom.trend) {
      var mbadge = mom.trend === 'stijgend' ? 'pill-ok' : mom.trend === 'dalend' ? 'pill-danger' : 'pill-neutral';
      var mlabel = mom.trend === 'stijgend' ? '↗ stijgend' : mom.trend === 'dalend' ? '↘ dalend' : '→ stabiel';
      html += '<div style="margin-top:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        '<span class="pill ' + mbadge + '">' + mlabel + '</span>' +
        '<span style="font-size:12px;color:var(--text-muted)">Momentum 28d: ' +
        mom.first_half_engagement + ' → ' + mom.last_half_engagement + ' interacties ' +
        '(' + (mom.engagement_delta_pct != null ? (mom.engagement_delta_pct > 0 ? '+' : '') + mom.engagement_delta_pct + '%' : 'n/b') +
        ' tussen eerste en tweede helft van het venster).</span></div>';
    }
    if (a.insights_available) {
      html += '<p style="font-size:12px;color:var(--ok-fg);margin-top:8px">✓ Insights beschikbaar (reach/impressions/engagement op paginaniveau).</p>';
    } else {
      html += '<p style="font-size:12px;color:var(--text-muted);margin-top:8px">Insights op paginaniveau niet beschikbaar — pagina is geen Business-account of heeft geen data. Zet de pagina om naar een Business-account voor reach/impression-analyse.</p>';
    }
    if (a.top_posts && a.top_posts.length) {
      html += '<h3 style="margin-top:14px">Top-posts</h3><div class="tbl-wrap">' +
        tbl(a.top_posts.map(function(p){ return {message:(p.message||'(geen tekst)').slice(0,80), created:p.created_time, eng:p.engagement, likes:p.likes, comments:p.comments, shares:p.shares}; }),
          ['Bericht','message'], ['Engagement','eng'], ['❤','likes'], ['💬','comments'], ['↗','shares']) + '</div>';
    }
    html += '</div>';
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Fout: '+escHtml(e.message)+'</div>';
  }
}

async function fbLoadIdeas() {
  var box = document.getElementById('fb-ideas');
  if (!box) return;
  box.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">Ideeën uit echte data laden...</span>';
  try {
    var resp = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/suggest?limit=5');
    if (!resp.ok) { box.innerHTML = '<span style="font-size:12px;color:var(--red)">'+escHtml((await resp.json()).detail||'mislukt')+'</span>'; return; }
    var r = await resp.json();
    if (!r.success || !r.ideas || !r.ideas.length) {
      box.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">Geen data-ideeën — gebruik vault-context.</span>';
      return;
    }
    var srcNote = 'Bronnen: ' + (r.sources_used && r.sources_used.length ? r.sources_used.join(', ') : 'geen') +
      (r.sources_missing && r.sources_missing.length ? ' (ontbreken: ' + r.sources_missing.join(', ') + ')' : '');
    box.innerHTML = '<p style="font-size:11px;color:var(--text-muted);margin-bottom:6px">'+escHtml(srcNote)+'</p>' +
      '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
      r.ideas.map(function(idea, i){
        return '<button type="button" class="btn btn-sm btn-ghost" title="'+escAttr(idea.bewijs||'')+'" onclick="fbUseIdea('+i+')">'+escHtml(idea.werktitel.slice(0,60))+'</button>';
      }).join('') + '</div>';
    _fbState.ideas = r.ideas;
  } catch(e) {
    box.innerHTML = '<span style="font-size:12px;color:var(--red)">Fout: '+escHtml(e.message)+'</span>';
  }
}

function fbUseIdea(i) {
  var idea = (_fbState.ideas || [])[i];
  if (!idea) return;
  var ta = document.getElementById('fb-post-text');
  if (ta) ta.value = idea.werktitel;
  var link = document.getElementById('fb-post-link');
  if (link) link.value = idea.url || '';
  var res = document.getElementById('fb-post-result');
  var urlNote = idea.url ? ' · link: '+escHtml(idea.url) : ' · geen artikel-link';
  if (res) res.innerHTML = '<span style="color:var(--text-dim)">Hoek: '+escHtml(idea.hoek)+' — bewijs: '+escHtml(idea.bewijs||'')+urlNote+'</span>';
}

async function fbRunSnapshot() {
  var btn = document.querySelector('#fb-analyse').previousElementSibling;
  var res = document.getElementById('fb-post-result');
  try {
    var resp = await fetch('/api/facebook/snapshot/run', {method:'POST'});
    var r = await resp.json();
    if (r.success) {
      fbLoadSnapshot();
      if (res) res.innerHTML = '<span style="color:var(--ok-fg)">✓ Snapshot bijgewerkt: '+escHtml(JSON.stringify(r.states||{}))+'</span>';
    } else {
      if (res) res.innerHTML = '<span style="color:var(--red)">'+escHtml(r.detail||'mislukt')+'</span>';
    }
  } catch(e) {
    if (res) res.innerHTML = '<span style="color:var(--red)">'+escHtml(e.message)+'</span>';
  }
}

async function fbLoadSettings() {
  var el = document.getElementById('fb-settings');
  if (!el) return;
  el.innerHTML = 'Laden...';
  try {
    var resp = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/settings');
    if (!resp.ok) { el.innerHTML = '<p style="color:var(--red)">'+escHtml((await resp.json()).detail||'niet leesbaar')+'</p>'; return; }
    var r = await resp.json();
    var st = r.settings || {};
    el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px">' +
      fbSettingField('Naam', 'name', st.name) +
      fbSettingField('Beschrijving', 'about', st.about) +
      fbSettingField('Website', 'website', st.website) +
      fbSettingField('Bio', 'bio', st.bio) +
      fbSettingField('Missie', 'mission', st.mission) +
      '</div>' +
      '<div style="margin-top:10px"><button onclick="fbSaveSettings()" class="btn btn-sm btn-primary">Opslaan</button> ' +
      '<span id="fb-settings-result" style="font-size:12px"></span></div>';
  } catch(e) {
    el.innerHTML = '<p style="color:var(--red)">Fout: '+escHtml(e.message)+'</p>';
  }
}
function fbSettingField(label, key, val) {
  return '<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text-dim)">'+escHtml(label)+
    '<input id="fb-set-'+key+'" value="'+escAttr(val||'')+'" style="padding:6px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font:inherit"></label>';
}
async function fbSaveSettings() {
  var payload = {};
  ['name','about','website','bio','mission'].forEach(function(k){
    var v = document.getElementById('fb-set-'+k);
    if (v) payload[k] = v.value;
  });
  var res = document.getElementById('fb-settings-result');
  res.innerHTML = 'Opslaan...';
  try {
    var resp = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    var r = await resp.json();
    res.innerHTML = r.success ? '<span style="color:var(--ok-fg)">✓ Opgeslagen</span>' : '<span style="color:var(--red)">'+escHtml(r.error||'mislukt')+'</span>';
  } catch(e) { res.innerHTML = '<span style="color:var(--red)">'+escHtml(e.message)+'</span>'; }
}

async function fbCreatePost() {
  var text = document.getElementById('fb-post-text').value.trim();
  var link = document.getElementById('fb-post-link').value.trim();
  var res = document.getElementById('fb-post-result');
  if (!text && !link) { res.innerHTML = '<span style="color:var(--red)">Voer tekst of een link in.</span>'; return; }
  res.innerHTML = 'Plaatsen...';
  try {
    var resp = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/posts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text, link:link||null})});
    var r = await resp.json();
    if (r.success) {
      res.innerHTML = '<span style="color:var(--ok-fg)">✓ Geplaatst: '+escHtml(r.url||r.post_id||'')+'</span>';
      document.getElementById('fb-post-text').value = '';
      document.getElementById('fb-post-link').value = '';
      fbRunSnapshot();
    } else {
      res.innerHTML = '<span style="color:var(--red)">'+escHtml(r.error||'mislukt')+'</span>';
    }
  } catch(e) { res.innerHTML = '<span style="color:var(--red)">'+escHtml(e.message)+'</span>'; }
}

async function fbLoadComments() {
  var el = document.getElementById('fb-comments');
  if (!el) return;
  el.innerHTML = 'Laden...';
  try {
    // Eerst recente posts ophalen, dan comments op de nieuwste post tonen.
    var pr = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/posts?limit=1');
    if (!pr.ok) { el.innerHTML = '<p style="color:var(--text-muted)">Geen posts om reacties op te tonen.</p>'; return; }
    var posts = await pr.json();
    if (!posts.posts || !posts.posts.length) { el.innerHTML = '<p style="color:var(--text-muted)">Nog geen posts.</p>'; return; }
    var pid = posts.posts[0].id;
    var cr = await fetch('/api/facebook/'+encodeURIComponent(_fbState.site)+'/comments/'+encodeURIComponent(pid));
    if (!cr.ok) { el.innerHTML = '<p style="color:var(--text-muted)">Geen reacties (of geen leesrecht).</p>'; return; }
    var c = await cr.json();
    var comments = c.comments || [];
    if (!comments.length) { el.innerHTML = '<p style="color:var(--text-muted)">Nog geen reacties op de laatste post.</p>'; return; }
    el.innerHTML = '<div class="tbl-wrap">' + tbl(comments.map(function(x){
      return {who:x.from||'?', msg:(x.message||'').slice(0,90), likes:x.like_count, when:(x.created_time||'').slice(0,10)};
    }), ['Van','who'], ['Reactie','msg'], ['❤','likes'], ['Datum','when']) +
    '</div><p style="font-size:11px;color:var(--text-muted);margin-top:6px">Reageren/verbergen via de API is beschikbaar in facebook/agent.py (reply_comment / hide_comment).</p>';
  } catch(e) {
    el.innerHTML = '<p style="color:var(--red)">Fout: '+escHtml(e.message)+'</p>';
  }
}
