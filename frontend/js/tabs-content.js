// ═══════════════════════════════════════════════════════════════════
//  HELPDESK TAB — per-project mail-helpdesk met review-gate
//  Concept-antwoorden van inkomende supportmail, klaar voor goedkeuring.
// ═══════════════════════════════════════════════════════════════════

async function renderHelpdeskTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Helpdesk laden...</p></div>';
  // Elk project zijn eigen helpdesk: alleen de mailboxen + concepten van dít project.
  var projQ = currentProject ? '?project=' + encodeURIComponent(currentProject) : '';
  var data;
  try {
    var [mbResp, pendResp] = await Promise.all([
      fetch('/api/mail/mailboxes' + projQ).then(function(r){return r.json();}),
      fetch('/api/mail/pending' + projQ).then(function(r){return r.json();}),
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
    '<div><h2 style="font-size:18px;font-weight:700;margin:0">Mail helpdesk' + (currentProject ? ' — ' + escHtml(currentProject) : '') + '</h2>' +
    '<p style="font-size:12px;color:#64748b;margin:2px 0 0">' +
    (mailboxes.length ? mailboxes.length + ' mailbox' + (mailboxes.length>1?'en':'') + ' voor dit project' : 'Nog geen mailbox voor dit project') +
    ' · ' + pending.length + ' concept' + (pending.length===1?'':'en') + ' wacht op goedkeuring</p></div>' +
    '<div style="display:flex;gap:6px">' +
    (mailboxes.length ? '<button onclick="helpdeskShowAddForm()" style="padding:7px 14px;background:#fff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Mailbox</button>' : '') +
    '<button onclick="helpdeskRunAll(this)" style="padding:7px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">↻ Nu ophalen</button>' +
    '</div></div>';

  if (!mailboxes.length) {
    html += '<div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:6px">' + escHtml(currentProject || 'Dit project') + ' heeft nog geen eigen helpdesk</p>' +
      '<p style="color:#94a3b8;font-size:12px;margin-bottom:10px">Voeg een mailbox toe — de helpdesk leest dan automatisch de projectkennis (vault, merkprofiel, live pagina\'s) mee bij elk antwoord.</p>' +
      '<button onclick="helpdeskShowAddForm()" style="padding:7px 16px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Mailbox toevoegen</button></div>';
    el.innerHTML = html + '<div id="helpdesk-add-form"></div>';
    return;
  }

  // ── Per mailbox: concepten ──
  mailboxes.forEach(function(mb) {
    var replies = byMb[mb.id] || [];
    var statusCls = mb.enabled ? '#16a34a' : '#94a3b8';
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:6px">' +
      '<div style="display:flex;align-items:center;gap:8px"><span style="width:9px;height:9px;border-radius:50%;background:' + statusCls + '"></span>' +
      '<h3 style="margin:0;font-size:14px;font-weight:700">' + escHtml(mb.address) + '</h3>' +
      '<span style="font-size:10px;color:#94a3b8;background:#f1f5f9;padding:1px 7px;border-radius:10px">' + escHtml(mb.project) + '</span></div>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="helpdeskRunOne(\'' + escHtml(mb.id) + '\',this)" style="padding:4px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">↻ Ophalen</button>' +
      '<button onclick="helpdeskToggle(\'' + escHtml(mb.id) + '\',' + (mb.enabled ? 0 : 1) + ',this)" style="padding:4px 12px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">' + (mb.enabled ? 'Pauzeer' : 'Activeer') + '</button>' +
      '<button onclick="helpdeskDeleteMailbox(\'' + escHtml(mb.id) + '\',\'' + escHtml(mb.address) + '\',this)" style="padding:4px 10px;background:#fff;color:#ef4444;border:1px solid #fecaca;border-radius:6px;font-size:11px;cursor:pointer">Verwijder</button>' +
      '</div></div>' +
      // Kennis-paneel: maakt zichtbaar wat de helpdesk over dit project weet
      '<details style="margin-bottom:8px" ontoggle="if(this.open)helpdeskLoadKnowledge(\'' + escHtml(mb.id) + '\')">' +
      '<summary style="font-size:11px;font-weight:600;color:#64748b;cursor:pointer">🧠 Wat weet de helpdesk?</summary>' +
      '<div id="hd-know-' + escHtml(mb.id) + '" style="font-size:11px;color:#475569;padding:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;margin-top:4px">Laden...</div></details>' +
      // Handtekening per project — WYSIWYG onder elk concept
      '<details style="margin-bottom:8px">' +
      '<summary style="font-size:11px;font-weight:600;color:#64748b;cursor:pointer">✍ Handtekening' + (mb.signature ? '' : ' <span style="color:#d97706">(nog niet ingesteld)</span>') + '</summary>' +
      '<div style="margin-top:4px"><textarea id="hd-sig-' + escHtml(mb.id) + '" placeholder="Bijv.:\nHartelijke groet,\nVincent van Munster\n' + escHtml(mb.project) + ' · hello@' + escHtml(mb.project) + '.nl" style="width:100%;min-height:80px;font-size:12px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:#f8fafc">' + escHtml(mb.signature || '') + '</textarea>' +
      '<button onclick="helpdeskSaveSignature(\'' + escHtml(mb.id) + '\',this)" style="margin-top:4px;padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Handtekening opslaan</button>' +
      '<span style="font-size:10px;color:#94a3b8;margin-left:8px">Komt automatisch onder elk nieuw concept; de AI ondertekent dan niet meer zelf.</span></div></details>';

    if (!replies.length) {
      html += '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:14px">Geen open concepten — inbox is bijgewerkt.</p>';
    } else {
      // ── Bulk-actiebalk (multi-select) ──
      html += '<div id="hd-bulkbar-' + escHtml(mb.id) + '" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:8px 10px;margin-bottom:10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">' +
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#475569;cursor:pointer">' +
        '<input type="checkbox" onclick="helpdeskToggleAll(\'' + escHtml(mb.id) + '\',this.checked)" style="cursor:pointer"> Alles selecteren</label>' +
        '<span id="hd-selcount-' + escHtml(mb.id) + '" style="font-size:12px;color:#64748b">0 geselecteerd</span>' +
        '<button onclick="helpdeskRejectSelected(\'' + escHtml(mb.id) + '\',this)" style="margin-left:auto;padding:5px 14px;background:#fff;color:#ef4444;border:1px solid #fecaca;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Geselecteerde afwijzen</button>' +
        '<button onclick="helpdeskDeleteSelected(\'' + escHtml(mb.id) + '\',this)" style="padding:5px 14px;background:#ef4444;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Definitief verwijderen</button>' +
        '</div>';

      replies.forEach(function(r) {
        var isEdited = r.status === 'edited';
        var question = (r.question_body || '').trim();
        html += '<div class="helpdesk-item" id="hd-item-' + r.id + '" data-mb="' + escHtml(mb.id) + '" style="border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff">' +
          '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
          '<input type="checkbox" class="hd-check-' + escHtml(mb.id) + '" data-id="' + r.id + '" onclick="helpdeskUpdateSelCount(\'' + escHtml(mb.id) + '\')" style="cursor:pointer;width:15px;height:15px">' +
          '<span style="font-size:11px;font-weight:600;color:#059669;background:#ecfdf5;padding:2px 8px;border-radius:10px">' + (isEdited ? 'bewerkt' : 'concept') + '</span>' +
          '<span style="font-size:13px;font-weight:600;color:#1e293b">Van: ' + escHtml(r.from_name || r.from_addr || r.to_addr) + ' &lt;' + escHtml(r.to_addr) + '&gt;</span></div>' +
          '<div style="font-size:12px;color:#475569;margin-bottom:6px"><strong>Onderwerp:</strong> ' + escHtml(r.subject) + '</div>' +
          (question ? '<details style="margin-bottom:8px"><summary style="font-size:11px;font-weight:600;color:#64748b;cursor:pointer">Vraag van de klant</summary>' +
            '<div style="font-size:12px;color:#475569;white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px;margin-top:4px;max-height:160px;overflow-y:auto">' + escHtml(question) + '</div></details>' : '') +
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

  // ── Social Inbox sectie (LinkedIn/IG/FB/TikTok) ──
  renderSocialInboxSection(el);
}

// ── Acties ──
async function helpdeskSend(id, btn) {
  if (!confirm('Concept versturen naar de klant?')) return;
  btn.disabled = true; btn.textContent = 'Versturen...';
  try {
    var resp = await fetch('/api/mail/reply/' + id + '/send', { method:'POST' });
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
    var resp = await fetch('/api/mail/reply/' + id + '/edit', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ text: body }),
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
    var resp = await fetch('/api/mail/reply/' + id + '/reject', { method:'POST' });
    var d = await resp.json();
    if (d.ok) { var item = document.getElementById('hd-item-' + id); if (item) item.remove(); pollHelpdeskBadge(); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

// ── Multi-select bulk-afwijzen ──
function helpdeskToggleAll(mbId, checked) {
  document.querySelectorAll('.hd-check-' + CSS.escape(mbId)).forEach(function(c){ c.checked = checked; });
  helpdeskUpdateSelCount(mbId);
}

function helpdeskUpdateSelCount(mbId) {
  var checks = document.querySelectorAll('.hd-check-' + CSS.escape(mbId));
  var n = 0; checks.forEach(function(c){ if (c.checked) n++; });
  var lbl = document.getElementById('hd-selcount-' + mbId);
  if (lbl) lbl.textContent = n + ' geselecteerd';
}

async function helpdeskRejectSelected(mbId, btn) {
  var checks = document.querySelectorAll('.hd-check-' + CSS.escape(mbId));
  var ids = [];
  checks.forEach(function(c){ if (c.checked) ids.push(parseInt(c.getAttribute('data-id'), 10)); });
  if (!ids.length) { alert('Geen concepten geselecteerd.'); return; }
  if (!confirm(ids.length + ' concept' + (ids.length===1?'':'en') + ' afwijzen? Ze worden gemarkeerd als afgewezen en niet verstuurd.')) return;
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Afwijzen...';
  try {
    var resp = await fetch('/api/mail/replies/reject-bulk', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ ids: ids }),
    });
    var d = await resp.json();
    if (d.ok) {
      ids.forEach(function(id){ var it = document.getElementById('hd-item-' + id); if (it) it.remove(); });
      helpdeskUpdateSelCount(mbId);
      pollHelpdeskBadge();
    } else {
      alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; btn.textContent = orig;
    }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; btn.textContent = orig; }
}

async function helpdeskDeleteSelected(mbId, btn) {
  var checks = document.querySelectorAll('.hd-check-' + CSS.escape(mbId));
  var ids = [];
  checks.forEach(function(c){ if (c.checked) ids.push(parseInt(c.getAttribute('data-id'), 10)); });
  if (!ids.length) { alert('Geen concepten geselecteerd.'); return; }
  if (!confirm(ids.length + ' bericht' + (ids.length===1?'':'en') + ' DEFINITIEF verwijderen?\n\nHet concept én het bericht verdwijnen uit Agent OS en komen niet meer terug bij het ophalen. De mail op de server zelf blijft staan.')) return;
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Verwijderen...';
  try {
    var resp = await fetch('/api/mail/replies/delete-bulk', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ ids: ids }),
    });
    var d = await resp.json();
    if (d.ok) {
      ids.forEach(function(id){ var it = document.getElementById('hd-item-' + id); if (it) it.remove(); });
      helpdeskUpdateSelCount(mbId);
      pollHelpdeskBadge();
    } else {
      alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; btn.textContent = orig;
    }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; btn.textContent = orig; }
}

async function helpdeskRunAll(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Ophalen...';
  try {
    // Alleen de mailboxen van dít project pollen — elk project zijn eigen helpdesk.
    var projQ = currentProject ? '?project=' + encodeURIComponent(currentProject) : '';
    var mbs = (await (await fetch('/api/mail/mailboxes' + projQ)).json()).mailboxes || [];
    for (var i = 0; i < mbs.length; i++) {
      var d = await (await fetch('/api/mail/run', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ mailbox_id: mbs[i].id }) })).json();
      console.log('Helpdesk opgehaald:', mbs[i].address, d.results || d);
    }
    renderHelpdeskTab(document.getElementById('tab-content'));
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

async function helpdeskSaveSignature(mailboxId, btn) {
  var ta = document.getElementById('hd-sig-' + mailboxId);
  if (!ta) return;
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Opslaan...';
  try {
    var resp = await fetch('/api/mail/mailboxes/' + encodeURIComponent(mailboxId), {
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ signature: ta.value }),
    });
    var d = await resp.json();
    if (d.ok) { btn.textContent = 'Opgeslagen ✓'; setTimeout(function(){ btn.textContent = orig; }, 1500); }
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.textContent = orig; }
  } catch(e) { alert('❌ ' + e.message); btn.textContent = orig; }
  finally { btn.disabled = false; }
}

var _hdKnowLoaded = {};
async function helpdeskLoadKnowledge(mailboxId) {
  if (_hdKnowLoaded[mailboxId]) return;
  _hdKnowLoaded[mailboxId] = true;
  var box = document.getElementById('hd-know-' + mailboxId);
  if (!box) return;
  try {
    var c = await (await fetch('/api/mail/mailboxes/' + encodeURIComponent(mailboxId) + '/knowledge')).json();
    function chip(ok, label) {
      return '<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;' +
        (ok ? 'background:#ecfdf5;color:#059669' : 'background:#fef2f2;color:#ef4444') + '">' + (ok ? '✓ ' : '✗ ') + label + '</span>';
    }
    var html = chip(c.vault, 'Vault-notes' + (c.vault ? ' (' + Math.round(c.vault_chars/100)/10 + 'k tekens)' : '')) +
      chip(c.site_profile, 'Merkprofiel') +
      chip(!!c.base_url, 'Site-URL' + (c.base_url ? '' : '')) +
      chip(c.live_pages > 0, 'Live pagina\'s (' + c.live_pages + ')') +
      chip(c.learned_qa > 0, 'Geleerde antwoorden (' + c.learned_qa + ')') +
      chip(c.signature, 'Handtekening') +
      '<div style="margin-top:4px;color:#94a3b8">Totale kennisbasis: ' + (c.total_chars || 0).toLocaleString('nl-NL') + ' tekens per antwoord.</div>';
    if (c.hints && c.hints.length) {
      html += '<ul style="margin:6px 0 0 14px;padding:0;color:#b45309">' + c.hints.map(function(h){ return '<li style="margin-bottom:2px">' + escHtml(h) + '</li>'; }).join('') + '</ul>';
    }
    box.innerHTML = html;
  } catch(e) { box.innerHTML = 'Fout: ' + escHtml(e.message); _hdKnowLoaded[mailboxId] = false; }
}

async function helpdeskToggle(mailboxId, enabled, btn) {
  btn.disabled = true;
  try {
    var resp = await fetch('/api/mail/mailboxes/' + encodeURIComponent(mailboxId), {
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ enabled: enabled }),
    });
    var d = await resp.json();
    if (d.ok) renderHelpdeskTab(document.getElementById('tab-content'));
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

async function helpdeskDeleteMailbox(mailboxId, address, btn) {
  if (!confirm('Mailbox ' + address + ' verwijderen?\n\nDe opgehaalde mails en concepten van deze mailbox verdwijnen ook uit Agent OS (de mail op de server zelf blijft staan).')) return;
  btn.disabled = true;
  try {
    var resp = await fetch('/api/mail/mailboxes/' + encodeURIComponent(mailboxId), { method:'DELETE' });
    var d = await resp.json();
    if (d.ok) renderHelpdeskTab(document.getElementById('tab-content'));
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

function helpdeskShowAddForm() {
  var box = document.getElementById('helpdesk-add-form');
  if (!box) return;
  box.innerHTML = '<div class="section-card" style="margin-top:16px">' +
    '<h3 style="margin-bottom:10px">Nieuwe mailbox voor ' + escHtml(currentProject || 'dit project') + '</h3>' +
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px">' +
    field('hd-project','Project', currentProject || 'Skillkaart', false, currentProject || '') +
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
    '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px;margin-top:8px">Handtekening (onder elk antwoord)' +
    '<textarea id="hd-signature" placeholder="Hartelijke groet,\nVincent van Munster" style="min-height:70px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;font-family:inherit;resize:vertical"></textarea></label>' +
    '<div style="margin-top:10px"><button onclick="helpdeskAdd(this)" style="padding:7px 18px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Aanmaken</button></div></div>';
}
function field(id, label, ph, pwd, val) {
  return '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px">' + label +
    '<input id="' + id + '" type="' + (pwd?'password':'text') + '" placeholder="' + escHtml(ph) + '"' + (val ? ' value="' + escHtml(val) + '"' : '') + ' style="padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px"></label>';
}
async function helpdeskAdd(btn) {
  var v = function(id){ var e = document.getElementById(id); return e ? e.value.trim() : ''; };
  var payload = {
    project: v('hd-project') || currentProject || '', address: v('hd-address'),
    pop_host: v('hd-pop-host'), pop_port: parseInt(v('hd-pop-port')||'110',10), pop_user: v('hd-pop-user'), pop_password: v('hd-pop-pass'),
    smtp_host: v('hd-smtp-host'), smtp_port: parseInt(v('hd-smtp-port')||'587',10), smtp_user: v('hd-smtp-user'), smtp_password: v('hd-smtp-pass'),
    from_display: v('hd-display'), signature: v('hd-signature'), enabled: 1, poll_minutes: 30,
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



// ═══════════════════════════════════════════════════════════════════
//  RECOVERED TABS — Content / Kansen / Wachtrij / Concurrentie /
//  Keywords / Technisch / Activiteit / Doelen (+ hun helpers)
//  Teruggehaald uit git-baseline (a229940) na frontend-modularisatie-
//  regressie die deze 8 render*Tab-functies deed verdwijnen. De
//  renderSeriesChart/renderPositionChart helpers staan in shell.js.
// ═══════════════════════════════════════════════════════════════════

// renderContentTab en zijn modal-helpers (openContentFile e.a.) zijn hier
// verwijderd (9 aug 2026): ze riepen /api/projects/{p}/content en
// /content-file* aan, routes die nergens in de backend bestaan — de tab heeft
// dus nooit data getoond, voor geen enkel project.
function renderSuggestions() {
  if (!weSuggestions.length) return '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen suggesties</p>';
  return weSuggestions.map(function(sug,i){return '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:10px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:flex-start"><div><p style="font-weight:600;font-size:13px">' + escHtml(sug.title) + '</p><p style="font-size:11px;color:#64748b;margin-top:2px">' + escHtml(sug.rationale) + '</p><div style="display:flex;gap:6px;margin-top:4px"><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.keyword) + '</span><span style="font-size:10px;padding:1px 6px;background:#f1f5f9;border-radius:4px;color:#475569">' + escHtml(sug.estimated_hours||'?') + '</span></div></div><button onclick="publishSuggestion(this,' + i + ')" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:4px;font-size:11px;cursor:pointer">Schrijf &amp; zet in Wachtrij</button></div></div>';}).join('');
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
  // De tellingen komen van de server (over de vólledige lijst), niet uit de
  // zojuist gefilterde weergave — anders zou "Uitgefilterd" altijd (0) tonen
  // zolang je er niet in kijkt, en is het filter alsnog onzichtbaar.
  var c = data.counts || {};
  var newCount = kansen.filter(function(o){return o.status==='new' && !o.filter_reason;}).length;
  var samenvatting = [];
  if (c.nieuw) samenvatting.push(c.nieuw + ' nieuw');
  if (c.in_behandeling) samenvatting.push(c.in_behandeling + ' in behandeling');
  if (c.gemeten) samenvatting.push(c.gemeten + ' met gemeten vraag');
  if (c.uitgefilterd) samenvatting.push(c.uitgefilterd + ' uitgefilterd');
  // De belofte van de hele lijst in één getal. Zonder dit is "11 kansen" een
  // hoeveelheid werk zonder opbrengst — en dan is meer altijd beter, wat de
  // reden was dat de knop "Schrijf alle 11" ooit aantrekkelijk leek.
  if (c.potentieel_klikken) samenvatting.push('samen ≈ +' + String(c.potentieel_klikken).replace('.',',') + ' klikken/mnd');
  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px"><div><h3 style="font-size:15px;font-weight:700">Kansen (' + kansen.length + ')</h3>' + (samenvatting.length?'<p style="font-size:11px;color:#64748b;margin-top:2px">' + samenvatting.join(' · ') + '</p>':'') + '</div><div style="display:flex;gap:6px;flex-wrap:wrap">' +
    '<select id="kansen-filter" onchange="oppStatusFilter=this.value;renderKansenTab(document.getElementById(\'tab-content\'))" style="padding:4px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;background:#fff">' +
    '<option value="open"'+(oppStatusFilter==='open'?' selected':'')+'>Open · niet afgerond (' + (c.open!=null?c.open:kansen.length) + ')</option><option value="">Alle</option><option value="new"'+(oppStatusFilter==='new'?' selected':'')+'>Nieuw (' + (c.nieuw||0) + ')</option><option value="in_progress"'+(oppStatusFilter==='in_progress'?' selected':'')+'>In behandeling</option><option value="published"'+(oppStatusFilter==='published'?' selected':'')+'>Gepubliceerd</option><option value="uitgefilterd"'+(oppStatusFilter==='uitgefilterd'?' selected':'')+'>Uitgefilterd (' + (c.uitgefilterd||0) + ')</option><option value="dismissed"'+(oppStatusFilter==='dismissed'?' selected':'')+'>Genegeerd</option></select>' +
    (newCount>=2?'<button onclick="writeAllNewKansen(this)" style="padding:4px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Schrijf alle ' + newCount + '</button>':'') +
    '<button onclick="runDemandScan()" style="padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Scan uitvoeren</button></div></div>';
  if (oppStatusFilter==='uitgefilterd') {
    html += '<p style="font-size:11px;color:#64748b;margin:-6px 0 12px;line-height:1.5">Deze zoekwoorden zijn uit de kansenlijst gehouden omdat er al content voor is, ze een bestaande pagina kannibaliseren, of het geen contentvraag is. Klopt een oordeel niet? Heropen de kans — dan telt hij weer mee.</p>';
  }
  if (!kansen.length) {
    var leegTekst = oppStatusFilter==='uitgefilterd'
      ? '<p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Niets uitgefilterd</p><p style="color:#94a3b8">Elke gevonden kans is bruikbaar.</p>'
      : '<p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Geen openstaande kansen</p><p style="color:#94a3b8">' + (c.uitgefilterd? c.uitgefilterd + ' kans(en) zijn uitgefilterd — bekijk ze via het menu.' : 'Voer een scan uit.') + '</p>';
    el.innerHTML = html + '<div class="empty-state">' + leegTekst + '</div>'; return;
  }
  kansen.forEach(function(opp, idx) {
    var sc = ({new:'#dbeafe',in_progress:'#fef3c7',published:'#dcfce7',dismissed:'#f1f5f9'})[opp.status]||'#f1f5f9';
    var st = ({new:'Nieuw',in_progress:'In behandeling',published:'Gepubliceerd',dismissed:'Genegeerd'})[opp.status]||opp.status;
    // Geen kale "Score 15" meer: een getal zonder eenheid zegt niemand of 15
    // veel is, en juist die vraag bepaalt of je de kans oppakt. De server
    // rekent in verwachte klikken per maand (potential.py).
    var opbrengst = opp.potential_clicks != null
      ? '<span style="font-weight:700;color:#166534">+' + String(opp.potential_clicks).replace('.',',') + ' klikken/mnd</span>'
      : '<span style="color:#94a3b8">opbrengst onbekend</span>';
    var pos = typeof opp.position==='number'?opp.position:10;
    var GOAL_POS = 3;
    var posPct = function(p){ return Math.max(0, Math.min(100, ((20-p)/19)*100)); };
    var curPct = noData ? 0 : posPct(pos), goalPct = posPct(GOAL_POS), atGoal = pos <= GOAL_POS;
    var barColor = atGoal ? '#16a34a' : (pos<=10?'#4f46e5':'#d97706');
    var hasData = (typeof opp.position==='number' && opp.position>0) || (opp.clicks&&opp.clicks>0) || (opp.impressions&&opp.impressions>0);
    var noData = (!hasData);
    if (noData) {
      // Geen GSC-data: toon eerlijke "geen data"-staat in plaats van vals "✓ doel bereikt"
      atGoal = false; barColor = '#94a3b8';
    }
    // Vraag-herkomst eerlijk labelen: een kans met 400 impressies op positie 7
    // is een andere belofte dan een bedacht zoekwoord uit de cold-start.
    var demandBadge = opp.demand==='gemeten'
      ? '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:#dcfce7;color:#166534;font-weight:600" title="Uit Google Search Console: mensen zoeken hier al op en de site verschijnt al">Gemeten vraag</span>'
      : '<span style="font-size:10px;padding:2px 8px;border-radius:6px;background:#f1f5f9;color:#64748b;font-weight:600" title="Bedacht zoekwoord (cold-start of trendsignaal) — nog geen gemeten vraag in Search Console">Speculatief</span>';
    var filterBanner = opp.filter_reason
      ? '<div style="margin-top:8px;padding:8px 10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;font-size:11px;color:#7c2d12;line-height:1.5">' +
        '<strong>Uitgefilterd — ' + escHtml(opp.filter_label||'') + '</strong>' +
        (opp.filter_detail?'<br>' + escHtml(opp.filter_detail):'') +
        (opp.filter_url?' <a href="'+escHtml(opp.filter_url)+'" target="_blank" style="color:#7c2d12;text-decoration:underline">bekijk</a>':'') +
        '</div>'
      : '';
    html += '<div class="opp-card" style="'+(opp.filter_reason?'border-left:3px solid #fb923c;opacity:.85;':(opp.status==='new'?'border-left:3px solid #4f46e5;':''))+'"><div class="opp-header"><div style="flex:1"><div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;flex-wrap:wrap"><p class="opp-query">'+escHtml(opp.query)+'</p><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+sc+';font-weight:600">'+st+'</span><span style="font-size:10px;padding:2px 8px;border-radius:6px;background:'+(opp.action==='re-optimaliseren'?'#fef3c7':'#dbeafe')+'">'+(opp.action==='re-optimaliseren'?'Heroptimaliseren':'Nieuwe content')+'</span>'+demandBadge+'</div>' +
    '<div class="opp-meta"><span style="color:#16a34a;font-weight:600">'+opp.clicks+' clicks</span><span>'+opp.impressions+' impressies</span><span>Pos. '+pos.toFixed(1)+'</span>'+opbrengst+'</div>' +
    (opp.potential_label?'<div style="font-size:11px;color:#64748b;margin-top:2px">'+escHtml(opp.potential_label)+'</div>':'') + '</div></div>' + filterBanner +
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
        '<span style="font-size:10px;width:64px;text-align:right;color:'+(noData?'#94a3b8':(atGoal?'#16a34a':'#94a3b8'))+';font-weight:'+(atGoal?'700':'400')+'">'+(noData?'– geen data':(atGoal?'✓ doel bereikt':'positie 1 →'))+'</span>' +
      '</div>' +
    '</div>' +
    '<div class="opp-actions" style="display:flex;gap:6px;flex-wrap:wrap">' +
    // Geen schrijfknop op een uitgefilterde kans: dát is de hele bedoeling van
    // het filter. Wie het oordeel betwist, gebruikt eerst "Toch oppakken".
    ((opp.status==='new'||opp.status==='in_progress')&&!opp.live_url&&!opp.filter_reason?'<button onclick="writeArticleFromOpp(this,'+idx+')" style="padding:5px 14px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Schrijf artikel</button>':'') +
    (opp.status==='new'&&!opp.filter_reason?'<button onclick="updateOppStatus(\''+opp.id+'\',\'in_progress\')" style="padding:5px 12px;background:#fff;color:#92400e;border:1.5px solid #f59e0b;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">→ Pak aan</button>':'') +
    (opp.filter_reason?'<button onclick="writeArticleFromOpp(this,'+idx+')" style="padding:5px 12px;background:#fff;color:#9a3412;border:1.5px solid #fb923c;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">Toch oppakken</button>':'') +
    ((opp.status==='in_progress'&&!opp.live_url)?'<button onclick="publishOpportunity(this,'+idx+')" style="padding:5px 12px;background:#16a34a;color:#fff;border:none;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">📝 Schrijf & zet in Wachtrij</button>':'') +
    (opp.live_url?'<a href="'+escHtml(opp.live_url)+'" target="_blank" style="padding:5px 12px;background:#fff;color:#166534;border:1.5px solid #16a34a;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600;text-decoration:none">🔗 Bekijk live</a>':'') +
    (opp.status!=='dismissed'&&!opp.live_url?'<button onclick="updateOppStatus(\''+opp.id+'\',\'dismissed\')" style="padding:5px 12px;background:#fff;color:#475569;border:1.5px solid #cbd5e1;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">✕ Negeren</button>':'') +
    (opp.status==='dismissed'?'<button onclick="updateOppStatus(\''+opp.id+'\',\'new\')" style="padding:5px 12px;background:#fff;color:#1e40af;border:1.5px solid #3b82f6;border-radius:6px;font-size:10px;cursor:pointer;font-weight:600">↺ Heropen</button>':'') +
    '</div>' +
    (opp.live_url?'<div style="margin-top:6px;font-size:10px;color:#16a34a">✓ Live: <a href="'+escHtml(opp.live_url)+'" target="_blank" style="color:#16a34a;text-decoration:underline">'+escHtml(opp.live_url)+'</a></div>':'') +
    '</div></div>';
  });
  el.innerHTML = html;
}
async function writeArticleFromOpp(btn, idx) {
  var opp = window._kansenData && window._kansenData[idx]; if (!opp) { alert('Kans niet gevonden'); return; }
  var waarschuwing = opp.filter_reason
    ? '\n\nLET OP — deze kans is uitgefilterd: ' + (opp.filter_label||'') +
      (opp.filter_detail? '\n' + opp.filter_detail : '') +
      '\nDoorgaan levert waarschijnlijk dubbele of kannibaliserende content op.'
    : '';
  if (!confirm('Schrijf artikel voor kans: "'+opp.query+'"?'+waarschuwing)) return;
  try {
    var result = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, btn);
    if (result.success) {
      await fetch('/api/demand/opportunities/'+opp.id, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'in_progress'}) });
      alert('Artikel opgeslagen: '+result.local_path+'\n'+formatSeoResultMsg(result));
      renderKansenTab(document.getElementById('tab-content'));
    } else alert('Mislukt: '+(result.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}
async function publishOpportunity(btn, idx) {
  var opp = window._kansenData && window._kansenData[idx]; if (!opp) { alert('Kans niet gevonden'); return; }
  if (!confirm('Artikel schrijven voor kans: "'+opp.query+'"?\n\nDit start de SEO-pipeline (schrijven → review → optimaliseren) en zet het resultaat klaar in de Wachtrij. Publiceren (incl. optioneel social) doe je daarna zelf met "Goedkeuren & publiceren".')) return;
  try {
    var result = await runArticlePipeline({title: opp.angle||opp.query, rationale: opp.rationale||'SEO-kans', keyword: opp.query}, btn);
    if (result && result.success) {
      if (result.passed_gate === false) {
        alert('Artikel geschreven maar onder de SEO-drempel (score ' + result.seo_score + '/10) — staat als "Verbeteren" klaar in de Wachtrij.\nConcept: ' + result.local_path);
      } else {
        alert('✅ Artikel klaar in de Wachtrij — keur daar goed om te publiceren (website altijd, social alleen als je dat aanvinkt).\nConcept: ' + result.local_path + '\n' + formatSeoResultMsg(result));
      }
      renderKansenTab(document.getElementById('tab-content'));
    } else if (result) alert('Mislukt: '+(result.detail||'onbekend'));
  } catch(e) { alert('Fout: '+e.message); }
}
async function writeAllNewKansen(btn) {
  if (!confirm('Schrijf artikelen voor ALLE nieuwe kansen? Dit kan even duren.')) return;
  // Expliciet ?status=new: dat is de gefilterde lijst. Zonder statusfilter komt
  // álles terug en zou de bulkknop precies de dubbelen en kannibalen schrijven
  // die het paneel net had weggehouden.
  var data = await (await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/kansen?status=new')).json();
  var newKansen = (data.kansen||[]).filter(function(o){return o.status==='new' && !o.filter_reason;});
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
      (function() {
        if (job.status !== 'pending_review') return '';
        var socPlatforms = Object.keys(wachtrijPlatformLabels).filter(function(p) { return (job.social_copy||{})[p]; });
        if (!socPlatforms.length) return '';
        return '<div style="margin-top:10px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px">' +
          '<span style="font-weight:600">Ook posten naar (optioneel, standaard uit):</span>' +
          socPlatforms.map(function(p) {
            // Bewust NIET default-checked: social is opt-in, nooit automatisch.
            return '<label style="display:flex;align-items:center;gap:4px;cursor:pointer"><input type="checkbox" class="soc-toggle-' + job.id + '" value="' + p + '">' + wachtrijPlatformLabels[p] + '</label>';
          }).join('') + '</div>';
      })() +
      '<div class="opp-actions" style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">' +
        '<button onclick="makeWachtrijVideo(this,\'' + job.id + '\')" style="padding:6px 12px;background:#7c3aed;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">🎬 Maak video</button>' +
        (job.status==='pending_review' ? '<button onclick="approveWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600">Goedkeuren &amp; publiceren</button>' +
        '<button onclick="regenerateWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:6px;font-size:11px;cursor:pointer">Opnieuw genereren</button>' +
        '<button onclick="rejectWachtrijJob(this,\'' + job.id + '\')" style="padding:6px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Afwijzen</button>' : '') +
      '</div>' +
      (job.video_path ? '<div style="margin-top:8px"><video src="/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + job.id + '/video" controls style="max-width:240px;border-radius:8px;border:1px solid #e2e8f0"></video></div>' : '') +
      '<div id="video-' + job.id + '" style="margin-top:8px"></div>' +
      (job.status==='published' && job.publish_result ? '<div style="margin-top:8px;font-size:11px;color:#64748b">' + renderPublishResult(job.publish_result) + '</div>' : '') +
      '</div>';
  });
  el.innerHTML = html;
}

function renderPublishResult(pr) {
  var parts = [];
  if (pr.netlify && pr.netlify.url) parts.push('Netlify: <a href="' + pr.netlify.url + '" target="_blank">' + pr.netlify.url + '</a>');
  if (pr.gsc && pr.gsc.status) parts.push('GSC: ' + pr.gsc.status);
  if (pr.bing && pr.bing.status_code) parts.push('Bing: ' + pr.bing.status_code);
  if (pr.social) {
    if (pr.social.skipped) parts.push('Social: ' + escHtml(pr.social.skipped));
    else Object.keys(pr.social).forEach(function(p) {
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
  var boxes = document.querySelectorAll('.soc-toggle-' + jobId);
  var channels = [];
  Array.prototype.forEach.call(boxes, function(b) { if (b.checked) channels.push(b.value); });
  var socialMsg = channels.length
    ? '\nSocial-posts: ' + channels.map(function(p){return wachtrijPlatformLabels[p]||p;}).join(', ') + '.'
    : '\nGeen social-posts — alleen de website.';
  if (!confirm('Artikel publiceren naar de website.' + socialMsg + '\nDoorgaan?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Publiceren...'; }
  try {
    // Altijd expliciet: het backend post alleen wat hier is aangevinkt.
    var opts = { method: 'POST', headers: {'Content-Type':'application/json'},
                 body: JSON.stringify({ channels: channels }) };
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/approve', opts);
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

async function makeWachtrijVideo(btn, jobId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Video maken... (30-60s)'; }
  var box = document.getElementById('video-' + jobId);
  try {
    var resp = await fetch('/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/make-video', { method: 'POST' });
    var data = await resp.json();
    if (!resp.ok) { alert('Mislukt: ' + (data.detail || data.error || 'onbekende fout')); if (btn) { btn.disabled = false; btn.textContent = '🎬 Maak video'; } return; }
    var url = '/api/projects/' + encodeURIComponent(currentProject) + '/content-queue/' + jobId + '/video';
    if (box) box.innerHTML = '<video src="' + url + '" controls style="max-width:240px;border-radius:8px;border:1px solid #e2e8f0"></video>' +
      '<div style="font-size:10px;color:#64748b;margin-top:4px">' + (data.scenes||'?') + ' scènes · ' + Math.round(data.duration||0) + 's · ' + (data.attributions||[]).length + ' Pexels-attributies</div>';
    renderWachtrijTab(document.getElementById('tab-content'));
  } catch(e) { alert('Fout: ' + e.message); if (btn) { btn.disabled = false; btn.textContent = '🎬 Maak video'; } }
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

// Toont de Doelen-tab álle projecten of alleen het geselecteerde? Standaard
// alleen het geselecteerde: de kop boven deze tab noemt één project, en een
// lijst die daar niet bij hoort is een stille leugen (4 aug 2026 — de tab
// haalde `/api/goals` zonder projectfilter op, waardoor onder de kop
// "Bewaardvoorjou" negen doelen van vier ándere projecten stonden).
let goalsAlleProjecten = false;

function toggleGoalScope() {
  goalsAlleProjecten = !goalsAlleProjecten;
  var el = document.getElementById('tab-content');
  if (el) renderDoelenTab(el);
}

async function renderDoelenTab(el) {
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Doelen laden...</p></div>';
  var scope = (!goalsAlleProjecten && currentProject) ? currentProject : '';
  try {
    var resp = await fetch('/api/goals?limit=25' +
      (scope ? '&project=' + encodeURIComponent(scope) : ''));
    var goals = await resp.json();
  } catch(e) { el.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; return; }

  var scopeLabel = scope ? escHtml(scope) : 'alle projecten';
  var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">' +
    '<div><h3 style="font-size:15px;font-weight:700">Goal Mode</h3>' +
    '<span style="font-size:11px;color:#64748b">Doelen van <strong>' + scopeLabel + '</strong>' +
    (currentProject ? ' &middot; <a href="#" onclick="event.preventDefault();toggleGoalScope()" style="color:#4f46e5">' +
      (goalsAlleProjecten ? 'alleen ' + escHtml(currentProject) : 'alle projecten') + '</a>' : '') +
    '</span></div>' +
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
        // Teller en noemer moeten dezelfde eenheid hebben. Tot 4 aug 2026 stond
        // hier `phase_count` (fases) onder `completed_tasks` (taken), wat "9/3"
        // en "14/4" opleverde en elke balk op >100% zette \u2014 een doel waarvan 4
        // van de 13 taken faalden zag er identiek uit als een dat alles haalde.
        // Geteld uit `goal_tasks`, niet uit de opgeslagen plancijfers: bij vijf
        // doelen stond `task_count` op 14 terwijl er 28 taakrijen waren (de hele
        // planning was tweemaal weggeschreven en dus tweemaal uitgevoerd).
        var total = (g.tasks_actual != null ? g.tasks_actual : g.task_count) || 0;
        var done = (g.completed_actual != null ? g.completed_actual : g.completed_tasks) || 0;
        var failed = (g.failed_actual != null ? g.failed_actual : g.failed_tasks) || 0;
        var pctDone = total > 0 ? Math.min(100, Math.round(done/total*100)) : 0;
        var pctFail = total > 0 ? Math.min(100 - pctDone, Math.round(failed/total*100)) : 0;
        var barColor = g.status==='completed' ? '#16a34a' : g.status==='failed' ? '#ef4444' : '#4f46e5';
        // Gefaalde taken staan in de data en werden nergens getoond. Juist bij
        // 'partial' is dat het hele verhaal: 'partial' betekent per definitie
        // dat de \u00e9chte actie niet is uitgevoerd.
        var counter = done + '/' + total + (failed ? ' <span style="color:#dc2626;font-weight:600">' + failed + ' mislukt</span>' : '');
        return '<tr style="cursor:pointer" onclick="loadGoalDetail(\'' + g.id + '\')"><td><span class="badge" style="background:' + sc + '">' + escHtml(g.status) + '</span> ' + escHtml(g.title) +
          (goalsAlleProjecten && g.project ? ' <span style="font-size:10px;color:#94a3b8">&middot; ' + escHtml(g.project) + '</span>' : '') +
          (g.status==='failed'?' <button onclick="event.stopPropagation();retryFailedGoal(\'' + g.id + '\')" style="padding:2px 8px;background:#ef4444;color:#fff;border:none;border-radius:4px;font-size:10px;cursor:pointer">\u2728 Los het op met AI</button>':'') + '</td>' +
          '<td>' + escHtml(g.status) + '</td><td><div style="display:flex;align-items:center;gap:6px" title="' + done + ' voltooid, ' + failed + ' mislukt van ' + total + ' taken">' +
          '<div style="flex:1;height:4px;background:#e2e8f0;border-radius:2px;overflow:hidden;display:flex">' +
          '<div style="height:100%;width:' + pctDone + '%;background:' + barColor + '"></div>' +
          '<div style="height:100%;width:' + pctFail + '%;background:#ef4444"></div></div>' +
          '<span style="font-size:10px;color:#64748b;white-space:nowrap">' + counter + '</span></div></td>' +
          '<td style="font-size:11px;color:#94a3b8">' + (g.created_at ? g.created_at.slice(0,10) : '') + '</td></tr>';
      }).join('') +
      '</tbody></table></div>';
  } else {
    html += '<div class="section-card"><div class="empty-state"><p style="font-size:14px;font-weight:600;color:#475569;margin-bottom:4px">Nog geen doelen voor ' + scopeLabel + '</p>' +
      '<p style="color:#94a3b8">Stel een langetermijndoel in. Hermes splitst het op in taken en voert ze autonoom uit.' +
      (scope ? ' Andere projecten kunnen wél doelen hebben — zie <a href="#" onclick="event.preventDefault();toggleGoalScope()" style="color:#4f46e5">alle projecten</a>.' : '') +
      '</p></div></div>';
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


// ═══════════════════════════════════════════════════════════════════
//  SOCIAL INBOX — per-project social kanalen, review-gate voor antwoorden
//  Gespiegeld aan de mail-helpdesk: de agent leest reacties/DM's, schrijft
//  een concept in de merkstem, en Vincent keurt één keer om te plaatsen.
// ═══════════════════════════════════════════════════════════════════

function _socialPlatformLabel(p) {
  return ({ linkedin:'LinkedIn', facebook:'Facebook', instagram:'Instagram', tiktok:'TikTok' })[p] || p;
}

async function renderSocialInboxSection(el) {
  if (!currentProject) return;
  var wrap = document.createElement('div');
  wrap.style.marginTop = '22px';
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div><p>Social inbox laden...</p></div>';
  el.appendChild(wrap);
  try {
    var [inbResp, pendResp] = await Promise.all([
      fetch('/api/social-inbox/inboxes?project=' + encodeURIComponent(currentProject)).then(function(r){return r.json();}),
      fetch('/api/social-inbox/' + encodeURIComponent(currentProject) + '/pending').then(function(r){return r.json();}),
    ]);
    var inboxes = inbResp || [];
    var pending = pendResp || [];
    var byInbox = {};
    pending.forEach(function(m){ (byInbox[m.inbox_id] = byInbox[m.inbox_id] || []).push(m); });

    var html = '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px">' +
      '<div><h3 style="margin:0;font-size:15px;font-weight:700">📱 Social inbox — ' + escHtml(currentProject) + '</h3>' +
      '<p style="font-size:11px;color:#64748b;margin:2px 0 0">' + inboxes.length + ' kanaal(en) · ' + pending.length + ' concept-antwoord(en) wacht op goedkeuring</p></div>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="socialShowAddForm()" style="padding:6px 14px;background:#fff;color:#0ea5e9;border:1px solid #bae6fd;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Kanaal</button>' +
      '<button onclick="socialRunAll(this)" style="padding:6px 16px;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">↻ Nu ophalen</button>' +
      '</div></div>';

    if (!inboxes.length) {
      html += '<div class="empty-state"><p style="font-size:13px;font-weight:600;color:#475569;margin-bottom:6px">Nog geen social kanalen voor ' + escHtml(currentProject) + '</p>' +
        '<p style="color:#94a3b8;font-size:12px;margin-bottom:10px">Koppel LinkedIn, Instagram, Facebook of TikTok. De agent leest reacties/DM\'s, schrijft een concept in jouw stijl, en jij keurt één keer.</p>' +
        '<button onclick="socialShowAddForm()" style="padding:7px 16px;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">+ Kanaal toevoegen</button></div>';
    } else {
      inboxes.forEach(function(ib) {
        var msgs = byInbox[ib.id] || [];
        var statusCls = ib.enabled ? '#16a34a' : '#94a3b8';
        html += '<div style="border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-bottom:8px">' +
          '<div style="display:flex;align-items:center;gap:8px"><span style="width:9px;height:9px;border-radius:50%;background:' + statusCls + '"></span>' +
          '<h4 style="margin:0;font-size:13px;font-weight:700">' + escHtml(_socialPlatformLabel(ib.platform)) + (ib.label ? ' · ' + escHtml(ib.label) : '') + '</h4>' +
          '<span style="font-size:10px;color:#94a3b8;background:#f1f5f9;padding:1px 7px;border-radius:10px">' + escHtml(ib.project) + '</span></div>' +
          '<div style="display:flex;gap:6px">' +
          '<button onclick="socialRunOne(\'' + escHtml(ib.id) + '\',this)" style="padding:4px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">↻ Ophalen</button>' +
          '<button onclick="socialToggle(\'' + escHtml(ib.id) + '\',' + (ib.enabled ? 0 : 1) + ',this)" style="padding:4px 12px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">' + (ib.enabled ? 'Pauzeer' : 'Activeer') + '</button>' +
          '</div></div>';
        if (!msgs.length) {
          html += '<p style="color:#94a3b8;font-size:12px;text-align:center;padding:12px">Geen open concepten — kanaal is bijgewerkt.</p>';
        } else {
          msgs.forEach(function(m) {
            var isEdited = m.status === 'edited';
            var kindBadge = ({ question:'#0ea5e9', complaint:'#ef4444', praise:'#16a34a', spam:'#94a3b8', other:'#94a3b8' })[m.kind] || '#94a3b8';
            var body = m.edited_body || m.draft_body || '';
            html += '<div class="social-item" id="soc-item-' + m.id + '" style="border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px;background:#fff">' +
              '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
              '<span style="font-size:10px;font-weight:600;color:#fff;background:' + kindBadge + ';padding:2px 8px;border-radius:10px">' + m.kind + '</span>' +
              '<span style="font-size:12px;font-weight:600;color:#1e293b">Van: ' + escHtml(m.author_name || m.author_handle || 'iemand') + '</span>' +
              (m.manual ? '<span style="font-size:10px;font-weight:600;color:#d97706;background:#fef3c7;padding:1px 7px;border-radius:10px">plak-antwoord</span>' : '') +
              '</div>' +
              (m.text ? '<div style="font-size:12px;color:#475569;white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px;margin-bottom:8px;max-height:140px;overflow-y:auto">' + escHtml(m.text) + '</div>' : '') +
              (m.parent_url ? '<div style="font-size:11px;margin-bottom:6px"><a href="' + escHtml(m.parent_url) + '" target="_blank" style="color:#2563eb">Bekijk originele post ↗</a></div>' : '') +
              '<textarea id="soc-body-' + m.id + '" style="width:100%;min-height:90px;font-size:12px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:' + (isEdited ? '#fffbeb' : '#f8fafc') + '">' + escHtml(body) + '</textarea>' +
              '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">' +
              '<button onclick="socialApprove(' + m.id + ',this)" style="padding:6px 16px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">✓ Plaats antwoord</button>' +
              '<button onclick="socialSave(' + m.id + ',this)" style="padding:6px 14px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;cursor:pointer">Opslaan</button>' +
              '<button onclick="copySocialReply(' + m.id + ')" style="padding:6px 14px;background:#fff;color:#0ea5e9;border:1px solid #bae6fd;border-radius:6px;font-size:12px;cursor:pointer">⧉ Kopieer</button>' +
              '<button onclick="socialReject(' + m.id + ',this)" style="padding:6px 14px;background:#fff;color:#ef4444;border:1px solid #fecaca;border-radius:6px;font-size:12px;cursor:pointer">Afwijzen</button>' +
              '</div></div>';
          });
        }
        html += '</div>';
      });
    }
    html += '<div id="social-add-form"></div>';
    wrap.innerHTML = html;
  } catch(e) {
    wrap.innerHTML = '<div class="empty-state">Social inbox fout: ' + escHtml(e.message) + '</div>';
  }
}

async function socialApprove(id, btn) {
  if (!confirm('Antwoord plaatsen op het sociale kanaal?')) return;
  btn.disabled = true; btn.textContent = 'Plaatsen...';
  try {
    var resp = await fetch('/api/social-inbox/msg/' + id + '/approve', { method:'POST' });
    var d = await resp.json();
    if (d.success) {
      if (d.manual) {
        alert('ℹ️ Dit kanaal staat geen API-antwoord toe (LinkedIn/TikTok zonder partner-toegang).\n\nHet antwoord is gemarkeerd als geplaatst — kopieer het hierboven en plaats het handmatig op het kanaal.');
      } else {
        alert('✅ Antwoord geplaatst' + (d.url ? ':\n' + d.url : '.'));
      }
      var item = document.getElementById('soc-item-' + id); if (item) item.remove();
    } else {
      alert('❌ Kon niet plaatsen: ' + (d.error || 'onbekend'));
      btn.disabled = false; btn.textContent = '✓ Plaats antwoord';
    }
  } catch(e) { alert('❌ Fout: ' + e.message); btn.disabled = false; btn.textContent = '✓ Plaats antwoord'; }
}

async function socialSave(id, btn) {
  var ta = document.getElementById('soc-body-' + id); if (!ta) return;
  btn.disabled = true; btn.textContent = 'Opslaan...';
  try {
    var resp = await fetch('/api/social-inbox/msg/' + id + '/edit', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ text: ta.value }),
    });
    var d = await resp.json();
    if (d.success) { ta.style.background = '#fffbeb'; btn.textContent = 'Opgeslagen ✓'; setTimeout(function(){ btn.textContent='Opslaan'; }, 1500); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.textContent = 'Opslaan'; }
  } catch(e) { alert('❌ ' + e.message); btn.textContent = 'Opslaan'; }
  finally { btn.disabled = false; }
}

function copySocialReply(id) {
  var ta = document.getElementById('soc-body-' + id); if (!ta) return;
  navigator.clipboard.writeText(ta.value).then(function(){
    alert('📋 Antwoord gekopieerd — plak het op het kanaal.');
  });
}

async function socialReject(id, btn) {
  if (!confirm('Concept afwijzen?')) return;
  btn.disabled = true;
  try {
    var resp = await fetch('/api/social-inbox/msg/' + id + '/reject', { method:'POST' });
    var d = await resp.json();
    if (d.success) { var item = document.getElementById('soc-item-' + id); if (item) item.remove(); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

function socialToast(msg) {
  var t = document.getElementById('social-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'social-toast';
    t.style.cssText = 'position:fixed;bottom:24px;right:24px;background:#0f172a;color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,.25);opacity:0;transition:opacity .2s';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = '1';
  clearTimeout(window._socialToastTimer);
  window._socialToastTimer = setTimeout(function(){ t.style.opacity = '0'; }, 3500);
}

async function socialRunAll(btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Ophalen...';
  try {
    var inboxes = (await (await fetch('/api/social-inbox/inboxes?project=' + encodeURIComponent(currentProject))).json()) || [];
    var total = 0;
    for (var i = 0; i < inboxes.length; i++) {
      var d = await (await fetch('/api/social-inbox/inboxes/' + encodeURIComponent(inboxes[i].id) + '/poll', { method:'POST' })).json();
      total += (d && typeof d.fetched === 'number') ? d.fetched : 0;
    }
    socialToast(total > 0 ? ('✓ Bijgewerkt — ' + total + ' nieuw(e) bericht(en)') : '✓ Bijgewerkt — geen nieuwe berichten');
    renderHelpdeskTab(document.getElementById('tab-content'));
  } catch(e) { socialToast('❌ ' + e.message); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

async function socialRunOne(inboxId, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = '...';
  try {
    var d = await (await fetch('/api/social-inbox/inboxes/' + encodeURIComponent(inboxId) + '/poll', { method:'POST' })).json();
    var n = (d && typeof d.fetched === 'number') ? d.fetched : 0;
    socialToast(n > 0 ? ('✓ Bijgewerkt — ' + n + ' nieuw(e) bericht(en)') : '✓ Bijgewerkt — geen nieuwe berichten');
    renderHelpdeskTab(document.getElementById('tab-content'));
  } catch(e) { socialToast('❌ ' + e.message); }
  finally { btn.disabled = false; btn.textContent = orig; }
}

async function socialToggle(inboxId, enabled, btn) {
  btn.disabled = true;
  try {
    var resp = await fetch('/api/social-inbox/inboxes/' + encodeURIComponent(inboxId), {
      method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ enabled: enabled }),
    });
    var d = await resp.json();
    if (d.success) renderHelpdeskTab(document.getElementById('tab-content'));
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

async function socialDelete(inboxId, label, btn) {
  if (!confirm('Kanaal ' + label + ' verwijderen? De opgehaalde berichten verdwijnen uit Agent OS.')) return;
  btn.disabled = true;
  try {
    var resp = await fetch('/api/social-inbox/inboxes/' + encodeURIComponent(inboxId), { method:'DELETE' });
    var d = await resp.json();
    if (d.success) renderHelpdeskTab(document.getElementById('tab-content'));
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.disabled = false; }
  } catch(e) { alert('❌ ' + e.message); btn.disabled = false; }
}

function socialShowAddForm() {
  var box = document.getElementById('social-add-form');
  if (!box) return;
  box.innerHTML = '<div class="section-card" style="margin-top:16px">' +
    '<h4 style="margin-bottom:10px">Nieuw sociaal kanaal voor ' + escHtml(currentProject || 'dit project') + '</h4>' +
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px">' +
    field('soc-project','Project', currentProject || 'bewaardvoorjou', false, currentProject || '') +
    '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px">Kanaal' +
    '<select id="soc-platform" style="padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">' +
    '<option value="linkedin">LinkedIn</option><option value="facebook">Facebook</option>' +
    '<option value="instagram">Instagram</option><option value="tiktok">TikTok</option></select></label>' +
    field('soc-label','Label','BVJ FB') +
    '</div>' +
    '<div style="font-size:11px;color:#64748b;margin-top:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px">' +
    'Facebook/Instagram delen het FB Page-token uit <code>.env</code> (of vul page_id + token hier). ' +
    'LinkedIn posten werkt direct; reacties via plak-antwoord. TikTok vraagt een geregistreerde app ' +
    '(<code>TIKTOK_CLIENT_KEY/SECRET</code>).</div>' +
    '<div style="margin-top:10px"><button onclick="socialAdd(this)" style="padding:7px 18px;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">Aanmaken</button></div></div>';
}

async function socialAdd(btn) {
  var v = function(id){ var e = document.getElementById(id); return e ? e.value.trim() : ''; };
  var platform = (document.getElementById('soc-platform') || {}).value || 'linkedin';
  var payload = {
    project: v('soc-project') || currentProject || '', platform: platform,
    label: v('soc-label'), brand_context: v('soc-project') || currentProject || '',
    enabled: 1, poll_minutes: 30,
  };
  if (!payload.project) { alert('Vul een project in.'); return; }
  btn.disabled = true; btn.textContent = 'Aanmaken...';
  try {
    var resp = await fetch('/api/social-inbox/inboxes', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    var d = await resp.json();
    if (d.success || d.id) { document.getElementById('social-add-form').innerHTML=''; renderHelpdeskTab(document.getElementById('tab-content')); }
    else { alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.textContent='Aanmaken'; }
  } catch(e) { alert('❌ ' + e.message); btn.textContent = 'Aanmaken'; }
  finally { btn.disabled = false; }
}


// ══════════════════════════════════════════════════════════════════════════
//  SOCIAL CREATIE TAB — agents maken posts, beeld-briefs & TikTok-packs
// ══════════════════════════════════════════════════════════════════════════

function _scBadge(status) {
  var map = {
    pending_review: ['#fffbeb', '#d97706', 'Wacht op goedkeuring'],
    approved:       ['#f0fdf4', '#16a34a', 'Goedgekeurd'],
    rejected:       ['#fef2f2', '#ef4444', 'Afgewezen'],
    posted:         ['#eff6ff', '#2563eb', 'Geplaatst'],
  };
  var b = map[status] || ['#f1f5f9', '#475569', status];
  return '<span style="font-size:10px;font-weight:600;color:' + b[1] + ';background:' + b[0] + ';padding:2px 8px;border-radius:10px">' + b[2] + '</span>';
}

async function renderSocialCreatieTab(el) {
  if (!currentProject) { el.innerHTML = '<div class="empty-state">Kies eerst een project.</div>'; return; }
  el.innerHTML = '<div class="loading"><div class="spinner"></div><p>Social content laden...</p></div>';
  try {
    var packs = (await (await fetch('/api/social-content/packs?project=' + encodeURIComponent(currentProject))).json()) || [];
    var html = '<div class="section-card" style="margin-bottom:16px">' +
      '<h3 style="margin:0 0 4px;font-size:15px;font-weight:700">Social Creatie — ' + escHtml(currentProject) + '</h3>' +
      '<p style="font-size:12px;color:#64748b;margin:0 0 14px">De agent schrijft posts voor LinkedIn, Facebook, Instagram en TikTok in jouw merkstem, plus een Canva-ready beeld-brief en een TikTok-scriptpack. Alles wacht op jouw goedkeuring — er wordt niets automatisch gepost.</p>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;align-items:end">' +
      '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px">Thema' +
      '<input id="sc-theme" placeholder="bijv. buurtfeest verbindt mensen" style="padding:7px 9px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px"></label>' +
      '<label style="font-size:11px;color:#475569;display:flex;flex-direction:column;gap:3px">Invalshoek (optioneel)' +
      '<input id="sc-angle" placeholder="bijv. echte ontmoetingen ipv scherm" style="padding:7px 9px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px"></label>' +
      '<label style="font-size:11px;color:#475569;display:flex;gap:12px;flex-direction:row;align-items:center;height:34px">' +
      '<span style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="sc-img" checked style="accent-color:#e5a500"> Beeld</span>' +
      '<span style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="sc-vid" checked style="accent-color:#e5a500"> TikTok</span></label>' +
      '<button onclick="scGenerate(this)" style="padding:8px 18px;background:#e5a500;color:#1f2937;border:none;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;height:34px">Genereer content pack</button>' +
      '</div></div>';

    if (!packs.length) {
      html += '<div class="empty-state"><p style="font-size:13px;color:#475569">Nog geen content packs voor ' + escHtml(currentProject) + '.</p>' +
        '<p style="font-size:12px;color:#94a3b8">Vul een thema in en klik "Genereer content pack".</p></div>';
    } else {
      html += '<div style="display:flex;flex-direction:column;gap:12px">';
      packs.forEach(function(p) {
        var conceptTag = p.concept ? '<span style="font-size:10px;font-weight:600;color:#92400e;background:#fef3c7;padding:2px 8px;border-radius:10px">concept (geen LLM)</span>' : '';
        html += '<div class="section-card" style="margin:0">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:8px">' +
          '<div style="display:flex;align-items:center;gap:8px"><strong style="font-size:13px">' + escHtml(p.theme) + '</strong>' +
          _scBadge(p.status) + conceptTag + '</div>' +
          '<div style="display:flex;gap:6px">' +
          '<button onclick="scOpenPack(\'' + escHtml(p.id) + '\')" style="padding:5px 12px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Bekijk</button>' +
          (p.status === 'pending_review' ?
            '<button onclick="scApprove(\'' + escHtml(p.id) + '\',this)" style="padding:5px 12px;background:#059669;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Goedkeuren</button>' : '') +
          (p.status === 'approved' ?
            '<button onclick="scPublish(\'' + escHtml(p.id) + '\',this)" style="padding:5px 12px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:11px;cursor:pointer">Plaatsen…</button>' : '') +
          '<button onclick="scReject(\'' + escHtml(p.id) + '\',this)" style="padding:5px 10px;background:#fff;color:#ef4444;border:1px solid #fecaca;border-radius:6px;font-size:11px;cursor:pointer">Afwijzen</button>' +
          '</div></div>' +
          (p.angle ? '<p style="font-size:11px;color:#64748b;margin:0 0 6px">Invalshoek: ' + escHtml(p.angle) + '</p>' : '') +
          '<div style="display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#64748b">' +
          Object.keys(p.copy || {}).map(function(k){ return '<span style="background:#f1f5f9;padding:2px 7px;border-radius:10px">' + _socialPlatformLabel(k) + '</span>'; }).join('') +
          (p.image_brief ? '<span style="background:#fef3c7;padding:2px 7px;border-radius:10px">Beeld-brief</span>' : '') +
          (p.tiktok_pack ? '<span style="background:#ede9fe;padding:2px 7px;border-radius:10px">TikTok-pack</span>' : '') +
          (p.video_url ? '<span style="background:#fee2e2;color:#991b1b;padding:2px 7px;border-radius:10px">🎬 Video</span>' : '') +
          '</div></div>';
      });
      html += '</div>';
    }
    html += '<div id="sc-detail"></div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div class="empty-state">Social Creatie fout: ' + escHtml(e.message) + '</div>';
  }
}

async function scGenerate(btn) {
  var theme = (document.getElementById('sc-theme') || {}).value || '';
  if (!theme.trim()) { alert('Vul een thema in.'); return; }
  var angle = (document.getElementById('sc-angle') || {}).value || '';
  var withImg = !!(document.getElementById('sc-img') || {}).checked;
  var withVid = !!(document.getElementById('sc-vid') || {}).checked;
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Genereren…';
  try {
    var resp = await fetch('/api/social-content/generate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ project: currentProject, theme: theme, angle: angle, with_image: withImg, with_video: withVid }),
    });
    var d = await resp.json();
    if (d.success && d.pack) {
      socialToast('✓ Content pack aangemaakt — keur de posts goed');
      renderSocialCreatieTab(document.getElementById('tab-content'));
    } else {
      alert('❌ ' + (d.error || d.detail || 'onbekend')); btn.textContent = orig;
    }
  } catch (e) { alert('❌ ' + e.message); btn.textContent = orig; }
  finally { btn.disabled = false; }
}

async function scApprove(id, btn) {
  btn.disabled = true;
  try {
    var resp = await fetch('/api/social-content/packs/' + encodeURIComponent(id) + '/approve', { method:'POST' });
    var d = await resp.json();
    if (d.success) { socialToast('✓ Goedgekeurd'); renderSocialCreatieTab(document.getElementById('tab-content')); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; }
  } catch (e) { alert('❌ ' + e.message); btn.disabled = false; }
}

async function scReject(id, btn) {
  if (!confirm('Content pack afwijzen?')) return;
  btn.disabled = true;
  try {
    var resp = await fetch('/api/social-content/packs/' + encodeURIComponent(id) + '/reject', { method:'POST' });
    var d = await resp.json();
    if (d.success) { socialToast('Afgewezen'); renderSocialCreatieTab(document.getElementById('tab-content')); }
    else { alert('❌ ' + (d.error || 'onbekend')); btn.disabled = false; }
  } catch (e) { alert('❌ ' + e.message); btn.disabled = false; }
}

async function scPublish(id, btn) {
  if (!confirm('Content pack plaatsen? Goedgekeurde posts gaan naar de gekoppelde kanalen.')) return;
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Plaatsen…';
  try {
    var pack = (await (await fetch('/api/social-content/packs/' + encodeURIComponent(id))).json());
    var platforms = Object.keys(pack.copy || {});
    for (var i = 0; i < platforms.length; i++) {
      var r = await (await fetch('/api/social-content/packs/' + encodeURIComponent(id) + '/publish', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ platform: platforms[i] }),
      })).json();
      if (r.manual) {
        socialToast('ℹ️ ' + _socialPlatformLabel(platforms[i]) + ': plak de tekst handmatig op het kanaal');
      } else if (r.success) {
        socialToast('✓ Geplaatst op ' + _socialPlatformLabel(platforms[i]));
      }
    }
    renderSocialCreatieTab(document.getElementById('tab-content'));
  } catch (e) { alert('❌ ' + e.message); btn.textContent = orig; }
  finally { btn.disabled = false; }
}

async function scOpenPack(id) {
  var box = document.getElementById('sc-detail'); if (!box) return;
  box.innerHTML = '<div class="loading" style="padding:20px"><div class="spinner"></div></div>';
  try {
    var p = await (await fetch('/api/social-content/packs/' + encodeURIComponent(id))).json();
    var html = '<div class="section-card" style="margin-top:16px;border-color:#e5a500">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<strong style="font-size:14px">' + escHtml(p.theme) + '</strong>' + _scBadge(p.status) + '</div>';
    html += '<h4 style="font-size:12px;font-weight:700;margin:14px 0 6px;color:#475569">Posts per kanaal</h4>';
    Object.keys(p.copy || {}).forEach(function(k) {
      var txt = p.copy[k] || '';
      html += '<div style="margin-bottom:10px">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px">' +
        '<span style="font-size:11px;font-weight:700;color:#1e293b">' + _socialPlatformLabel(k) + '</span>' +
        '<button onclick="scCopyText(this)" data-text="' + escHtml(txt) + '" style="padding:2px 8px;background:#fff;border:1px solid #cbd5e1;border-radius:4px;font-size:10px;cursor:pointer">Kopieer</button></div>' +
        '<div style="font-size:12px;color:#334155;white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px">' + escHtml(txt) + '</div></div>';
    });
    if (p.image_brief) {
      var ib = p.image_brief;
      html += '<h4 style="font-size:12px;font-weight:700;margin:14px 0 6px;color:#475569">Beeld-brief (Canva / Midjourney)</h4>' +
        '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px;font-size:12px;color:#475569">' +
        '<div><strong>Kop:</strong> ' + escHtml(ib.headline || '') + '</div>' +
        (ib.subtext ? '<div><strong>Onderschrift:</strong> ' + escHtml(ib.subtext) + '</div>' : '') +
        '<div><strong>Formaat:</strong> ' + escHtml(ib.dimensions || '') + ' · <strong>Type:</strong> ' + escHtml(ib.template_type || '') + '</div>' +
        (ib.layout ? '<div><strong>Opmaak:</strong> ' + escHtml(ib.layout) + '</div>' : '') +
        (ib.canva_edit_url ? '<div style="margin-top:6px"><a href="' + escHtml(ib.canva_edit_url) + '" target="_blank" style="display:inline-block;padding:5px 12px;background:#00c4cc;color:#fff;border-radius:6px;font-size:11px;font-weight:600;text-decoration:none">Open in Canva ↗</a></div>' : '') +
        (ib.canva_template_url ? '<div style="margin-top:6px"><a href="' + escHtml(ib.canva_template_url) + '" target="_blank" style="display:inline-block;padding:4px 10px;background:#fff;border:1px solid #00c4cc;color:#0e7490;border-radius:6px;font-size:11px;font-weight:600;text-decoration:none">Open basis-template ↗</a></div>' : '') +
        (ib.canva_method ? '<div style="margin-top:6px;font-size:10px;color:#64748b">' + (ib.canva_method === 'autofill' ? '✅ Automatisch ingevuld uit template' : ib.canva_method === 'create' ? '⚠️ Leeg design aangemaakt (geen template-id)' : '') + '</div>' : '') +
        '<div style="margin-top:6px"><strong>Midjourney-prompt:</strong></div>' +
        '<div style="font-size:11px;background:#fff;border:1px solid #fde68a;border-radius:6px;padding:6px;white-space:pre-wrap;color:#7c2d12">' + escHtml(ib.midjourney_prompt || '') + '</div>' +
        '<div style="margin-top:6px;font-size:11px;color:#92400e">' + escHtml(ib.canva_note || '') + '</div></div>';
    }
    if (p.tiktok_pack) {
      var tp = p.tiktok_pack;
      // Verdedigend: shotlist kan uit oude rows een string zijn (newline-lijst).
      // Normaliseer naar een array zodat .map nooit crasht.
      if (typeof tp.shotlist === 'string') {
        tp.shotlist = tp.shotlist.split('\n').map(function(s){ return s.trim().replace(/^[-*]\s*/, ''); }).filter(Boolean);
      }
      if (!Array.isArray(tp.shotlist)) tp.shotlist = [];
      html += '<h4 style="font-size:12px;font-weight:700;margin:14px 0 6px;color:#475569">TikTok / Reels-scriptpack</h4>' +
        '<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:8px;padding:10px;font-size:12px;color:#475569">' +
        '<div><strong>Hook:</strong> ' + escHtml(tp.hook || '') + '</div>' +
        (tp.script ? '<div style="margin-top:4px"><strong>Script:</strong></div><div style="white-space:pre-wrap">' + escHtml(tp.script) + '</div>' : '') +
        (tp.shotlist && tp.shotlist.length ? '<div style="margin-top:4px"><strong>Shotlist:</strong><ul style="margin:4px 0 0 16px">' + tp.shotlist.map(function(s){ return '<li>' + escHtml(s) + '</li>'; }).join('') + '</ul></div>' : '') +
        (tp.voiceover_cues ? '<div style="margin-top:4px"><strong>Voiceover:</strong> ' + escHtml(tp.voiceover_cues) + '</div>' : '') +
        (tp.captions ? '<div style="margin-top:4px"><strong>Captions:</strong> ' + escHtml(tp.captions) + '</div>' : '') +
        (tp.hashtags && tp.hashtags.length ? '<div style="margin-top:4px"><strong>Hashtags:</strong> ' + tp.hashtags.map(function(h){ return '#' + escHtml(h); }).join(' ') + '</div>' : '') +
        '</div>';
    }
    // Video (9:16 short) — gerenderd uit het scriptpack (edge-tts + merk-slides + ffmpeg).
    html += '<h4 style="font-size:12px;font-weight:700;margin:14px 0 6px;color:#475569">Video (9:16 short)</h4>' +
      '<div id="sc-video-' + escHtml(p.id) + '" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px">';
    if (p.video_url) {
      html += '<video src="' + escHtml(p.video_url) + '" controls playsinline style="width:230px;max-width:100%;border-radius:10px;background:#000;display:block"></video>' +
        '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">' +
        '<a href="' + escHtml(p.video_url) + '" download style="padding:5px 12px;background:#0f172a;color:#fff;border-radius:6px;font-size:11px;text-decoration:none">Download mp4</a>' +
        '<button onclick="scRenderVideo(\'' + escHtml(p.id) + '\',this)" style="padding:5px 12px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Opnieuw renderen</button>' +
        '</div>';
    } else {
      html += '<button onclick="scRenderVideo(\'' + escHtml(p.id) + '\',this)" style="padding:6px 14px;background:#e5a500;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">🎬 Render video</button>' +
        '<span style="font-size:11px;color:#94a3b8;margin-left:8px">Merk-slides + Nederlandse voice-over — ~30 sec</span>';
    }
    html += '</div>';
    html += '<div style="margin-top:12px"><button onclick="document.getElementById(\'sc-detail\').innerHTML=\'\'" style="padding:5px 14px;background:#fff;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Sluiten</button></div></div>';
    box.innerHTML = html;
    box.scrollIntoView({ behavior:'smooth', block:'nearest' });
  } catch (e) {
    box.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
  }
}

function scCopyText(btn) {
  var txt = btn.getAttribute('data-text') || '';
  navigator.clipboard.writeText(txt).then(function(){ socialToast('📋 Gekopieerd'); });
}

async function scRenderVideo(id, btn) {
  btn.disabled = true; var orig = btn.textContent; btn.textContent = 'Renderen… (~30 sec)';
  try {
    var resp = await fetch('/api/social-content/packs/' + encodeURIComponent(id) + '/render-video', { method:'POST' });
    var d = await resp.json();
    if (resp.ok && d.success) {
      socialToast('🎬 Video klaar — ' + (d.scenes || '?') + ' scènes, ' + Math.round(d.duration || 0) + 's');
      scOpenPack(id); // herlaad detail → toont de <video>-preview
    } else {
      alert('❌ ' + (d.error || d.detail || 'renderen mislukt')); btn.textContent = orig; btn.disabled = false;
    }
  } catch (e) { alert('❌ ' + e.message); btn.textContent = orig; btn.disabled = false; }
}


