// ── Impact OS — De Sparringpartner: persoonlijke business- en welzijnscoach ──
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Render-doel: <div id="coach-panel"> in home.js (Control Room, buiten
// projectcontext — de coach gaat over Vincent zelf, niet over één project).

var TECHNIQUE_ICON = {
  grow: '🎯', mi: '💬', oplossingsgericht: '📈', cgt: '🔍',
  act: '🧭', systemisch: '🕸️', strengths: '⭐',
};

function loadCoachPanel() {
  var el = document.getElementById('coach-panel');
  if (!el) return;
  fetch('/api/coach/lessons').then(function (r) { return r.json(); }).then(function (data) {
    renderCoachPanel(el, data.lessons || [], null);
  }).catch(function (e) {
    el.innerHTML = '<div style="color:var(--danger-fg)">Coach laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

function renderCoachPanel(el, lessons, result) {
  var html = '';

  if (result) {
    if (result.ok) {
      html += '<div style="background:#f8fafc;border-radius:8px;padding:12px;margin-bottom:12px">' +
        '<span style="font-size:11px;font-weight:600;color:var(--accent);background:rgba(99,102,241,.1);border-radius:999px;padding:2px 8px">' +
        (TECHNIQUE_ICON[result.technique] || '') + ' ' + escHtml(result.technique_label) + '</span>' +
        '<p style="margin:8px 0 0;font-size:13px;line-height:1.5;color:var(--text);white-space:pre-line">' + escHtml(result.analysis) + '</p>' +
        '<p style="margin:8px 0 0;font-size:11px;color:#94a3b8;border-top:1px solid #f1f5f9;padding-top:6px">' + escHtml(result.reason) + '</p>' +
        '</div>';
    } else {
      html += '<div style="background:#fff7ed;border-radius:8px;padding:12px;margin-bottom:12px;color:#c2410c;font-size:12px">' +
        escHtml(result.error) + '</div>';
    }
  } else {
    html += '<p style="color:#64748b;margin:0 0 12px">Vraag een reflectie op basis van je ritueel van vandaag, je energie-geschiedenis en de stand van de holding.</p>';
  }

  if (lessons.length) {
    html += '<div style="margin-top:4px"><h5 style="font-size:11px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Wat de coach al over je weet</h5>';
    html += lessons.map(function (l) {
      return '<div style="border-top:1px solid #f1f5f9;padding:6px 0">' +
        '<p style="margin:0;font-size:12px;color:var(--text)">' + escHtml(l.insight) + '</p>' +
        '<p style="margin:2px 0 0;font-size:10px;color:#94a3b8">' + escHtml(l.technique_label) + ' · ' +
        Math.round(l.confidence * 100) + '% trefkans, ' + l.times_confirmed + 'x gezien</p></div>';
    }).join('');
    html += '</div>';
  }

  el.innerHTML = html;
}

function askCoachReflection() {
  var btn = document.getElementById('coach-ask-btn');
  var el = document.getElementById('coach-panel');
  if (btn) { btn.disabled = true; btn.textContent = 'Reflecteert...'; }
  post('/api/coach/analyse', {}).then(function (result) {
    fetch('/api/coach/lessons').then(function (r) { return r.json(); }).then(function (data) {
      renderCoachPanel(el, data.lessons || [], result);
    });
  }).catch(function (e) {
    renderCoachPanel(el, [], { ok: false, error: e.message });
  }).finally(function () {
    if (btn) { btn.disabled = false; btn.textContent = 'Vraag reflectie'; }
  });
}
