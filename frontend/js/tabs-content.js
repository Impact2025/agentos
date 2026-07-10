
// ═══════════════════════════════════════════════════════════════════
//  HELPDESK TAB — per-project mail-helpdesk met review-gate
//  Concept-antwoorden van inkomende supportmail, klaar voor goedkeuring.
// ═══════════════════════════════════════════════════════════════════

async function renderHelpdeskTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Helpdesk laden...</p></div>';
  // Haal in één slag de mailboxes + open concepten (pending_review + edited)
  var data;
  try {
    var [mbResp, pendResp] = await Promise.all([
      fetch('/api/mail/mailboxes').then(function(r){return r.json();}),
      fetch('/api/mail/pending').then(function(r){return r.json();}),
    ]);
    data = { mailboxes: mbResp.mailboxes || [], pending: pendResp.replies || [] };
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
    return;
  }

  var mailboxes = data.mailboxes || [];
  var pending = data.pending || [];

  // Groepeer concepten per mailbox-id
  var byMb = {};
  pending.forEach(function(r){ (byMb[r.mailbox_id] = byMb[r.mailbox_id] || []).push(r); });

  var html = '';

  // ── Header + acties ──
  html += '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px">' +
    '<div><h2 style="font-size:18px;font-weight:700;margin:0">Mail helpdesk</h2>' +
    '<p style="font-size:12px;color:#64748b;margin:2px 0 0">' +
    (mailboxes.length ? mailboxes.length + ' mailbox' + (mailboxes.length>1?'en':'') + ' actief' : 'Nog geen mailbox') +
    ' · ' + pending.length + ' concept' + (pending.length===1?'':'en') + ' wacht op goedkeuring</p></div>' +
    '<div style="display:flex;gap:6px">' +
    '<button onclick="helpdeskRunAll(this)" style="padding:7px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">↻ Nu ophalen</button>' +
    '</div></div>';

  if (!mailboxes.length) {
    html += '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:6px">Nog geen mailbox ingesteld</p>' +
      '<p style="color:#94a3b8;font-size:12px;margin-bottom:10px">Voeg een mailbox toe om supportmail per project automatisch te verwerken.</p>' +
      '<button onclick="helpdeskShowAddForm()" style="padding:7px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Mailbox toevoegen</button></div>';
    el.innerHTML = html + '<div id="helpdesk-add-form"></div>';
    return;
  }

  // ── Per mailbox: concepten ──
  mailboxes.forEach(function(mb) {
    var replies = byMb[mb.id] || [];
    var statusCls = mb.enabled ? '#16a34a' : '#94a3b8';
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<div style="display:flex;align-items:center;gap:8px"><span style="width:9px;height:9px;border-radius:50%;background:' + statusCls + '"></span>' +
      '<h3 style="margin:0;font-size:14px;font-weight:700">' + escHtml(mb.address) + '</h3>' +
      '<span style="font-size:10px;color:#94a3b8;background:#f1f5f9;padding:1px 7px;border-radius:10px">' + escHtml(mb.project) + '</span></div>' +
      '<button onclick="helpdeskRunOne(\'' + escHtml(mb.id) + '\',this)" style="padding:4px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">↻ Ophalen</button>' +
      '</div>';

    if (!replies.length) {
      html += '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:14px">Geen open concepten — inbox is bijgewerkt.</p>';
    } else {
      replies.forEach(function(r) {
        var isEdited = r.status === 'edited';
        html += '<div class="helpdesk-item" id="hd-item-' + r.id + '" style="border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff">' +
          '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
          '<span style="font-size:11px;font-weight:600;color:#059669;background:#ecfdf5;padding:2px 8px;border-radius:10px">' + (isEdited ? 'bewerkt' : 'concept') + '</span>' +
          '<span style="font-size:13px;font-weight:600;color:#1e293b">Van: ' + escHtml(r.to_addr) + '</span></div>' +
          '<div style="font-size:12px;color:#475569;margin-bottom:6px"><strong>Onderwerp:</strong> ' + escHtml(r.subject) + '</div>' +
          '<textarea id="hd-body-' + r.id + '" style="width:100%;min-height:120px;font-size:12px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:' + (isEdited ? '#fffbeb' : '#f8fafc') + '">' + escHtml(r.draft_body) + '</textarea>' +
          '<div style="display:flex;gap:6px;margin-top:8px">' +
          '<button onclick="helpdeskSend(' + r.id + ',this)" style="padding:6px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">✉ Verstuur</button>' +
          '<button onclick="helpdeskSave(' + r.id + ',this)" style="padding:6px 14px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;cursor:pointer">Opslaan</button>' +
          '<button onclick="helpdeskReject(' + r.id + ',this)" style="padding:6px 14px;background:#fff;color:#ef4444;border:1px solid #fecaca;border-radius:6px;font-size:12px;cursor:pointer">Afwijzen</button>' +
          '</div></div>';
      });
    }
    html += '</div>';
  });

  el.innerHTML = html + '<div id="helpdesk-add-form"></div>';
  // badge verversen
  var badge = document.getElementById('helpdesk-badge');
  if (badge) { if (pending.length>0) { badge.style.display='inline-block'; badge.textContent=pending.length; } else { badge.style.display='none'; } }
}

// ── Acties ──
async function helpdeskSend(id, btn) {
  if (!confirm('Concept versturen naar de klant?')) return;
  btn.disabled = true; btn.textContent = 'Versturen...';
  try {
    var resp = await fetch('/api/mail/replies/' + id + '/send', { method:'POST' });
    var d = await resp.json();
    if (d.ok) {
      var item = document.getElementById('hd-item-' + id);
      if (item) item.remove();
      pollHelpdeskBadge();
      alert('✅ Verstuurd naar de klant.');
    } else {
      alert('❌ Kon niet versturen: ' + (d.error || 'onbekend'));
    }
  } catch(e) {
    alert('❌ Fout: ' + (e.message || e) + '\n\nDe server was tijdelijk niet bereikbaar. Probeer het opnieuw.');
  } finally {
    btn.disabled = false; btn.textContent = '✉ Verstuur';
  }
}

async function helpdeskSave(id, btn) {
  var ta = document.getElementById('hd-body-' + id);
  if (!ta) return;
  var body = ta.value;
  btn.disabled = true; btn.textContent = 'Opslaan...';
  try {
    var resp = await fetch('/api/mail/replies/' + id + '/edit', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ draft_body: body }),
    });
    var d = await resp.json();
    if (d.ok) { ta.style.background = '#fffbeb'; btn.textContent = 'Opgeslagen ✓'; setTimeout(function(){ btn.textContent='Opslaan'; }, 1500); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.textContent = 'Opslaan'; }
  } catch(e) { alert('❌ ' + e.message); btn.textContent = 'Opslaan'; }
  finally { btn.disabled = false; }
}

async function helpdeskReject(id, btn) {
  if (!confirm('Concept afwijzen? Het wordt gemarkeerd als afgewezen en niet verstuurd.')) return;
  btn.disabled = true;
  try {
    var resp = await fetch('/api/mail/replies/' + id + '/reject', { method:'POST' });
    var d = await resp.json();
    if (d.ok) { var item = document.getElementById('hd-item-' + id); if (item) item.remove(); pollHelpdeskBadge(); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

async function helpdeskRunAll(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Ophalen...';
  try {
    var resp = await fetch('/api/mail/run', { method:'POST' });
    var d = await resp.json();
    renderHelpdeskTab(document.getElementById('tab-content'));
    var msg = Object.keys(d || {}).map(function(k){ return k + ': ' + (d[k]||0) + ' concepten'; }).join('\n');
    if (msg) console.log('Helpdesk opgehaald:\n' + msg);
  } catch(e) { alert('❌ ' + e.message); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

async function helpdeskRunOne(mailboxId, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = '...';
  try {
    var resp = await fetch('/api/mail/run', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ mailbox_id: mailboxId }) });
    await resp.json();
    renderHelpdeskTab(document.getElementById('tab-content'));
  } catch(e) { alert('❌ ' + e.message); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

function helpdeskShowAddForm() {
  var box = document.getElementById('helpdesk-add-form');
  if (!box) return;
  box.innerHTML = '<div class="section-card" style="margin-top:16px">' +
    '<h3 style="margin-bottom:10px">Nieuwe mailbox toevoegen</h3>' +
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px">' +
    field('hd-project','Project','Skillkaart') +
    field('hd-address','E-mailadres','help@project.nl') +
    field('hd-pop-host','POP-host','mail.project.nl') +
    field('hd-pop-port','POP-poort','110') +
    field('hd-pop-user','POP-gebruiker','help@project.nl') +
    field('hd-pop-pass','POP-wachtwoord','', true) +
    field('hd-smtp-host','SMTP-host','mail.project.nl') +
    field('hd-smtp-port','SMTP-poort','587') +
    field('hd-smtp-user','SMTP-gebruiker','help@project.nl') +
    field('hd-smtp-pass','SMTP-wachtwoord','', true) +
    field('hd-display','Weergavenaam','Project Hulp') +
    '</div>' +
    '<div style="margin-top:10px"><button onclick="helpdeskAdd(this)" style="padding:7px 18px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Aanmaken</button></div></div>';
}
function field(id, label, ph, pwd) {
  return '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px">' + label +
    '<input id="' + id + '" type="' + (pwd?'password':'text') + '" placeholder="' + escHtml(ph) + '" style="padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px"></label>';
}
async function helpdeskAdd(btn) {
  var v = function(id){ var e = document.getElementById(id); return e ? e.value.trim() : ''; };
  var payload = {
    project: v('hd-project'), address: v('hd-address'),
    pop_host: v('hd-pop-host'), pop_port: parseInt(v('hd-pop-port')||'110',10), pop_user: v('hd-pop-user'), pop_password: v('hd-pop-pass'),
    smtp_host: v('hd-smtp-host'), smtp_port: parseInt(v('hd-smtp-port')||'587',10), smtp_user: v('hd-smtp-user'), smtp_password: v('hd-smtp-pass'),
    from_display: v('hd-display'), enabled: 1, poll_minutes: 30,
  };
  if (!payload.project || !payload.address || !payload.pop_host) { alert('Vul minimaal project, adres en POP-host in.'); return; }
  btn.disabled = true; btn.textContent = 'Aanmaken...';
  try {
    var resp = await fetch('/api/mail/mailboxes', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    var d = await resp.json();
    if (d.ok || d.id) { document.getElementById('helpdesk-add-form').innerHTML=''; renderHelpdeskTab(document.getElementById('tab-content')); }
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.textContent='Aanmaken'; }
  } catch(e) { alert('❌ ' + e.message); btn.textContent = 'Aanmaken'; }
  finally { btn.disabled = false; }
}
