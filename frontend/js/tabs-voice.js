// ── Impact OS — Voice-tab (Apollo-achtige spraaklaag) ─────────────────────────
// Input: Web Speech API (SpeechRecognition, nl-NL). Brain: bestaande
// /api/chat/stream (SSE). Output: browser SpeechSynthesis (gratis NL) of
// optioneel de backend-edge-tts via /api/voice/speak.
//
// Fase 2 ("Hands"): een spraakopdracht kan een écht doel worden via het
// bestaande Goal-systeem (plan -> confirm -> start, mét review-gates).
// Gebouwde artifacts landen in de Gallery (/api/voice/artifacts), één ledger
// van "wat je hardop vroeg -> wat ervan kwam".
//
// Werkt ook zónder microfoon: type-fallback onderin.

let _voiceSessionId = null;
let _voiceRecognition = null;
let _voiceListening = false;
let _voiceSpeaking = false;
let _voiceFullscreen = false;
let _voiceUseBackendTTS = false;   // false = browser SpeechSynthesis, true = /api/voice/speak
let _voiceAgent = 'claude';        // 'claude' (slim) of 'hermes' (snel/bulk)
let _voiceLastAnswer = '';
let _voicePendingPlan = null;      // {goal_id, plan} wacht op menselijke bevestiging
let _voiceAutoLog = false;         // Apollo's "memory galaxy": Q&A -> Obsidian

// ── Sessie (dezelfde als chat) ──────────────────────────────────────────────
async function ensureVoiceSession() {
  if (_voiceSessionId) return _voiceSessionId;
  try {
    var resp = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: (currentProject || 'Impact OS') + ' voice', agent: _voiceAgent }),
    });
    var data = await resp.json();
    _voiceSessionId = data.id;
    return _voiceSessionId;
  } catch (e) {
    try {
      var ex = await (await fetch('/api/sessions')).json();
      if (ex && ex.length) { _voiceSessionId = ex[0].id; return _voiceSessionId; }
    } catch (e2) {}
    return null;
  }
}

// ── Status-glow ─────────────────────────────────────────────────────────────
// states: idle | listening | thinking | speaking
function setVoiceState(state) {
  var orb = document.getElementById('voice-orb');
  var label = document.getElementById('voice-state-label');
  if (orb) orb.className = 'voice-orb voice-state-' + state;
  if (label) {
    var map = { idle: 'Klaar', listening: 'Luistert…', thinking: 'Denkt…', speaking: 'Praat' };
    label.textContent = map[state] || 'Klaar';
  }
}

// ── TTS (output) ────────────────────────────────────────────────────────────
function speakText(text) {
  if (!text) return;
  _voiceLastAnswer = text;
  window.speechSynthesis && window.speechSynthesis.cancel();
  if (_voiceUseBackendTTS) {
    setVoiceState('speaking');
    fetch('/api/voice/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text }),
    }).then(function (r) { return r.blob(); }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = new Audio(url);
      a.onended = function () { setVoiceState('idle'); URL.revokeObjectURL(url); };
      a.onerror = function () { setVoiceState('idle'); URL.revokeObjectURL(url); };
      a.play().catch(function () { setVoiceState('idle'); });
    }).catch(function () { setVoiceState('idle'); });
    return;
  }
  if (!('speechSynthesis' in window)) { setVoiceState('idle'); return; }
  var u = new SpeechSynthesisUtterance(text);
  u.lang = 'nl-NL';
  u.onstart = function () { setVoiceState('speaking'); _voiceSpeaking = true; };
  u.onend = function () { setVoiceState('idle'); _voiceSpeaking = false; };
  u.onerror = function () { setVoiceState('idle'); _voiceSpeaking = false; };
  window.speechSynthesis.speak(u);
}

function stopSpeaking() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  _voiceSpeaking = false;
  setVoiceState('idle');
}

// ── STT (input) ─────────────────────────────────────────────────────────────
function voiceSTTSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function startListening() {
  if (_voiceListening) return;
  if (!voiceSTTSupported()) {
    appendVoiceLine('assistant', 'Spraakherkenning niet ondersteund in deze browser. Gebruik Chrome of Edge, of typ hieronder.', true);
    return;
  }
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var rec = new SR();
  rec.lang = 'nl-NL';
  rec.continuous = true;
  rec.interimResults = true;
  var finalBuffer = '';

  rec.onresult = function (ev) {
    var interim = '';
    for (var i = ev.resultIndex; i < ev.results.length; i++) {
      var res = ev.results[i];
      if (res.isFinal) finalBuffer += res[0].transcript;
      else interim += res[0].transcript;
    }
    var live = document.getElementById('voice-live');
    if (live) live.textContent = finalBuffer + interim;
    // Barge-in: als we aan het praten zijn en de gebruiker begint te praten, stop dan.
    if (_voiceSpeaking && (finalBuffer + interim).trim().length > 0) stopSpeaking();
    if (finalBuffer.trim().length > 0) {
      var said = finalBuffer.trim();
      finalBuffer = '';
      var live2 = document.getElementById('voice-live');
      if (live2) live2.textContent = '';
      sendVoiceMessage(said);
    }
  };
  rec.onerror = function (e) {
    if (e.error === 'not-allowed') appendVoiceLine('assistant', 'Microfoon geweigerd. Sta mic-toegang toe en probeer opnieuw.', true);
    else if (e.error === 'no-speech') { /* stilte, gewoon doorgaan */ }
    else if (e.error === 'aborted') { /* handmatig gestopt */ }
  };
  rec.onend = function () {
    if (_voiceListening) { try { rec.start(); } catch (e) {} }
    else setVoiceState('idle');
  };
  try { rec.start(); _voiceRecognition = rec; _voiceListening = true; setVoiceState('listening'); }
  catch (e) { appendVoiceLine('assistant', 'Kon microfoon niet starten: ' + e.message, true); }
}

function stopListening() {
  _voiceListening = false;
  if (_voiceRecognition) { try { _voiceRecognition.stop(); } catch (e) {} _voiceRecognition = null; }
  setVoiceState('idle');
}

// ── Verstuur naar de brain (bestaande chat-stream) ───────────────────────────
async function sendVoiceMessage(text) {
  if (!text || !text.trim()) return;
  appendVoiceLine('user', text, false);
  setVoiceState('thinking');

  var sid = _voiceSessionId || await ensureVoiceSession();
  if (!sid) { appendVoiceLine('assistant', 'Kon geen sessie starten.', true); setVoiceState('idle'); return; }

  var t0 = performance.now();
  var firstTokenAt = 0;
  try {
    var resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, message: text, agent: _voiceAgent, use_obsidian: true, voice: true }),
    });
    if (!resp.ok) {
      var err = await resp.text();
      appendVoiceLine('assistant', 'Fout: ' + err.slice(0, 200), true);
      setVoiceState('idle');
      return;
    }
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var full = '';
    var ansEl = null;
    while (true) {
      var r = await reader.read();
      if (r.done) break;
      var chunk = decoder.decode(r.value, { stream: true });
      var lines = chunk.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var line = lines[li].trim();
        if (!line || line === ':' || line.startsWith(':keepalive')) continue;
        if (line === '[DONE]' || line === 'data: [DONE]') {
          if (!ansEl) ansEl = appendVoiceLine('assistant', full || '(geen antwoord)', false);
          else ansEl.innerHTML = mdToHtmlSimple(full || '(geen antwoord)');
          break;
        }
        if (line.startsWith('data: ')) {
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text' || evt.type === 'thought') {
              full += evt.text || '';
              if (!firstTokenAt) { firstTokenAt = performance.now(); showLatency(firstTokenAt - t0); }
              if (!ansEl) ansEl = appendVoiceLine('assistant', '', false);
              ansEl.innerHTML = mdToHtmlSimple(full);
              scrollVoiceLog();
            } else if (evt.type === 'error') {
              if (ansEl) ansEl.innerHTML += '<div style="color:var(--red);margin-top:8px">Fout: ' + escHtml(evt.message || '') + '</div>';
            } else if (evt.type === 'tool_start') {
              if (!ansEl) ansEl = appendVoiceLine('assistant', '', false);
              ansEl.innerHTML += '<div style="color:var(--text-dim);font-size:11px;margin:4px 0">Gebruik: ' + escHtml(evt.name || '') + '…</div>';
            }
          } catch (e) { /* niet-JSON SSE-regel, negeer */ }
        }
      }
    }
    if (full) {
      speakText(full);
      if (_voiceAutoLog && text) {
        // Apollo's "memory galaxy": elk Q&A-pair naar Obsidian + gallery.
        fetch('/api/voice/log-session', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: currentProject || '', title: text.slice(0, 80), transcript: text, answer: full }),
        }).catch(function () {});
      }
    }
    else setVoiceState('idle');
  } catch (e) {
    appendVoiceLine('assistant', 'Fout: ' + e.message, true);
    setVoiceState('idle');
  }
}

function showLatency(ms) {
  var el = document.getElementById('voice-latency');
  if (el) el.textContent = Math.round(ms) + ' ms';
}

// ── FASE 2: "Plan als doel" (Hands) ─────────────────────────────────────────
// Roept het bestaande Goal-systeem aan: plan -> (mens bevestigt) -> confirm ->
// start. Autonome uitvoering start dus NOOIT zonder de bevestigingsknop.
async function planVoiceGoal(text, btn) {
  var project = currentProject || 'WeAreImpact';
  setVoiceState('thinking');
  try {
    var resp = await fetch('/api/goals/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: text, objective: text, project: project }),
    });
    var plan = await resp.json();
    if (!plan || !plan.goal_id) {
      appendVoiceLine('assistant', 'Kon geen plan maken: ' + escHtml(plan.detail || plan.error || 'onbekend'), true);
      setVoiceState('idle');
      return;
    }
    _voicePendingPlan = { goal_id: plan.goal_id, title: text, project: project, transcript: text, plan: plan };
    renderVoicePlanPreview(plan, text);
    setVoiceState('idle');
  } catch (e) {
    appendVoiceLine('assistant', 'Fout bij plannen: ' + e.message, true);
    setVoiceState('idle');
  }
}

function renderVoicePlanPreview(plan, transcript) {
  var phases = (plan.phases || []);
  var html = '<div class="voice-plan-card"><div class="voice-plan-head">Plan klaar — ' + escHtml(plan.task_count || '?') + ' taken in ' + (phases.length || '?') + ' fasen</div>';
  html += '<div class="voice-plan-phases">';
  phases.slice(0, 6).forEach(function (p) {
    html += '<div class="voice-plan-phase"><strong>' + escHtml(p.title || '') + '</strong><br><span class="voice-plan-desc">' + escHtml((p.description || '').slice(0, 140)) + '</span></div>';
  });
  if (phases.length > 6) html += '<div class="voice-plan-phase">+ ' + (phases.length - 6) + ' meer…</div>';
  html += '</div>';
  html += '<div class="voice-plan-actions no-print">' +
    '<button class="voice-btn" onclick="startVoiceGoal()">Start uitvoering</button>' +
    '<button class="voice-btn voice-btn-ghost" onclick="cancelVoicePlan()">Annuleren</button>' +
    '</div></div>';
  var planBox = document.getElementById('voice-plan');
  if (planBox) { planBox.innerHTML = html; planBox.style.display = 'block'; }
}

async function startVoiceGoal() {
  if (!_voicePendingPlan) return;
  var p = _voicePendingPlan;
  var btn = document.querySelector('.voice-plan-actions .voice-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Starten…'; }
  try {
    await fetch('/api/goals/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal_id: p.goal_id }),
    });
    var startResp = await fetch('/api/goals/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal_id: p.goal_id }),
    });
    var startInfo = await startResp.json().catch(function () { return {}; });
    appendVoiceLine('assistant', 'Doel gestart: "' + p.title + '". De agent werkt dit nu op de achtergrond uit — de fasen verschijnen in de Doelen-tab.', false);
    // Zet in de gallery.
    await fetch('/api/voice/artifact', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project: p.project, goal_id: p.goal_id, title: p.title,
        transcript: p.transcript, artifact_type: 'goal', status: 'running',
      }),
    });
    loadVoiceGallery();
  } catch (e) {
    appendVoiceLine('assistant', 'Fout bij starten doel: ' + e.message, true);
  } finally {
    _voicePendingPlan = null;
    var box = document.getElementById('voice-plan');
    if (box) box.style.display = 'none';
    setVoiceState('idle');
  }
}

function cancelVoicePlan() {
  _voicePendingPlan = null;
  var box = document.getElementById('voice-plan');
  if (box) box.style.display = 'none';
}

// ── Gallery ─────────────────────────────────────────────────────────────────
async function loadVoiceGallery() {
  var g = document.getElementById('voice-gallery');
  if (!g) return;
  try {
    var url = '/api/voice/artifacts?limit=30' + (currentProject ? '&project=' + encodeURIComponent(currentProject) : '');
    var rows = await (await fetch(url)).json();
    if (!rows || !rows.length) {
      g.innerHTML = '<div class="voice-gallery-empty">Nog niets gebouwd. Zeg bijvoorbeeld "Plan een landingpage voor X" of "Bouw een tool die Y doet".</div>';
      return;
    }
    g.innerHTML = rows.map(function (a) {
      var meta = [];
      if (a.artifact_type) meta.push(a.artifact_type);
      if (a.status) meta.push(a.status);
      return '<div class="voice-gallery-item">' +
        '<div class="vg-title">' + escHtml(a.title) + '</div>' +
        (a.transcript ? '<div class="vg-transcript">"' + escHtml(a.transcript.slice(0, 160)) + '"</div>' : '') +
        (a.artifact ? '<div class="vg-artifact"><a href="' + escAttr(a.artifact) + '" target="_blank" rel="noopener">' + escHtml(a.artifact.slice(0, 80)) + '</a></div>' : '') +
        '<div class="vg-meta">' + meta.map(escHtml).join(' · ') + ' · ' + escHtml((a.created_at || '').replace('T', ' ').slice(0, 16)) + '</div>' +
        '</div>';
    }).join('');
  } catch (e) {
    g.innerHTML = '<div class="voice-gallery-empty">Gallery niet geladen: ' + escHtml(e.message) + '</div>';
  }
}

// ── Log-regels ──────────────────────────────────────────────────────────────
function appendVoiceLine(role, text, isError) {
  var log = document.getElementById('voice-log');
  if (!log) return null;
  var div = document.createElement('div');
  div.className = 'voice-line voice-' + role + (isError ? ' voice-err' : '');
  div.innerHTML = (role === 'user' ? '<span class="voice-who">Jij</span> ' : '<span class="voice-who">Iris</span> ') + (isError ? escHtml(text) : text);
  log.appendChild(div);
  scrollVoiceLog();
  return div;
}
function scrollVoiceLog() {
  var log = document.getElementById('voice-log');
  if (log) log.scrollTop = log.scrollHeight;
}

// ── Wall / fullscreen mode ──────────────────────────────────────────────────
function toggleVoiceWall() {
  _voiceFullscreen = !_voiceFullscreen;
  var wrap = document.getElementById('voice-app');
  if (!wrap) return;
  if (_voiceFullscreen) {
    wrap.classList.add('voice-wall');
    if (wrap.requestFullscreen) wrap.requestFullscreen().catch(function () {});
  } else {
    wrap.classList.remove('voice-wall');
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
  }
}

// ── Render ──────────────────────────────────────────────────────────────────
function renderVoiceTab(main) {
  main.innerHTML = renderSidebar() + '<div class="main-content">' +
    renderMobileBar() +
    '<div class="project-header"><div><h1>Iris — spraakassistent' + (currentProject ? ' · ' + escHtml(currentProject) : '') + '</h1>' +
    '<p class="meta">Praat hardop, krijg direct antwoord, bouw met spraak</p></div>' +
    '<div class="actions"><button id="voice-wall-btn" onclick="toggleVoiceWall()">Wall</button>' +
    '<button onclick="goHome()">Projecten</button></div></div>' +
    '<div id="voice-app" class="voice-app">' +
      '<div class="voice-stage">' +
        '<div id="voice-orb" class="voice-orb voice-state-idle"></div>' +
        '<div class="voice-orb-meta"><span id="voice-state-label">Klaar</span> · <span id="voice-latency">– ms</span></div>' +
        '<div class="voice-controls no-print">' +
          '<button id="voice-mic-btn" class="voice-btn" onclick="toggleVoiceMic()">Start luisteren</button>' +
          '<button class="voice-btn voice-btn-ghost" onclick="stopSpeaking()">Stop spraak</button>' +
          '<button class="voice-btn voice-btn-ghost" onclick="speakText(_voiceLastAnswer)">Lees voor</button>' +
          '<button class="voice-btn voice-btn-ghost" onclick="readBriefingAloud()">Lees briefing</button>' +
          '<label class="voice-toggle"><input type="checkbox" id="voice-tts-backend" onchange="toggleBackendTTS(this)"> Backend-TTS (edge)</label>' +
          '<label class="voice-toggle"><input type="checkbox" id="voice-agent-mode" onchange="toggleVoiceAgent(this)"> Agent-modus</label>' +
          '<label class="voice-toggle"><input type="checkbox" id="voice-autolog" onchange="toggleVoiceAutoLog(this)"> Auto-log Obsidian</label>' +
        '</div>' +
      '</div>' +
      '<div id="voice-plan" class="voice-plan" style="display:none"></div>' +
      '<div id="voice-log" class="voice-log"></div>' +
      '<div class="voice-input-row no-print">' +
        '<input id="voice-type" placeholder="Of typ hier (Enter om te versturen, of Plan als doel)" onkeydown="if(event.key===\'Enter\'){var v=this.value.trim();this.value=\'\';sendVoiceMessage(v);}">' +
        '<button class="voice-btn voice-btn-ghost" onclick="var v=document.getElementById(\'voice-type\').value.trim();if(v)planVoiceGoal(v);">Plan als doel</button>' +
      '</div>' +
      (voiceSTTSupported() ? '' : '<div class="voice-warn">Spraakherkenning werkt alleen in Chrome of Edge. Typ hierboven om toch te gebruiken.</div>') +
      '<div class="voice-gallery-wrap"><div class="voice-gallery-head">Gallery — wat je bouwde</div><div id="voice-gallery" class="voice-gallery"></div></div>' +
    '</div>' +
    '</div>';

  ensureVoiceSession();
  loadVoiceGallery();
}

// Knoppen (globaal, want inline onclick).
function toggleVoiceMic() {
  if (_voiceListening) {
    stopListening();
    var b = document.getElementById('voice-mic-btn');
    if (b) b.textContent = 'Start luisteren';
  } else {
    startListening();
    var b2 = document.getElementById('voice-mic-btn');
    if (b2) b2.textContent = 'Stop luisteren';
  }
}
function toggleBackendTTS(box) { _voiceUseBackendTTS = !!box.checked; }
function toggleVoiceAgent(box) {
  _voiceAgent = box.checked ? 'hermes' : 'claude';
  _voiceSessionId = null;
  ensureVoiceSession();
}
function toggleVoiceAutoLog(box) { _voiceAutoLog = !!box.checked; }
async function readBriefingAloud() {
  try {
    var r = await fetch('/api/voice/briefing');
    var data = await r.json();
    if (!data.available || !data.text) {
      appendVoiceLine('assistant', data.note || 'Geen briefing beschikbaar. Draai eerst de Iris-briefing.', true);
      return;
    }
    appendVoiceLine('assistant', 'Briefing van ' + (data.report_date || 'vandaag') + ':', false);
    speakText(data.text);
  } catch (e) {
    appendVoiceLine('assistant', 'Fout bij briefing: ' + e.message, true);
  }
}
