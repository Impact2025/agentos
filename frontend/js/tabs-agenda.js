// Agenda-tab — WeAreImpact-only. Toont de dag- en weekplanning uit de
// gekoppelde Google Agenda (chat@weareimpact.nl). Leest /api/calendar/*.
// Gescoped op WeAreImpact via visibleTabs() in core.js: voor andere projecten
// verschijnt deze tab niet in de zijbalk.

// Maandag van de week die de gegeven datum bevat (lokaal, CEST).
function agendaMondayOf(d) {
  var m = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  var dow = (m.getDay() + 6) % 7; // ma=0
  m.setDate(m.getDate() - dow);
  return m;
}
function agendaISODate(d) {
  var y = d.getFullYear();
  var mo = String(d.getMonth() + 1).padStart(2, '0');
  var da = String(d.getDate()).padStart(2, '0');
  return y + '-' + mo + '-' + da;
}
var AGENDA_DAYS = ['Ma', 'Di', 'Wo', 'Do', 'Vr', 'Za', 'Zo'];
var AGENDA_MONTHS = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];

function agendaFmtTime(iso) {
  if (!iso) return '';
  if (iso.indexOf('T') >= 0) return iso.slice(11, 16);
  return 'hele dag';
}
function agendaFmtDayHeader(d) {
  return AGENDA_DAYS[d.getDay() === 0 ? 6 : d.getDay() - 1] + ' ' + d.getDate() + ' ' +
    AGENDA_MONTHS[d.getMonth()];
}
// "vandaag / morgen / gisteren" i.p.v. een datum, als het om de huidige week gaat.
function agendaDayLabel(d, today) {
  var diff = Math.round((agendaMondayOf(d) - agendaMondayOf(today)) / 86400000) * 0 + 0;
  var dayMs = 86400000;
  var a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  var b = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  var delta = Math.round((a - b) / dayMs);
  if (delta === 0) return 'Vandaag';
  if (delta === 1) return 'Morgen';
  if (delta === -1) return 'Gisteren';
  return '';
}

async function renderAgendaTab(el) {
  // _agendaWeekStart wordt bijgehouden zodat de navigatie-knoppen dezelfde
  // week tonen die al geladen is (en niet terugspringt naar "nu").
  if (_agendaWeekStart === null) _agendaWeekStart = agendaMondayOf(new Date());
  var ws = _agendaWeekStart;
  el.innerHTML =
    '<div class="agenda-wrap">' +
      '<div class="agenda-head">' +
        '<div><h2>Agenda</h2>' +
        '<p class="muted">WeAreImpact — dag- en weekplanning uit Google Agenda.</p></div>' +
        '<div class="agenda-nav">' +
          '<button class="btn" onclick="agendaShiftWeek(-1)">‹ Vorige</button>' +
          '<button class="btn" onclick="agendaShiftWeek(0)">Deze week</button>' +
          '<button class="btn" onclick="agendaShiftWeek(1)">Volgende ›</button>' +
        '</div>' +
      '</div>' +
      '<div id="agenda-status"></div>' +
      '<div class="section-card" style="margin-bottom:16px">' +
        '<label style="font-size:12px;font-weight:600;color:var(--text-dim);display:block;margin-bottom:6px">Snel iets toevoegen — typ in gewone taal</label>' +
        '<div style="display:flex;gap:8px">' +
        '<input id="agenda-command-input" placeholder="Bijv. dinsdag 14 uur bij de tandarts" style="flex:1" onkeydown="if(event.key===\'Enter\')agendaSubmitCommand()">' +
        '<button class="btn btn-primary" onclick="agendaSubmitCommand()">Voorstel maken</button>' +
        '</div>' +
        '<p id="agenda-command-result" style="font-size:12px;margin-top:6px"></p>' +
      '</div>' +
      '<div id="agenda-proposals" class="agenda-section"></div>' +
      '<div id="agenda-today" class="agenda-section"></div>' +
      '<div id="agenda-week" class="agenda-section"><div class="section-card"><div class="spinner"></div></div></div>' +
    '</div>';
  agendaLoad(el, ws);
}

// -1 vorige / +1 volgende / 0 = deze week
function agendaShiftWeek(dir) {
  if (dir === 0) _agendaWeekStart = agendaMondayOf(new Date());
  else {
    var m = new Date(_agendaWeekStart.getFullYear(), _agendaWeekStart.getMonth(), _agendaWeekStart.getDate());
    m.setDate(m.getDate() + dir * 7);
    _agendaWeekStart = m;
  }
  var el = document.getElementById('tab-content');
  if (el) agendaLoad(el, _agendaWeekStart);
}

async function agendaLoad(el, weekStart) {
  var year = weekStart.getFullYear();
  var statusEl = el.querySelector('#agenda-status');
  var todayEl = el.querySelector('#agenda-today');
  var weekEl = el.querySelector('#agenda-week');

  agendaLoadProposals();

  // Kalender-status (bereikbaarheid + welke agenda gelezen wordt).
  if (statusEl) statusEl.innerHTML = '<div class="muted" style="padding:4px 0">Kalender controleren…</div>';
  var status = await agendaFetchStatus();
  if (statusEl) {
    if (!status) statusEl.innerHTML = '';
    else if (!status.configured) {
      statusEl.innerHTML = '<div class="empty-state">Google Agenda is niet geconfigureerd op deze installatie.</div>';
    } else if (!status.reachable) {
      statusEl.innerHTML = '<div class="empty-state" style="border-left:4px solid var(--red)">Agenda niet bereikbaar: ' +
        escHtml(status.error || 'onbekende fout') + '</div>';
    } else {
      statusEl.innerHTML = '<div class="muted" style="padding:4px 0">Gekoppelde agenda: <b>' +
        escHtml(status.calendar_id) + '</b></div>';
    }
  }

  // Vandaag-blok (belangrijkste afspraken van vandaag).
  if (todayEl) {
    todayEl.innerHTML = '<div class="section-card"><h3>Vandaag</h3><div id="agenda-today-body"><div class="spinner"></div></div></div>';
    var todayBody = todayEl.querySelector('#agenda-today-body');
    try {
      var tdata = await (await fetch('/api/calendar/today')).json();
      if (todayBody) {
        if (tdata && tdata.configured === false) {
          todayBody.innerHTML = '<div class="muted">Geen agenda gekoppeld.</div>';
        } else if (tdata && tdata.summary) {
          var lines = tdata.summary.split('\n').map(function(l) { return escHtml(l); });
          todayBody.innerHTML = lines.join('<br>');
        } else {
          todayBody.innerHTML = '<div class="muted">Geen afspraken vandaag.</div>';
        }
      }
    } catch (e) {
      if (todayBody) todayBody.innerHTML = '<div class="muted">Kon vandaag-overzicht niet laden: ' + escHtml(String(e)) + '</div>';
    }
  }

  // Week-overzicht.
  if (weekEl) weekEl.innerHTML = '<div class="section-card"><div class="spinner"></div></div>';
  try {
    var wsIso = agendaISODate(weekStart);
    var data = await (await fetch('/api/calendar/events?week_start=' + encodeURIComponent(wsIso))).json();
    var events = (data && data.events) || [];
    if (weekEl) weekEl.innerHTML = agendaRenderWeek(events, weekStart);
  } catch (e) {
    if (weekEl) weekEl.innerHTML = '<div class="empty-state">Kon weekoverzicht niet laden: ' + escHtml(String(e)) + '</div>';
  }
}

async function agendaFetchStatus() {
  try {
    var d = await (await fetch('/api/calendar/status')).json();
    return d;
  } catch (e) { return null; }
}

// Deelnemer-context per info-knop, gevuld bij het renderen en gelezen door
// agendaShowAttendeeInfo(). Een dataset-attribuut met de hele naam/e-mail zou
// ook kunnen, maar dan moet elke aanhalingsteken in een naam ontsnapt worden
// in een inline onclick — een lookup-tabel is simpelweg veiliger.
var _agendaAttendeeStore = {};

function agendaRenderWeek(events, weekStart) {
  _agendaAttendeeStore = {};
  var today = new Date();
  var grouped = {};
  events.forEach(function(ev) {
    if (!ev.start) return;
    var dayKey = ev.start.slice(0, 10);
    (grouped[dayKey] = grouped[dayKey] || []).push(ev);
  });
  var evCounter = 0;
  // 7 dagen vanaf maandag.
  var rows = [];
  for (var i = 0; i < 7; i++) {
    var d = new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i);
    var key = agendaISODate(d);
    var dayEvents = grouped[key] || [];
    dayEvents.sort(function(a, b) { return (a.start || '').localeCompare(b.start || ''); });
    var isToday = agendaISODate(d) === agendaISODate(today);
    var itemsHtml = dayEvents.length
      ? dayEvents.map(function(ev) {
          var time = agendaFmtTime(ev.start);
          var loc = ev.location ? ' <span class="muted">· ' + escHtml(ev.location) + '</span>' : '';
          var link = ev.html_link
            ? ' <a href="' + escHtml(ev.html_link) + '" target="_blank" class="agenda-ext" title="Open in Google Agenda">↗</a>'
            : '';
          var attendees = (ev.attendees || []).filter(function(a) { return a && a.name && a.name !== '?'; });
          var attendeesHtml = '';
          if (attendees.length) {
            evCounter++;
            attendeesHtml = '<div class="agenda-attendees">Met: ' +
              attendees.map(function(a, ai) {
                var storeKey = 'ev' + evCounter + '_' + ai;
                _agendaAttendeeStore[storeKey] = {
                  name: a.name, email: a.email || '', eventTitle: ev.summary || '',
                };
                return '<span class="agenda-attendee">' + escHtml(a.name) +
                  ' <button type="button" class="agenda-info-btn" title="Vertel me meer over ' + escHtml(a.name) + '" ' +
                  'onclick="agendaShowAttendeeInfo(\'' + storeKey + '\', this)">ⓘ</button></span>';
              }).join(', ') + '</div>';
          }
          return '<div class="agenda-ev' + (ev.all_day ? ' all-day' : '') + '">' +
            '<span class="agenda-time">' + escHtml(time) + '</span>' +
            '<span class="agenda-sum">' + escHtml(ev.summary || '(geen titel)') + loc + link + attendeesHtml + '</span>' +
            '</div>';
        }).join('')
      : '<div class="agenda-ev empty"><span class="agenda-time"></span><span class="agenda-sum muted">— vrij —</span></div>';
    var label = agendaDayLabel(d, today);
    var nameHtml = label
      ? '<span class="agenda-day-name">' + escHtml(label) + '</span>'
      : '<span class="agenda-day-name">' + escHtml(agendaFmtDayHeader(d)) + '</span>';
    var dateHtml = label ? '<span class="agenda-day-date">' + escHtml(agendaFmtDayHeader(d)) + '</span>' : '';
    rows.push(
      '<div class="agenda-day' + (isToday ? ' is-today' : '') + '">' +
        '<div class="agenda-day-head">' + nameHtml + dateHtml + '</div>' +
        '<div class="agenda-day-body">' + itemsHtml + '</div>' +
      '</div>'
    );
  }
  return '<div class="section-card"><h3>Deze week</h3><div class="agenda-week-grid">' + rows.join('') + '</div></div>';
}

// ── Info-knop: "wie is dit" (websearch + LLM via Iris, /api/calendar/attendee-info) ──
function agendaShowModal(title, bodyHtml) {
  var overlay = document.getElementById('agenda-modal-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'agenda-modal-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;z-index:9999;padding:16px';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }
  overlay.innerHTML =
    '<div style="background:var(--card-bg,#fff);color:var(--text,#1e293b);border-radius:12px;max-width:440px;width:100%;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.25)">' +
      '<h3 style="margin:0 0 12px;font-size:15px">' + escHtml(title) + '</h3>' +
      '<div id="agenda-modal-body" style="font-size:13px;line-height:1.6;white-space:pre-wrap">' + bodyHtml + '</div>' +
      '<div style="margin-top:16px;text-align:right"><button onclick="document.getElementById(\'agenda-modal-overlay\').remove()" class="btn btn-primary">Sluiten</button></div>' +
    '</div>';
}

async function agendaShowAttendeeInfo(storeKey, btn) {
  var info = _agendaAttendeeStore[storeKey];
  if (!info) return;
  if (btn) btn.disabled = true;
  agendaShowModal(info.name, '<div class="spinner"></div><div class="muted" style="margin-top:8px">Iris zoekt het op…</div>');
  try {
    var r = await fetch('/api/calendar/attendee-info', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: info.name, email: info.email, event_title: info.eventTitle,
      }),
    });
    var data = await r.json();
    var body = document.getElementById('agenda-modal-body');
    if (!body) return;
    if (!r.ok) {
      body.innerHTML = '<div style="color:var(--red,#dc2626)">Kon geen info ophalen: ' + escHtml(data.detail || ('HTTP ' + r.status)) + '</div>';
      return;
    }
    var cachedNote = data.cached ? '<div class="muted" style="font-size:11px;margin-top:8px">Eerder opgezocht.</div>' : '';
    body.innerHTML = escHtml(data.summary || 'Geen informatie gevonden.') + cachedNote;
  } catch (e) {
    var body2 = document.getElementById('agenda-modal-body');
    if (body2) body2.innerHTML = '<div style="color:var(--red,#dc2626)">Kon geen info ophalen: ' + escHtml(String(e)) + '</div>';
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Snel-toevoegen (vrije tekst -> voorstel) + wacht-op-goedkeuring ──────

function agendaSubmitCommand() {
  var input = document.getElementById('agenda-command-input');
  var resultEl = document.getElementById('agenda-command-result');
  var text = input ? input.value.trim() : '';
  if (!text) return;
  if (resultEl) { resultEl.style.color = '#64748b'; resultEl.textContent = 'Bezig...'; }
  post('/api/calendar/command', { text: text }).then(function (res) {
    if (resultEl) { resultEl.style.color = 'var(--ok-fg)'; resultEl.textContent = res.message; }
    if (input) input.value = '';
    agendaLoadProposals();
  }).catch(function (e) {
    if (resultEl) { resultEl.style.color = 'var(--danger-fg)'; resultEl.textContent = e.message; }
  });
}

function agendaLoadProposals() {
  var el = document.getElementById('agenda-proposals');
  if (!el) return;
  fetch('/api/calendar/proposals').then(function (r) { return r.json(); }).then(function (data) {
    var proposals = data.proposals || [];
    if (!proposals.length) { el.innerHTML = ''; return; }
    el.innerHTML = '<div class="section-card" style="margin-bottom:16px;border-left:4px solid var(--accent)">' +
      '<h3 style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:8px">Wacht op jouw goedkeuring (' + proposals.length + ')</h3>' +
      proposals.map(agendaProposalCard).join('') +
      '</div>';
  }).catch(function () { el.innerHTML = ''; });
}

function agendaProposalCard(p) {
  var start = p.proposed_start ? new Date(p.proposed_start) : null;
  var when = start ? start.toLocaleString('nl-NL', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '?';
  var reistijd = p.travel_buffer_min ? '<span style="font-size:10px;color:#94a3b8;margin-left:6px">reistijd ' + p.travel_buffer_min + ' min (enkele reis)</span>' : '';
  var conflict = p.conflict_note ? '<p style="margin:4px 0 0;font-size:11px;color:#c2410c">' + escHtml(p.conflict_note) + '</p>' : '';
  return '<div style="border-top:1px solid #f1f5f9;padding:10px 0">' +
    '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">' +
    '<div><p style="margin:0;font-size:13px;font-weight:600;color:var(--text)">' + escHtml(p.title) + '</p>' +
    '<p style="margin:2px 0 0;font-size:12px;color:var(--text-dim)">' + escHtml(when) + ' · ' + escHtml(p.location || 'geen locatie') + reistijd + '</p></div>' +
    '<div style="display:flex;gap:6px">' +
    '<button class="btn btn-sm btn-primary" onclick="agendaApproveProposal(' + p.id + ')">Goedkeuren</button>' +
    '<button class="btn btn-sm btn-ghost" onclick="agendaRejectProposal(' + p.id + ')">Afwijzen</button>' +
    '</div></div>' + conflict +
    '</div>';
}

function agendaApproveProposal(id) {
  post('/api/calendar/proposals/approve', { proposal_id: id }).then(function (res) {
    if (!res.ok) { alert(res.error || 'Kon niet boeken.'); return; }
    agendaLoadProposals();
    var el = document.getElementById('tab-content');
    if (el) agendaLoad(el, _agendaWeekStart);
  }).catch(function (e) { alert(e.message); });
}

function agendaRejectProposal(id) {
  post('/api/calendar/proposals/reject', { proposal_id: id }).then(function () {
    agendaLoadProposals();
  }).catch(function (e) { alert(e.message); });
}
