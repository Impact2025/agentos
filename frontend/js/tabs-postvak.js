// ═════════════════════════════════════════════════════════════════════════════
//  POSTVAK TAB — per-project vs. persoonlijk onderscheid.
//
//  Tot 13 aug 2026 haalde deze tab altijd de énige, globale persoonlijke
//  Outlook-inbox op (v.munster@weareimpact.nl) en gooide die — ongefilterd —
//  in elk project. Daardoor stonden ING/ClubMatch/Impact2025-CI/CD in
//  Bewaardvoorjou. Nu volgt het Postvak hetzelfde patroon als elke andere tab:
//  het filtert op het geselecteerde project.
//
//    • In een project  → de per-project mailbox-inbox (helpdesk-systeem:
//      mail_inbox via /api/postvak?project=...). Alleen de mailbox van dát
//      project komt in beeld.
//    • Op hoofdniveau (geen project) → de persoonlijke Outlook-inbox, precies
//      zoals de telefoon (Iris Remote) die laat zien.
//
//  De persoonlijke-modus herbruikt de bestaande Outlook-endpoints; de
//  project-modus krijgt zijn eigen lichte weergave (geen AI-triage/antwoorden
//  — dat is de Helpdesk-tab, met review-gate; het Postvak is lezen + archiveer).
// ═════════════════════════════════════════════════════════════════════════════

var _pvPollTimer = null;
var _pvBodyCache = {};

async function renderPostvakTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Postvak laden...</p></div>';

  // ── Project-modus: laad de per-project mailbox-inbox ──
  if (currentProject) {
    let data;
    try {
      data = await (await fetch('/api/postvak?project=' + encodeURIComponent(currentProject))).json();
    } catch (e) {
      el.innerHTML = '<div class="empty-state">Fout bij laden postvak: ' + escHtml(e.message) + '</div>';
      return;
    }

    if (data.mode === 'project' && !data.address) {
      // Dit project heeft (nog) geen eigen mailbox. Bied de Helpdesk-tab aan.
      el.innerHTML = '<div class="empty-state">' +
        '<p style="font-size:14px;font-weight:600;color:var(--text-dim);margin-bottom:6px">' +
        escHtml(currentProject) + ' heeft nog geen eigen postvak</p>' +
        '<p style="color:#94a3b8;font-size:12px">Koppel een mailbox op de Helpdesk-tab (⋮ → Helpdesk) zodat ' +
        'de inkomende mail van dit project hier verschijnt. Je persoonlijke inbox staat bovenin, ' +
        'zonder project geselecteerd.</p></div>';
      return;
    }

    var html = '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px">' +
      '<div><h2 style="font-size:18px;font-weight:700;margin:0">Postvak — ' + escHtml(data.address || currentProject) + '</h2>' +
      '<p style="font-size:12px;color:#64748b;margin:2px 0 0">' +
      (data.emails && data.emails.length ? data.emails.length + ' bericht' + (data.emails.length > 1 ? 'en' : '') + ' in het postvak van ' + escHtml(currentProject)
                                        : 'Geen berichten in het postvak van ' + escHtml(currentProject)) +
      '</p></div>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="postvakRefreshProject(this)" class="btn btn-sm btn-primary">Nu ophalen</button>' +
      '</div></div>';

    if (!data.emails || !data.emails.length) {
      html += '<div class="empty-state"><p style="color:#94a3b8;font-size:13px">Leeg — er ligt niets in de mailbox van ' + escHtml(currentProject) + '.</p></div>';
    } else {
      // Eén bucket "Vraagt om actie" voor question/appointment, de rest "Ter info".
      var actie = data.emails.filter(function (m) { return m.bucket === 'actie'; });
      var info = data.emails.filter(function (m) { return m.bucket !== 'actie'; });
      html += renderPostvakBucket('Vraagt om actie', actie, 'Niets — het postvak van ' + currentProject + ' is bij.');
      html += renderPostvakBucket('Ter info', info, '');
    }

    el.innerHTML = html;
    return;
  }

  // ── Hoofdniveau (geen project): de persoonlijke Outlook-inbox ──
  var status;
  try {
    status = await (await fetch('/api/outlook/status')).json();
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
    return;
  }

  if (!status.configured) {
    el.innerHTML = '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:var(--text-dim);margin-bottom:6px">Postvak is niet geconfigureerd</p>' +
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

  var html2 = '';
  html2 += '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px">' +
    '<div><h2 style="font-size:18px;font-weight:700;margin:0">Postvak' + (status.account ? ' — ' + escHtml(status.account.email) : '') + '</h2>' +
    '<p style="font-size:12px;color:#64748b;margin:2px 0 0">' +
    (sorted.untriaged > 0 ? sorted.untriaged + ' nog niet getrieerd · ' : '') +
    sorted.needs_reply.length + ' vraagt om actie · ' + sorted.waiting.length + ' wacht op antwoord</p></div>' +
    '<div style="display:flex;gap:6px">' +
    (sorted.untriaged > 0 ? '<button onclick="postvakTriageBatch(this)" class="btn btn-sm btn-ghost">Trieer ' + sorted.untriaged + '</button>' : '') +
    '<button onclick="postvakSync(this)" class="btn btn-sm btn-primary">Nu ophalen</button>' +
    '</div></div>';

  html2 += renderPostvakBucket('Vraagt om actie', sorted.needs_reply, 'Niets — je postvak is bij.');
  html2 += renderPostvakBucket('Wacht op antwoord van iemand anders', sorted.waiting, '');
  html2 += renderPostvakBucket('Ter info', sorted.fyi, '');
  html2 += renderPostvakRules(rules);

  el.innerHTML = html2;
}

// Ververs de project-mailbox vanaf de server (poll de helpdesk-mailboxen).
async function postvakRefreshProject(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Ophalen...'; }
  try {
    await fetch('/api/mail/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (e) { /* niet-blokkerend */ }
  await renderPostvakTab(document.getElementById('tab-content'));
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

// Urgentie is een statusbetekenis, dus een .pill i.p.v. een losse hexkleur.
function _pvPrioPill(p) {
  p = p || 0;
  if (p >= 70) return '<span class="pill pill-danger">Urgent</span>';
  if (p >= 40) return '<span class="pill pill-warn">Belangrijk</span>';
  return '';
}

// Zelfde rij-patroon als het Postvak-paneel op de Control Room.
// Project-modus: geen apart detail-endpoint — de body zit al in de rij.
function renderPostvakRow(r) {
  var id = escAttr(r.id);
  var name = r.from_name || r.from_email || 'Onbekend';
  var bodyHtml = r.body_text
    ? '<pre style="white-space:pre-wrap;font-family:inherit;font-size:12px;color:#475569;background:var(--neutral-bg);border:1px solid var(--card-border);border-radius:6px;padding:10px;max-height:240px;overflow-y:auto">' + escHtml(r.body_text) + '</pre>'
    : '';
  return '<div class="list-row" id="pv-item-' + id + '">' +
    '<div class="list-avatar" style="background:' + _avatarColor(name) + '">' + escHtml(_initial(name)) + '</div>' +
    '<div style="flex:1;min-width:0;cursor:pointer" onclick="postvakToggle(\'' + id + '\')">' +
    '<div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">' +
    '<span style="font-size:12px;font-weight:600;color:var(--text-dim)">' + escHtml(name) + '</span>' +
    '<span style="font-size:10px;color:#94a3b8">' + escHtml(_relTime(r.received_at)) + '</span>' +
    _pvPrioPill(r.priority) +
    '</div>' +
    '<div style="font-size:13px;font-weight:' + (r.is_read ? '400' : '700') + ';color:var(--text)">' + escHtml(r.subject || '(geen onderwerp)') + '</div>' +
    (r.ai_summary ? '<p style="font-size:12px;color:#64748b;margin:4px 0 0">' + escHtml(r.ai_summary) + '</p>' : '') +
    '<div id="pv-body-' + id + '" style="display:none;margin-top:8px">' + bodyHtml + '</div>' +
    '</div>' +
    '<div style="display:flex;gap:4px;flex-shrink:0">' +
    '<button title="Deze mail hoeft niets van je" onclick="postvakArchiveProject(\'' + id + '\',event)" ' +
    'class="btn btn-sm btn-ghost">Archiveer</button>' +
    '</div>' +
    '</div>';
}

// Project-modus archiveren: markeer de inbox-rij als verwerkt (classified='ignored'),
// zodat hij uit het Postvak verdwijnt maar in de DB blijft staan voor de Helpdesk.
async function postvakArchiveProject(id, ev) {
  if (ev) ev.stopPropagation();
  var realId = ('' + id).replace(/^mb_/, '');
  try {
    await fetch('/api/mail/inbox/' + encodeURIComponent(realId) + '/archive', { method: 'POST' });
  } catch (e) { /* best-effort */ }
  var el = document.getElementById('pv-item-' + id);
  if (el) el.remove();
}

async function postvakToggle(id) {
  var box = document.getElementById('pv-body-' + id);
  if (!box) return;
  if (box.style.display === 'block') { box.style.display = 'none'; return; }
  box.style.display = 'block';
}

// ── Persoonlijke-modus helpers (onveranderd) ────────────────────────────────

async function postvakArchive(id, ev) {
  if (ev) ev.stopPropagation();
  try {
    await fetch('/api/outlook/emails/' + encodeURIComponent(id) + '/archive', { method: 'POST' });
    var el = document.getElementById('pv-item-' + id);
    if (el) el.remove();
  } catch (e) { alert('Archiveren mislukt: ' + e.message); }
}

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

function renderPostvakRules(data) {
  var rules = (data && data.rules) || [];
  var stats = (data && data.stats) || {};
  var eigen = rules.filter(function (r) { return r.source === 'mens'; });
  var systeem = rules.filter(function (r) { return r.source !== 'mens'; });
  var rij = function (r) {
    return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-top:1px solid #f1f5f9">' +
      '<span style="font-size:12px;color:var(--text-dim);flex:1">' + escHtml(r.pattern) +
      ' <span style="color:#94a3b8">· ' + escHtml(r.scope) + ' · ' + escHtml(r.action) + '</span></span>' +
      '<span style="font-size:11px;color:#94a3b8">' + (r.hits || 0) + '× geraakt</span>' +
      '<button onclick="postvakUnblock(' + r.id + ')" class="btn btn-sm btn-ghost">Intrekken</button>' +
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
  btn.disabled = false; btn.textContent = 'Concept genereren';
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
    btn.disabled = false; btn.textContent = 'Verstuur';
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

function renderPostvakConnectCard() {
  return '<div class="section-card" style="max-width:420px">' +
    '<h3 style="margin:0 0 8px;font-size:15px;font-weight:700">Koppel je Outlook-postvak</h3>' +
    '<p style="font-size:12px;color:#64748b;margin-bottom:10px">Eén keer inloggen met je Microsoft-account — geen wachtwoord dat Impact OS bewaart. Dezelfde login geeft ook toegang tot je agenda.</p>' +
    '<div id="pv-connect-flow" style="display:none;background:var(--neutral-bg);border:1px solid var(--card-border);border-radius:8px;padding:12px;margin-bottom:10px">' +
    '<p style="font-size:12px;color:#475569;margin-bottom:6px">Ga naar <a id="pv-connect-link" href="#" target="_blank">Microsoft login</a> en voer deze code in:</p>' +
    '<div id="pv-connect-code" style="font-size:22px;font-weight:800;letter-spacing:2px;color:var(--accent);margin-bottom:6px">••••••••</div>' +
    '<div id="pv-connect-msg" style="font-size:12px;color:#64748b"></div></div>' +
    '<button id="pv-connect-btn" onclick="postvakConnect()" class="btn btn-primary">Koppel Outlook-account</button>' +
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
