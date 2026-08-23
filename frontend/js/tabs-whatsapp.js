// ═════════════════════════════════════════════════════════════════════════════
//  WHATSAPP TAB — het Communicatie-scherm van Iris Remote (CLAUDE.md 14h), maar
//  dan op :1250 in plaats van alleen op de telefoon. De data leeft in Neon
//  (remote-systeem), dus deze tab praat er alleen via de bestaande
//  bridge-proxy mee (backend/domains/bridge/router.py `/api/bridge/whatsapp*`)
//  — geen tweede waarheid, geen eigen database-koppeling.
//
//  Drie secties, zelfde volgorde als op de telefoon en om dezelfde reden:
//  wat op jou wacht (escalaties) hoort bovenaan, "nieuw" is interessanter dan
//  "bekend" maar minder urgent dan "wacht", en de volledige lijst is de
//  bodem-laag voor wie een specifiek gesprek zoekt.
// ═════════════════════════════════════════════════════════════════════════════

var _waConversationsCache = [];
var _waOpenThread = null;

async function renderWhatsAppTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>WhatsApp laden...</p></div>';

  var statsResp, escResp, convResp;
  try {
    [statsResp, escResp, convResp] = await Promise.all([
      fetch('/api/bridge/whatsapp-stats').then(function (r) { return r.json(); }),
      fetch('/api/bridge/whatsapp').then(function (r) { return r.json(); }),
      fetch('/api/bridge/whatsapp-conversations').then(function (r) { return r.json(); }),
    ]);
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Fout bij laden: ' + escHtml(e.message) + '</div>';
    return;
  }

  if (statsResp.ok === false || escResp.ok === false || convResp.ok === false) {
    var detail = (statsResp && statsResp.detail) || (escResp && escResp.detail) || (convResp && convResp.detail) || 'onbekende fout';
    el.innerHTML = '<div class="empty-state">' +
      '<p style="font-size:14px;font-weight:600;color:var(--text-dim);margin-bottom:6px">WhatsApp-overzicht niet bereikbaar</p>' +
      '<p style="color:#94a3b8;font-size:12px;max-width:560px">' + escHtml(detail) + '</p>' +
      '<p style="color:#94a3b8;font-size:12px;margin-top:8px">Dit scherm proxy\'t naar Iris Remote (Vercel/Neon) via BRIDGE_TOKEN. ' +
      'Staat de bridge wel aan (/api/bridge/status) maar faalt dit toch, dan is de nieuwste remote-code ' +
      'vermoedelijk nog niet gedeployed.</p></div>';
    return;
  }

  var escalations = escResp.escalations || [];
  _waConversationsCache = convResp.conversations || [];

  el.innerHTML = renderWaStatsRow(statsResp) +
    renderWaEscalaties(escalations) +
    renderWaNieuweContacten(_waConversationsCache) +
    renderWaAlleGesprekken(_waConversationsCache) +
    '<div id="wa-thread-panel"></div>';
}

function renderWaStatsRow(s) {
  var escOpen = (s.escalations && s.escalations.open) || 0;
  var escAnswered = (s.escalations && s.escalations.answered_7d) || 0;
  var avgSec = s.escalations && s.escalations.avg_response_seconds;
  var avgLabel = avgSec ? Math.round(avgSec / 3600 * 10) / 10 + 'u gem. reactie' : 'nog geen reactietijd';
  return '<div class="kpi-row" style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px">' +
    waKpi('Berichten vandaag', s.messages_today) +
    waKpi('Gesprekken (7d)', s.active_conversations_7d, 'actieve klanten') +
    waKpi('Nieuw (7d)', s.new_contacts_7d, 'nieuwe contacten') +
    waKpi('Escalaties open', escOpen, (s.escalations && s.escalations.created_7d || 0) + ' nieuw/7d', escOpen > 0 ? 'danger' : '') +
    waKpi('Beantwoord (7d)', escAnswered, avgLabel) +
    '</div>';
}
function waKpi(label, value, sub, tone) {
  var borderColor = tone === 'danger' ? 'var(--red)' : 'var(--border)';
  return '<div class="kpi-card" style="border-left:3px solid ' + borderColor + '">' +
    '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8">' + escHtml(label) + '</div>' +
    '<div style="font-size:24px;font-weight:700;margin-top:2px">' + (value == null ? '-' : value) + '</div>' +
    (sub ? '<div style="font-size:11px;color:#94a3b8;margin-top:2px">' + escHtml(sub) + '</div>' : '') +
    '</div>';
}

function renderWaEscalaties(escalations) {
  var html = '<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 8px;font-size:14px">Wacht op jou' +
    (escalations.length ? ' (' + escalations.length + ')' : '') + '</h3>';
  if (!escalations.length) {
    html += '<p style="color:#94a3b8;font-size:12px">Niets wacht op jou.</p></div>';
    return html;
  }
  html += escalations.map(function (e) {
    return '<div style="border-top:1px solid var(--border);padding:10px 0">' +
      '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">' +
      '<div><strong>' + escHtml(e.project || 'onbekend project') + '</strong> — ' + escHtml(e.wa_id || '') +
      '<div style="font-size:12px;color:#64748b;margin-top:2px">' + escHtml(e.question || '') + '</div>' +
      '<div style="font-size:11px;color:#94a3b8;margin-top:2px">Reden: ' + escHtml(e.reason || 'onbekend') +
      ' &middot; ' + _leadRelTime(e.created_at) + '</div></div>' +
      '<div style="display:flex;gap:6px;align-items:flex-start">' +
      '<button class="btn btn-sm btn-primary" onclick="waOpenReply(\'' + escAttr(e.id) + '\', this)">Beantwoord</button>' +
      '<button class="btn btn-sm btn-ghost" onclick="waDismiss(\'' + escAttr(e.id) + '\', this)">Negeer</button>' +
      '</div></div>' +
      '<div class="wa-reply-box" id="wa-reply-' + escAttr(e.id) + '" style="display:none;margin-top:8px">' +
      '<textarea rows="2" style="width:100%;font-size:12px;padding:6px" placeholder="Typ je antwoord aan de klant..."></textarea>' +
      '<button class="btn btn-sm btn-primary" style="margin-top:4px" onclick="waSendReply(\'' + escAttr(e.id) + '\', this)">Verstuur</button>' +
      '</div></div>';
  }).join('') + '</div>';
  return html;
}

function renderWaNieuweContacten(conversations) {
  var nieuw = conversations.filter(function (c) { return c.is_new; });
  var html = '<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 8px;font-size:14px">Nieuwe contacten (7d)' +
    (nieuw.length ? ' (' + nieuw.length + ')' : '') + '</h3>';
  if (!nieuw.length) {
    html += '<p style="color:#94a3b8;font-size:12px">Geen nieuwe contacten deze week.</p></div>';
    return html;
  }
  html += nieuw.map(waConvRow).join('') + '</div>';
  return html;
}

function renderWaAlleGesprekken(conversations) {
  var html = '<div class="card"><h3 style="margin:0 0 8px;font-size:14px">Alle gesprekken (30d)' +
    (conversations.length ? ' (' + conversations.length + ')' : '') + '</h3>';
  if (!conversations.length) {
    html += '<p style="color:#94a3b8;font-size:12px">Nog geen WhatsApp-gesprekken.</p></div>';
    return html;
  }
  html += conversations.map(waConvRow).join('') + '</div>';
  return html;
}

// Eén rij per gesprek — kan in twee secties tegelijk voorkomen (nieuw én in de
// volledige lijst), dus het transcript-toggle zoekt zijn paneel via een uniek
// element-id per (sectie, wa_id) i.p.v. één globale id (zelfde valkuil als
// Iris Remote op 22 aug 2026 al vermeed, zie CLAUDE.md 14h).
var _waRowSeq = 0;
function waConvRow(c) {
  var rowId = 'wa-row-' + (_waRowSeq++);
  var initial = (c.contact_name || c.wa_id || '?').trim().charAt(0).toUpperCase();
  return '<div style="border-top:1px solid var(--border);padding:8px 0">' +
    '<div style="display:flex;align-items:center;gap:10px;cursor:pointer" onclick="waToggleThread(\'' + escAttr(c.wa_id) + '\', \'' + rowId + '\')">' +
    '<span style="width:26px;height:26px;border-radius:50%;background:var(--accent-bg,#eef2ff);color:var(--accent,#4f46e5);' +
    'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">' + escHtml(initial) + '</span>' +
    '<div style="flex:1;min-width:0">' +
    '<div style="font-size:13px;font-weight:600">' + escHtml(c.contact_name || c.wa_id) +
    (c.open_escalations ? ' <span class="pill pill-danger" style="font-size:10px">' + c.open_escalations + ' open</span>' : '') + '</div>' +
    '<div style="font-size:11px;color:#94a3b8">' + escHtml(c.project || 'onbekend project') + ' &middot; ' +
    (c.message_count || 0) + ' berichten &middot; laatste: ' + _leadRelTime(c.updated_at) + '</div></div>' +
    '</div><div id="' + rowId + '" style="display:none;margin-top:8px;padding-left:36px"></div></div>';
}

function waToggleThread(waId, rowId) {
  var panel = document.getElementById(rowId);
  if (!panel) return;
  if (panel.style.display !== 'none' && panel.dataset.loaded === waId) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  panel.dataset.loaded = waId;
  panel.innerHTML = '<div style="color:#94a3b8;font-size:12px">Transcript laden...</div>';
  fetch('/api/bridge/whatsapp-thread?wa_id=' + encodeURIComponent(waId))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.ok === false) { panel.innerHTML = '<div style="color:#ef4444;font-size:12px">' + escHtml(d.detail || 'fout') + '</div>'; return; }
      var msgs = (d.thread && d.thread.messages) || [];
      panel.innerHTML = '<div style="max-height:280px;overflow-y:auto;background:var(--bg-soft,#f8fafc);border-radius:8px;padding:8px">' +
        msgs.map(function (m) {
          var isUser = m.role === 'user';
          return '<div style="margin-bottom:6px;text-align:' + (isUser ? 'left' : 'right') + '">' +
            '<span style="display:inline-block;max-width:80%;padding:6px 10px;border-radius:10px;font-size:12px;' +
            'background:' + (isUser ? 'var(--card-bg,#fff)' : 'var(--accent,#4f46e5)') + ';color:' + (isUser ? 'inherit' : '#fff') + '">' +
            escHtml(m.content || '') + '</span></div>';
        }).join('') + '</div>';
    })
    .catch(function (e) { panel.innerHTML = '<div style="color:#ef4444;font-size:12px">Fout: ' + escHtml(e.message) + '</div>'; });
}

function waOpenReply(id, btn) {
  var box = document.getElementById('wa-reply-' + id);
  if (box) box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function waSendReply(id, btn) {
  var box = document.getElementById('wa-reply-' + id);
  var text = box.querySelector('textarea').value.trim();
  if (!text) return;
  btn.disabled = true; btn.textContent = 'Versturen...';
  fetch('/api/bridge/whatsapp-reply', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: id, text: text}),
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.ok === false || (d.error)) {
      btn.disabled = false; btn.textContent = 'Verstuur';
      alert('Versturen mislukt: ' + (d.detail || d.error || 'onbekende fout'));
      return;
    }
    switchView('WhatsApp');
  }).catch(function (e) {
    btn.disabled = false; btn.textContent = 'Verstuur';
    alert('Versturen mislukt: ' + e.message);
  });
}

function waDismiss(id, btn) {
  btn.disabled = true;
  fetch('/api/bridge/whatsapp-dismiss', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: id}),
  }).then(function (r) { return r.json(); }).then(function () {
    switchView('WhatsApp');
  }).catch(function (e) { btn.disabled = false; alert('Negeren mislukt: ' + e.message); });
}
