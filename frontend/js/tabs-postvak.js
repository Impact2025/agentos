// ═══════════════════════════════════════════════════════════════════
//  POSTVAK TAB — AI-getrieerde Outlook-inbox (Microsoft Graph, device-code
//  login). Zelfde backend als de mail die de telefoon (Iris Remote) al laat
//  zien; dit is het bureaublad-equivalent zodat een klant zonder Iris Remote
//  (bv. Nicole) er ook zonder telefoon bij kan.
// ═══════════════════════════════════════════════════════════════════

var _pvPollTimer = null;
var _pvBodyCache = {};

async function renderPostvakTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Postvak laden...</p></div>';
  var status;
  try {
    status = await (await fetch('/api/outlook/status')).json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
    return;
  }

  if (!status.configured) {
    el.innerHTML = '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:6px">Postvak is niet geconfigureerd</p>' +
      '<p style="color:#94a3b8;font-size:12px">OUTLOOK_CLIENT_ID ontbreekt in .env — zie backend/domains/outlook/service.py voor de Azure-app-registratie-stappen.</p></div>';
    return;
  }

  if (!status.authenticated || !status.token_valid) {
    el.innerHTML = renderPostvakConnectCard(status);
    return;
  }

  var sorted, rules;
  try {
    sorted = await (await fetch('/api/outlook/sorted')).json();
    rules = await (await fetch('/api/outlook/rules')).json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout bij laden inbox: ' + escHtml(e.message) + '</div>';
    return;
  }

  var html = '';
  html += '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px">' +
    '<div><h2 style="font-size:18px;font-weight:700;margin:0">Postvak' + (status.account ? ' — ' + escHtml(status.account.email) : '') + '</h2>' +
    '<p style="font-size:12px;color:#64748b;margin:2px 0 0">' +
    (sorted.untriaged > 0 ? sorted.untriaged + ' nog niet getrieerd · ' : '') +
    sorted.needs_reply.length + ' vraagt om actie · ' + sorted.waiting.length + ' wacht op antwoord</p></div>' +
    '<div style="display:flex;gap:6px">' +
    (sorted.untriaged > 0 ? '<button onclick="postvakTriageBatch(this)" style="padding:7px 14px;background:#fff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">🤖 Trieer ' + sorted.untriaged + '</button>' : '') +
    '<button onclick="postvakSync(this)" style="padding:7px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">↻ Nu ophalen</button>' +
    '</div></div>';

  html += renderPostvakBucket('Vraagt om actie', sorted.needs_reply, 'Niets — je postvak is bij.');
  html += renderPostvakBucket('Wacht op antwoord van iemand anders', sorted.waiting, '');
  html += renderPostvakBucket('Ter info', sorted.fyi, '');
  html += renderPostvakRules(rules);

  el.innerHTML = html;
}

function renderPostvakBucket(title, rows, emptyText) {
  if (!rows.length && !emptyText) return '';
  var html = '<div class="section-card" style="margin-bottom:16px">' +
    '<h3 style="margin:0 0 10px;font-size:14px;font-weight:700">' + escHtml(title) + '</h3>';
  if (!rows.length) {
    html += '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:10px">' + escHtml(emptyText) + '</p>';
  } else {
    rows.forEach(function (r) { html += renderPostvakRow(r); });
  }
  return html + '</div>';
}

var _pvPrioColor = function (p) { return p >= 70 ? '#ef4444' : (p >= 40 ? '#d97706' : '#94a3b8'); };

// Twee handelingen naast het openen, met dezelfde scheiding als op de telefoon:
// archiveren gaat over dít bericht, blokkeren over de afzender. Beide zijn
// lokaal en omkeerbaar — er wordt niets in de échte mailbox verplaatst.
function _pvRowActions(r) {
  var id = escAttr(r.id);
  var afz = escAttr(r.from_email || '');
  var knop = 'padding:3px 8px;border-radius:5px;font-size:11px;font-weight:600;cursor:pointer;background:#fff';
  return '<div style="display:flex;gap:4px;flex-shrink:0">' +
    '<button title="Deze mail hoeft niets van je" onclick="postvakArchive(\'' + id + '\',event)" ' +
    'style="' + knop + ';color:#64748b;border:1px solid #e2e8f0">Archiveer</button>' +
    (afz ? '<button title="Nooit meer mail van ' + afz + '" onclick="postvakBlock(\'' + id + '\',\'' + afz + '\',event)" ' +
      'style="' + knop + ';color:#b91c1c;border:1px solid #fecaca">Blokkeer</button>' : '') +
    '</div>';
}

function renderPostvakRow(r) {
  var id = escAttr(r.id);
  return '<div class="postvak-item" id="pv-item-' + id + '" style="border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin-bottom:8px;background:#fff">' +
    '<div style="display:flex;align-items:center;gap:8px;cursor:pointer" onclick="postvakToggle(\'' + id + '\')">' +
    '<span style="width:8px;height:8px;border-radius:50%;background:' + _pvPrioColor(r.priority || 0) + ';flex-shrink:0"></span>' +
    '<span style="font-size:13px;font-weight:' + (r.is_read ? '400' : '700') + ';color:#1e293b;flex:1">' + escHtml(r.subject || '(geen onderwerp)') + '</span>' +
    '<span style="font-size:11px;color:#94a3b8">' + escHtml(r.from_name || r.from_email || '') + '</span>' +
    _pvRowActions(r) + '</div>' +
    (r.ai_summary ? '<p style="font-size:12px;color:#64748b;margin:6px 0 0 16px">' + escHtml(r.ai_summary) + '</p>' : '') +
    (r.ai_action ? '<p style="font-size:11px;color:#4f46e5;margin:2px 0 0 16px"><strong>Actie:</strong> ' + escHtml(r.ai_action) + '</p>' : '') +
    '<div id="pv-body-' + id + '" style="display:none;margin-top:8px;margin-left:16px"></div>' +
    '</div>';
}

async function postvakToggle(id) {
  var box = document.getElementById('pv-body-' + id);
  if (!box) return;
  if (box.style.display === 'block') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = '<p style="font-size:12px;color:#94a3b8">Laden...</p>';
  var email = _pvBodyCache[id];
  if (!email) {
    try {
      email = await (await fetch('/api/outlook/emails/' + encodeURIComponent(id))).json();
      _pvBodyCache[id] = email;
    } catch (e) {
      box.innerHTML = '<p style="font-size:12px;color:#ef4444">Fout: ' + escHtml(e.message) + '</p>';
      return;
    }
  }
  if (!email.is_read) postvakMarkRead(id);
  var bodyHtml = email.body_html || ('<pre style="white-space:pre-wrap;font-family:inherit">' + escHtml(email.body_preview || '') + '</pre>');
  var draftText = email.suggested_reply || email.reply_hint || '';
  box.innerHTML =
    '<div style="font-size:12px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;max-height:220px;overflow-y:auto">' + bodyHtml + '</div>' +
    '<textarea id="pv-reply-' + id + '" placeholder="Concept-antwoord..." style="width:100%;min-height:100px;font-size:12px;line-height:1.5;padding:8px;margin-top:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:#f8fafc">' + escHtml(draftText) + '</textarea>' +
    '<div style="display:flex;gap:6px;margin-top:6px">' +
    '<button onclick="postvakGenerateDraft(\'' + id + '\',this)" style="padding:5px 12px;background:#fff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">🤖 Concept genereren</button>' +
    '<button onclick="postvakSend(\'' + id + '\',this)" style="padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">✉ Verstuur</button>' +
    '</div>';
}

async function postvakArchive(id, ev) {
  if (ev) ev.stopPropagation();
  try {
    await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/archive', { method: 'POST' });
    var el = document.getElementById('pv-item-' + id);
    if (el) el.remove();
  } catch (e) { alert('Archiveren mislukt: ' + e.message); }
}

// De spam-knop. Bevestigen is hier geen formaliteit: de regel werkt met
// terugwerkende kracht, dus één klik ruimt ook op wat er al ligt — dat is de
// bedoeling, maar je moet het wel weten. De melding zegt daarom hoeveel mails
// er zijn opgeruimd; een regel die stil niets deed voelt als een kapotte knop.
async function postvakBlock(id, afzender, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm('Nooit meer mail van ' + afzender + ' in dit postvak?\n\n'
    + 'Alles wat er al van deze afzender ligt wordt opgeruimd. Terugdraaien kan '
    + 'onderaan bij "Geblokkeerde afzenders".')) return;
  try {
    var resp = await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/spam', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'adres', action: 'spam' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    var data = await resp.json();
    await renderPostvakTab(document.getElementById('tab-content'));
    alert(afzender + ' geblokkeerd — ' + (data.applied || 0) + ' mail(s) opgeruimd.');
  } catch (e) { alert('Blokkeren mislukt: ' + e.message); }
}

// Geblokkeerde afzenders: zonder dit scherm is strenger filteren onverantwoord.
// Je moet kunnen zien wát er weg is en waarom, en het met één klik terug kunnen
// draaien — anders is "0 urgent" niet te onderscheiden van "alles weggegooid".
function renderPostvakRules(data) {
  var rules = (data && data.rules) || [];
  var stats = (data && data.stats) || {};
  var eigen = rules.filter(function (r) { return r.source === 'mens'; });
  var systeem = rules.filter(function (r) { return r.source !== 'mens'; });
  var rij = function (r) {
    return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-top:1px solid #f1f5f9">' +
      '<span style="font-size:12px;color:#1e293b;flex:1">' + escHtml(r.pattern) +
      ' <span style="color:#94a3b8">· ' + escHtml(r.scope) + ' · ' + escHtml(r.action) + '</span></span>' +
      '<span style="font-size:11px;color:#94a3b8">' + (r.hits || 0) + '× geraakt</span>' +
      '<button onclick="postvakUnblock(' + r.id + ')" style="padding:2px 8px;background:#fff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:5px;font-size:11px;cursor:pointer">Intrekken</button>' +
      '</div>';
  };
  return '<details class="section-card" style="margin-bottom:16px">' +
    '<summary style="cursor:pointer;font-size:14px;font-weight:700">Geblokkeerde afzenders' +
    ' <span style="font-weight:400;color:#64748b;font-size:12px">— ' + (stats.blocked_period || 0) +
    ' mails weggehouden in ' + (stats.days || 7) + ' dagen</span></summary>' +
    '<div style="margin-top:10px">' +
    (eigen.length ? eigen.map(rij).join('') :
      '<p style="font-size:12px;color:#94a3b8">Je hebt zelf nog niemand geblokkeerd.</p>') +
    '<details style="margin-top:10px"><summary style="cursor:pointer;font-size:12px;color:#64748b">' +
    systeem.length + ' standaardregels (webshops, vacaturesites, digests, systeemmeldingen)</summary>' +
    '<div style="margin-top:6px">' + systeem.map(rij).join('') + '</div></details>' +
    '</div></details>';
}

async function postvakUnblock(ruleId) {
  try {
    var resp = await fetch('/api/outlook/rules/' + ruleId, { method: 'DELETE' });
    var data = await resp.json();
    await renderPostvakTab(document.getElementById('tab-content'));
    alert('Regel ingetrokken — ' + (data.released || 0) + ' mail(s) terug in het postvak.');
  } catch (e) { alert('Intrekken mislukt: ' + e.message); }
}

async function postvakMarkRead(id) {
  try { await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/read', { method: 'POST' }); } catch (e) {}
}

// Leest een SSE-response (zelfde patroon als tabs-settings-chat.js:sendChatMessage)
// en geeft elk 'data: {...}'-event door aan `onEvent`.
async function _pvReadSSE(resp, onEvent) {
  var reader = resp.body.getReader();
  var decoder = new TextDecoder();
  var buf = '';
  while (true) {
    var res = await reader.read();
    if (res.done) break;
    buf += decoder.decode(res.value, { stream: true });
    var lines = buf.split('\n');
    buf = lines.pop();
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line.startsWith('data: ')) continue;
      try { onEvent(JSON.parse(line.slice(6))); } catch (e) {}
    }
  }
}

async function postvakGenerateDraft(id, btn) {
  btn.disabled = true; btn.textContent = 'Bezig...';
  var ta = document.getElementById('pv-reply-' + id);
  if (ta) ta.value = '';
  try {
    var resp = await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
    });
    await _pvReadSSE(resp, function (ev) {
      if (ev.type === 'text' && ta) ta.value += ev.text;
    });
  } catch (e) {
    if (ta) ta.value = '(genereren mislukt: ' + e.message + ')';
  }
  btn.disabled = false; btn.textContent = '🤖 Concept genereren';
}

function _pvTextToHtml(text) {
  return text.split(/\n\s*\n/).map(function (p) {
    return '<p>' + escHtml(p.trim()).replace(/\n/g, '<br>') + '</p>';
  }).join('');
}

async function postvakSend(id, btn) {
  var ta = document.getElementById('pv-reply-' + id);
  var text = ta ? ta.value.trim() : '';
  if (!text) { alert('Nog geen concept — genereer er eerst een of typ zelf iets.'); return; }
  if (!confirm('Antwoord versturen?')) return;
  btn.disabled = true; btn.textContent = 'Versturen...';
  try {
    var resp = await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/reply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body_html: _pvTextToHtml(text) }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    var item = document.getElementById('pv-item-' + id);
    if (item) item.remove();
  } catch (e) {
    alert('Versturen mislukt: ' + e.message);
    btn.disabled = false; btn.textContent = '✉ Verstuur';
  }
}

async function postvakSync(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Ophalen...';
  try {
    await fetch('/api/outlook/sync', { method: 'POST' });
  } catch (e) {}
  await renderPostvakTab(document.getElementById('tab-content'));
}

async function postvakTriageBatch(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Trieert...';
  try {
    var resp = await fetch('/api/outlook/triage/batch', { method: 'POST' });
    await _pvReadSSE(resp, function () {});
  } catch (e) {}
  await renderPostvakTab(document.getElementById('tab-content'));
}

// ── Nog niet gekoppeld: eigen device-code-kaart (los van Instellingen, die
// elementen bestaan hier niet in de DOM). ──
function renderPostvakConnectCard() {
  return '<div class="section-card" style="max-width:420px">' +
    '<h3 style="margin:0 0 8px;font-size:15px;font-weight:700">Koppel je Outlook-postvak</h3>' +
    '<p style="font-size:12px;color:#64748b;margin-bottom:10px">Eén keer inloggen met je Microsoft-account — geen wachtwoord dat Agent OS bewaart. Dezelfde login geeft ook toegang tot je agenda.</p>' +
    '<div id="pv-connect-flow" style="display:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px">' +
    '<p style="font-size:12px;color:#475569;margin-bottom:6px">Ga naar <a id="pv-connect-link" href="#" target="_blank">Microsoft login</a> en voer deze code in:</p>' +
    '<div id="pv-connect-code" style="font-size:22px;font-weight:800;letter-spacing:2px;color:#0ea5e9;margin-bottom:6px">••••••••</div>' +
    '<div id="pv-connect-msg" style="font-size:12px;color:#64748b"></div></div>' +
    '<button id="pv-connect-btn" onclick="postvakConnect()" style="padding:7px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Koppel Outlook-account</button>' +
    '</div>';
}

function postvakConnect() {
  var flowEl = document.getElementById('pv-connect-flow');
  var codeEl = document.getElementById('pv-connect-code');
  var linkEl = document.getElementById('pv-connect-link');
  var msgEl = document.getElementById('pv-connect-msg');
  var btn = document.getElementById('pv-connect-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/outlook/auth/start', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (flow) {
    if (flow.detail) throw new Error(flow.detail);
    if (codeEl) codeEl.textContent = flow.user_code;
    if (linkEl) linkEl.href = flow.verification_uri;
    if (flowEl) flowEl.style.display = 'block';
    if (_pvPollTimer) clearInterval(_pvPollTimer);
    _pvPollTimer = setInterval(postvakPollAuth, 2000);
  }).catch(function (e) {
    if (msgEl) msgEl.textContent = 'Fout: ' + e.message;
    if (btn) { btn.disabled = false; btn.textContent = 'Koppel Outlook-account'; }
  });
}

function postvakPollAuth() {
  fetch('/api/outlook/auth/status').then(function (r) { return r.json(); }).then(function (st) {
    var msgEl = document.getElementById('pv-connect-msg');
    if (st.status === 'done') {
      if (_pvPollTimer) { clearInterval(_pvPollTimer); _pvPollTimer = null; }
      renderPostvakTab(document.getElementById('tab-content'));
    } else if (st.status === 'error') {
      if (_pvPollTimer) { clearInterval(_pvPollTimer); _pvPollTimer = null; }
      if (msgEl) msgEl.textContent = 'Mislukt: ' + (st.error || 'onbekende fout');
    } else if (msgEl) {
      msgEl.textContent = 'Wachten op inloggen...';
    }
  }).catch(function () {
    if (_pvPollTimer) { clearInterval(_pvPollTimer); _pvPollTimer = null; }
  });
}
