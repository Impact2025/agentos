// ── Impact OS — tab: Agent Control (Iris' stal-overzicht & deploy)
// Toont de 13 expert-agenten met live occupancy (idle/busy) en laat Iris
// (of Vincent) elke agent direct op een taak zetten via /api/agentctl/deploy.
// Werkt tegen de backend-domain agentctl (backend/domains/agentctl).

let _agentctlES = null;          // live EventSource (deploy/recover events)
let _agentctlTimer = null;       // poll-timer zolang je op deze tab bent

async function renderAgentControlTab(el) {
  el.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
      '<h3 style="font-size:15px;font-weight:700">Agent Control</h3>' +
      '<span style="font-size:11px;color:#64748b">Wie is idle, wie is bezig — en wat Iris nu kan inzetten</span>' +
    '</div>' +
    '<div id="agentctl-summary" class="section-card" style="margin-bottom:16px;display:flex;gap:18px;flex-wrap:wrap"></div>' +
    '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
        '<h4 style="font-size:13px;font-weight:600">Voorgestelde acties (Iris)</h4>' +
        '<button id="ac-exec-all" style="padding:6px 12px;background:#16a34a;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Voer allemaal uit</button>' +
      '</div>' +
      '<div id="agentctl-suggest" style="font-size:12px;color:#64748b">Laden…</div>' +
    '</div>' +
    '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">' +
        '<h4 style="font-size:13px;font-weight:600">Vastgelopen content (Iris Orchestrator)</h4>' +
        '<button id="orch-run-one" style="padding:6px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Verwerk er één</button>' +
      '</div>' +
      '<p style="font-size:11px;color:#94a3b8;margin-bottom:8px">' +
        'Stukken die de goedkope 30-min verbeteraar niet redde (\'stuck\') of die zijn afgewezen (\'rejected\'). ' +
        'Zet er handmatig één op de zware Gauntlet Loop (meerdere critici, kan enkele minuten duren) — nooit automatisch.' +
      '</p>' +
      '<div id="orch-list" style="font-size:12px;color:#64748b;margin-bottom:10px">Laden…</div>' +
      '<div id="orch-result" style="font-size:12px;margin-bottom:10px"></div>' +
      '<div id="orch-history"></div>' +
    '</div>' +
    '<div id="agentctl-grid" class="agent-grid"></div>' +
    '<div id="agentctl-deploy" style="margin-top:16px"></div>';

  await loadAgentControl();
  await loadAgentSuggestions();
  await loadOrchestratorPanel();

  var execBtn = document.getElementById('ac-exec-all');
  if (execBtn) execBtn.onclick = executeAllSuggestions;
  var orchBtn = document.getElementById('orch-run-one');
  if (orchBtn) orchBtn.onclick = runOrchestratorOne;

  // Live houden zolang je op de tab bent
  if (_agentctlTimer) clearInterval(_agentctlTimer);
  _agentctlTimer = setInterval(function () {
    if (currentTab === 'Agenten') loadAgentControl();
    else { clearInterval(_agentctlTimer); _agentctlTimer = null; }
  }, 6000);

  // SSE voor deploy/recover events
  if (_agentctlES) _agentctlES.close();
  try {
    _agentctlES = new EventSource('/api/agentctl/stream');
    _agentctlES.onmessage = function () { loadAgentControl(); };
  } catch (e) { /* SSE optioneel */ }
}

async function loadAgentControl() {
  var grid = document.getElementById('agentctl-grid');
  var sum = document.getElementById('agentctl-summary');
  if (!grid) return;
  try {
    var r = await fetch('/api/agentctl/agents');
    var d = await r.json();
    var s = d.summary || {};
    sum.innerHTML =
      statCard('Totaal', s.total || 0, '#0f172a') +
      statCard('Idle', s.idle_count || 0, '#16a34a') +
      statCard('Bezig', s.busy_count || 0, '#ea580c') +
      statCard('Draaiende Gauntlets', d.running_gauntlets || 0, '#2563eb') +
      statCard('Open doelen', d.running_goals || 0, '#64748b');

    grid.innerHTML = (d.agents || []).map(function (a) {
      var isBusy = a.state === 'busy';
      var dot = isBusy ? 'background:#ea580c;animation:pulse 1.4s infinite' : 'background:#16a34a';
      var work = (a.work || []).map(function (w) { return '<li style="font-size:11px;color:#475569">• ' + escHtml(w) + '</li>'; }).join('');
      return '' +
        '<div class="agent-card" style="border-left:3px solid ' + (isBusy ? '#ea580c' : '#16a34a') + '">' +
          '<div style="display:flex;align-items:center;justify-content:space-between">' +
            '<strong style="font-size:13px">' + escHtml(a.name) + '</strong>' +
            '<span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;color:' + (isBusy ? '#ea580c' : '#16a34a') + '">' +
              '<span style="width:7px;height:7px;border-radius:50%;' + dot + '"></span>' + (isBusy ? 'bezig' : 'idle') +
            '</span>' +
          '</div>' +
          '<div style="font-size:10px;color:#94a3b8;margin:3px 0 6px">' + escHtml(a.model) + '</div>' +
          (work ? '<ul style="margin:0 0 8px 14px;padding:0">' + work + '</ul>' : '<div style="font-size:11px;color:#16a34a;margin-bottom:8px">Beschikbaar</div>') +
          '<button class="agent-deploy-btn" data-agent="' + a.id + '" data-name="' + escHtml(a.name) + '" ' +
            'style="width:100%;padding:6px;font-size:11px;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc;cursor:pointer">Inzetten op taak</button>' +
        '</div>';
    }).join('');

    // Deploy-knoppen
    Array.prototype.forEach.call(grid.querySelectorAll('.agent-deploy-btn'), function (btn) {
      btn.onclick = function () { showDeployForm(parseInt(btn.dataset.agent, 10), btn.dataset.name); };
    });
  } catch (e) {
    grid.innerHTML = '<div class="empty-state">Kon agenten niet laden: ' + escHtml(e.message) + '</div>';
  }
}

async function loadAgentSuggestions() {
  var box = document.getElementById('agentctl-suggest');
  if (!box) return;
  try {
    var r = await fetch('/api/agentctl/suggest');
    var d = await r.json();
    var sugs = d.suggestions || [];
    if (!sugs.length) { box.innerHTML = '<span style="color:#16a34a">Alle projecten solide — geen acties nodig.</span>'; return; }
    box.innerHTML = sugs.slice(0, 8).map(function (s) {
      return '<div style="display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid #f1f5f9">' +
        '<span style="min-width:54px;font-size:10px;font-weight:700;color:#ea580c">' + s.priority.toFixed(1) + '</span>' +
        '<span style="min-width:130px;font-weight:600">' + escHtml(s.project) + '</span>' +
        '<span style="color:#64748b">→ <b>' + escHtml(s.agent) + '</b> (' + escHtml(s.pillar) + ' ' + s.pillar_score + '/25)</span>' +
      '</div>';
    }).join('') +
    '<div style="font-size:10px;color:#94a3b8;margin-top:6px">' + sugs.length + ' acties voorgesteld · ' +
      'laagste pijler per project = grootste hefboom · klik "Voer allemaal uit" om ze als agent-runs te starten</div>';
  } catch (e) {
    box.innerHTML = 'Kon suggesties niet laden: ' + escHtml(e.message);
  }
}

async function loadOrchestratorPanel() {
  var list = document.getElementById('orch-list');
  var hist = document.getElementById('orch-history');
  if (!list) return;
  try {
    var r = await fetch('/api/orchestrator/under-threshold');
    var d = await r.json();
    var jobs = d.jobs || [];
    if (!jobs.length) {
      list.innerHTML = '<span style="color:#16a34a">Niets vastgelopen — geen \'stuck\' of \'rejected\' stukken onder de grens.</span>';
    } else {
      list.innerHTML = '<table style="width:100%;border-collapse:collapse">' +
        '<thead><tr style="text-align:left;color:#94a3b8;border-bottom:1px solid #e2e8f0">' +
          '<th style="padding:4px">Titel</th><th style="padding:4px">Project</th>' +
          '<th style="padding:4px">Status</th><th style="padding:4px">Score</th></tr></thead><tbody>' +
        jobs.slice(0, 10).map(function (j) {
          return '<tr style="border-bottom:1px solid #f1f5f9">' +
            '<td style="padding:4px">' + escHtml(j.title || j.id) + '</td>' +
            '<td style="padding:4px">' + escHtml(j.project || '') + '</td>' +
            '<td style="padding:4px">' + escHtml(j.status || '') + '</td>' +
            '<td style="padding:4px">' + (j.seo_score == null ? '—' : j.seo_score) + '</td>' +
          '</tr>';
        }).join('') + '</tbody></table>' +
        '<div style="font-size:10px;color:#94a3b8;margin-top:6px">' + jobs.length + ' stuk(ken) wachten · oudste/eerste wint bij "Verwerk er één"</div>';
    }
  } catch (e) {
    list.innerHTML = 'Kon lijst niet laden: ' + escHtml(e.message);
  }

  if (!hist) return;
  try {
    var rf = await fetch('/api/action-center/feed?limit=25');
    var df = await rf.json();
    var runs = (df || []).filter(function (o) { return o.action === 'orchestrator_gauntlet'; }).slice(0, 5);
    if (!runs.length) {
      hist.innerHTML = '<div style="font-size:10px;color:#94a3b8">Nog geen Orchestrator-runs geweest.</div>';
      return;
    }
    hist.innerHTML = '<div style="font-size:10px;color:#94a3b8;margin-bottom:4px">Laatste runs</div>' +
      runs.map(function (o) {
        var color = o.status === 'error' ? '#ef4444' : '#16a34a';
        return '<div style="font-size:11px;padding:4px 0;border-top:1px solid #f1f5f9">' +
          '<span style="color:' + color + ';font-weight:600">●</span> ' + escHtml(o.detail) + '</div>';
      }).join('');
  } catch (e) { /* geschiedenis is optioneel */ }
}

async function runOrchestratorOne() {
  var btn = document.getElementById('orch-run-one');
  var res = document.getElementById('orch-result');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig… (kan enkele minuten duren)'; }
  if (res) { res.style.color = '#64748b'; res.textContent = 'De Gauntlet Loop draait — meerdere critici beoordelen het herschreven stuk…'; }
  try {
    var r = await fetch('/api/orchestrator/process-one', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
    });
    var d = await r.json();
    if (res) {
      if (d.processed) {
        res.style.color = '#16a34a';
        res.textContent = 'Herschreven en teruggezet in de Wachtrij (job ' + d.published_job_id + ').';
      } else {
        res.style.color = d.reason === 'geen stukken onder de grens' ? '#64748b' : '#ea580c';
        res.textContent = d.reason || 'Niets verwerkt.';
      }
    }
    loadOrchestratorPanel();
  } catch (e) {
    if (res) { res.style.color = '#ef4444'; res.textContent = 'Fout: ' + e.message; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Verwerk er één'; }
  }
}

// Pijler -> wat een 'staged'/'running' resultaat concreet betekent, voor de
// per-project regel. 'content' publiceert async (Gauntlet-run duurt minuten),
// de rest is synchroon klaar zodra de respons terug is.
var _PILLAR_LANDING = {
  seo: 'CTR-varianten klaar in Optimalisatie',
  content: 'artikel wordt geschreven — komt in de Wachtrij zodra de Gauntlet-run klaar is',
  uitvoering: 'doelen bijgewerkt',
  hygiene: 'stuk herschreven, in de Wachtrij',
};

async function executeAllSuggestions() {
  var box = document.getElementById('agentctl-suggest');
  var btn = document.getElementById('ac-exec-all');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig…'; }
  try {
    var r = await fetch('/api/agentctl/suggest/execute', { method: 'POST' });
    var d = await r.json();
    var results = d.results || [];
    var lines = results.map(function (res) {
      var project = escHtml(res.project || '');
      if (!res.ok) {
        var reason = escHtml(res.reason || 'geen effect');
        return '<div style="padding:3px 0;color:#94a3b8">' + project + ' — ' + reason + '</div>';
      }
      var label = escHtml(res.detail || _PILLAR_LANDING[res.pillar] || 'gedaan');
      var link = res.artifact
        ? ' <a href="' + escHtml(res.artifact) + '" style="color:#4f46e5;text-decoration:underline">bekijk</a>'
        : '';
      return '<div style="padding:3px 0"><span style="color:#16a34a">' + project + '</span> → ' +
        label + link + '</div>';
    });
    if (box) {
      box.innerHTML = '<div style="margin-bottom:6px">' + (d.succeeded || 0) + '/' + (d.executed || 0) +
        ' suggesties uitgevoerd:</div>' + lines.join('');
    }
    loadAgentControl();
    setTimeout(loadAgentSuggestions, 4000);
  } catch (e) {
    if (box) box.innerHTML = 'Fout: ' + escHtml(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Voer allemaal uit'; }
  }
}

function statCard(label, val, color) {
  return '<div style="min-width:84px"><div style="font-size:20px;font-weight:700;color:' + color + '">' + val + '</div>' +
         '<div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.04em">' + label + '</div></div>';
}

function showDeployForm(agentId, agentName) {
  var box = document.getElementById('agentctl-deploy');
  if (!box) return;
  box.innerHTML =
    '<div class="section-card" style="border:1px solid #e2e8f0">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:6px">Zet <b>' + escHtml(agentName) + '</b> op een taak</h4>' +
      '<textarea id="ac-task" rows="2" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;margin-bottom:8px" placeholder="Bijv. schrijf een SEO-metabeschrijving voor de homepage"></textarea>' +
      '<input id="ac-project" style="width:100%;padding:8px;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;margin-bottom:10px" placeholder="Project (bijv. WeAreImpact)">' +
      '<div style="display:flex;gap:8px">' +
        '<button id="ac-go" style="padding:7px 14px;background:#16a34a;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">Start agent</button>' +
        '<button onclick="document.getElementById(\'agentctl-deploy\').innerHTML=\'\'" style="padding:7px 14px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;cursor:pointer">Annuleer</button>' +
      '</div>' +
      '<div id="ac-result" style="font-size:11px;color:#64748b;margin-top:8px"></div>' +
    '</div>';
  document.getElementById('ac-go').onclick = function () {
    var task = document.getElementById('ac-task').value.trim();
    var project = document.getElementById('ac-project').value.trim();
    var res = document.getElementById('ac-result');
    if (!task) { res.style.color = '#ef4444'; res.textContent = 'Voer een taak in.'; return; }
    res.style.color = '#64748b'; res.textContent = 'Agent starten…';
    fetch('/api/agentctl/deploy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, task: task, project: project || null })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) {
        res.style.color = '#16a34a';
        res.textContent = j.message + ' (run ' + j.run_id + ')';
        box.innerHTML = '';
        loadAgentControl();
      } else {
        res.style.color = '#ef4444';
        res.textContent = 'Mislukt: ' + (j.detail || 'onbekend');
      }
    }).catch(function (e) { res.style.color = '#ef4444'; res.textContent = 'Fout: ' + e.message; });
  };
}

// Opruimen bij tab-wissel
function stopAgentControl() {
  if (_agentctlTimer) { clearInterval(_agentctlTimer); _agentctlTimer = null; }
  if (_agentctlES) { _agentctlES.close(); _agentctlES = null; }
}
