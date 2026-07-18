/* Iris Remote — vanilla SPA in de glass/iris-blauw designtaal. Praat alleen met /api/ui. */
(() => {
  const $ = (id) => document.getElementById(id);
  let items = [];

  // ── API ──────────────────────────────────────────────────────────────────
  async function api(op, method = 'GET', body = null) {
    const r = await fetch(`/api/ui?op=${op}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : null,
    });
    if (r.status === 401) { show('login'); throw new Error('login'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  function fmtDate(v) {
    if (!v) return '';
    try {
      return new Date(v).toLocaleString('nl-NL', {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      });
    } catch { return String(v).slice(0, 16).replace('T', ' '); }
  }

  // ── Views + nav ──────────────────────────────────────────────────────────
  const views = ['login', 'inbox', 'briefing', 'note', 'system'];
  function show(view) {
    views.forEach((v) => { $(`view-${v}`).hidden = v !== view; });
    document.querySelectorAll('.nav-btn').forEach((b) =>
      b.classList.toggle('nav-active', b.dataset.view === view));
    if (view === 'inbox') refresh();
    if (view === 'briefing') loadBriefing();
    if (view === 'note') loadNotes();
    if (view === 'system') loadSystem();
  }
  document.querySelectorAll('.nav-btn').forEach((b) => {
    b.onclick = () => show(b.dataset.view);
  });

  $('syncTrigger').onclick = () => {
    const icon = $('syncTrigger').querySelector('.material-symbols-outlined');
    icon.classList.add('animate-spin');
    setTimeout(() => icon.classList.remove('animate-spin'), 1000);
    const active = views.find((v) => !$(`view-${v}`).hidden) || 'inbox';
    show(active);
  };

  $('login-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api('login', 'POST', { password: $('login-pw').value });
      $('login-error').textContent = '';
      show('inbox');
    } catch (err) { $('login-error').textContent = err.message; }
  };

  $('logoutBtn').onclick = async () => { await api('logout', 'POST', {}); show('login'); };

  // ── Actiecentrum ─────────────────────────────────────────────────────────
  const KIND_META = {
    content: { icon: 'article', label: 'Wachtrij · Artikel' },
    mail: { icon: 'mail', label: 'Helpdesk · Mail' },
    outreach: { icon: 'alternate_email', label: 'Outreach' },
    calendar: { icon: 'calendar_month', label: 'Agenda-voorstel' },
    goal: { icon: 'flag', label: 'Doel' },
    task: { icon: 'task_alt', label: 'Taak' },
    error: { icon: 'error', label: 'Fout' },
    vacancies: { icon: 'work', label: 'Opdrachten' },
    leads: { icon: 'group_add', label: 'Leads' },
    linkbuilding: { icon: 'link', label: 'Linkbuilding' },
  };

  function decisionBadge(it) {
    if (!it.decision_status) return '';
    const map = {
      pending: ['schedule', 'text-primary', 'Wacht op AgentOS-sync'],
      applied: ['check_circle', 'text-green-400', it.decision_result || 'Uitgevoerd'],
      failed: ['warning', 'text-error', it.decision_result || 'Mislukt'],
    };
    const [icon, cls, txt] = map[it.decision_status] || map.pending;
    return `<div class="flex items-center gap-2 pt-1 ${cls}">
      <span class="material-symbols-outlined text-[16px]">${icon}</span>
      <span class="font-body-md text-body-md">${esc(txt)}</span></div>`;
  }

  async function refresh() {
    try {
      const data = await api('items');
      items = data.items || [];
      renderItems();
      const open = items.filter((i) => !i.decision_status || i.decision_status === 'failed').length;
      $('inbox-sub').textContent = open === 1
        ? '1 besluit in de wachtrij' : `${open} besluiten in de wachtrij`;
      if (data.last_push) {
        const age = Math.round((Date.now() - new Date(data.last_push)) / 60000);
        $('sync-info').textContent = age < 10
          ? `Laatste sync: ${age < 1 ? 'zojuist' : age + ' min geleden'}`
          : `⚠ Laatste sync ${age} min geleden — staat je machine uit? Besluiten worden dan later uitgevoerd.`;
      } else {
        $('sync-info').textContent = 'Nog geen sync ontvangen van AgentOS.';
      }
    } catch (e) { /* login-view is al getoond bij 401 */ }
  }

  function renderItems() {
    const el = $('items');
    if (!items.length) {
      el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">task_alt</span>
        <p class="font-body-lg text-body-lg text-on-surface-variant">Niets wacht op je. 🎉</p></div>`;
      return;
    }
    el.innerHTML = items.map((it, idx) => {
      const m = KIND_META[it.dismiss_kind] || { icon: 'radio_button_unchecked', label: it.dismiss_kind };
      return `
      <div class="glass-panel p-stack-md rounded-xl space-y-3 cursor-pointer hover:border-primary/40 transition-colors group item" data-idx="${idx}">
        <div class="flex justify-between items-start gap-3">
          <div class="flex gap-4 min-w-0">
            <div class="w-12 h-12 shrink-0 rounded-lg bg-primary-container/20 flex items-center justify-center border border-primary/20">
              <span class="material-symbols-outlined text-primary">${m.icon}</span>
            </div>
            <div class="min-w-0">
              <p class="font-label-caps text-label-caps text-primary mb-1 uppercase">${esc(m.label)}${it.project ? ' · ' + esc(it.project) : ''}</p>
              <h3 class="font-headline-sm text-headline-sm text-on-surface leading-snug">${esc(it.title)}</h3>
            </div>
          </div>
          <span class="font-label-caps text-label-caps text-on-surface-variant shrink-0">${esc(fmtDate(it.created_at))}</span>
        </div>
        <div class="bg-surface-container-lowest/50 rounded-lg p-3 border border-white/5 font-body-md text-body-md text-on-surface-variant leading-relaxed">
          ${esc(it.summary || '')}
        </div>
        ${decisionBadge(it)}
      </div>`;
    }).join('');
    el.querySelectorAll('.item').forEach((card) => {
      card.onclick = () => openDetail(items[card.dataset.idx]);
    });
  }

  // ── Detail bottom-sheet + acties ─────────────────────────────────────────
  const ACTION_LABELS = {
    approve: { content: 'Goedkeuren & publiceren', outreach: 'Versturen', calendar: 'Goedkeuren' },
    send: { mail: 'Versturen' },
    reject: { content: 'Afwijzen', mail: 'Afwijzen', outreach: 'Afwijzen (→ lost)', calendar: 'Afwijzen' },
    edit: { mail: 'Bewerking opslaan' },
  };
  const ACTIONS_PER_KIND = {
    content: ['approve', 'reject'], mail: ['send', 'edit', 'reject'],
    outreach: ['approve', 'reject'], calendar: ['approve', 'reject'],
  };
  const inputCls = 'w-full bg-[#020617] border-none rounded-lg p-4 text-on-surface font-body-md focus:ring-2 focus:ring-primary/50 mt-2';

  function detailHtml(it) {
    const d = it.detail || {};
    if (it.dismiss_kind === 'content') {
      return `
        <p class="font-body-md text-body-md text-on-surface-variant mt-2">Zoekwoord: <b class="text-on-surface">${esc(d.keyword || '?')}</b>
          · SEO-score: <b class="text-primary">${d.seo_score ?? '?'}</b>/100</p>
        <iframe class="preview-frame mt-3" sandbox srcdoc="${esc(d.blog_html || '<p>Geen preview</p>')}"></iframe>`;
    }
    if (it.dismiss_kind === 'mail') {
      return `
        <details class="mt-3" open>
          <summary class="font-label-caps text-label-caps text-primary cursor-pointer">VRAAG VAN ${esc((d.from_name || d.from_addr || '?').toUpperCase())}</summary>
          <pre class="whitespace-pre-wrap break-words bg-surface-container-lowest/50 border border-white/5 rounded-lg p-3 font-body-md text-body-md text-on-surface-variant mt-2">${esc(d.question_body || '')}</pre>
        </details>
        <p class="font-body-md text-body-md text-on-surface-variant mt-3">Antwoord aan <b class="text-on-surface">${esc(d.to_addr || '?')}</b> — bewerk gerust vóór versturen:</p>
        <textarea id="edit-text" rows="10" class="${inputCls}">${esc(d.draft_body || '')}</textarea>`;
    }
    if (it.dismiss_kind === 'outreach') {
      return `
        <p class="font-body-md text-body-md text-on-surface-variant mt-2">Aan: <b class="text-on-surface">${esc(d.target_email || '?')}</b> (${esc(d.org_name || '')})</p>
        <input id="edit-subject" value="${esc(d.subject || '')}" class="${inputCls}"/>
        <textarea id="edit-text" rows="10" class="${inputCls}">${esc(d.body || '')}</textarea>`;
    }
    if (it.dismiss_kind === 'calendar') {
      return `
        <div class="bg-surface-container-lowest/50 rounded-lg p-3 border border-white/5 mt-3 space-y-1">
          <div class="flex items-center gap-2 text-on-surface-variant">
            <span class="material-symbols-outlined text-[16px]">schedule</span>
            <span class="font-body-md text-body-md">${esc(String(d.proposed_start || '').slice(0, 16).replace('T', ' '))} – ${esc(String(d.proposed_end || '').slice(11, 16))}</span>
          </div>
          <div class="flex items-center gap-2 text-on-surface-variant">
            <span class="material-symbols-outlined text-[16px]">location_on</span>
            <span class="font-body-md text-body-md">${esc(d.location || 'geen locatie')}</span>
          </div>
          <div class="flex items-center gap-2 text-on-surface-variant">
            <span class="material-symbols-outlined text-[16px]">verified</span>
            <span class="font-body-md text-body-md">Conflict-check: ${esc(d.conflict_checked || '?')}</span>
          </div>
        </div>
        <pre class="whitespace-pre-wrap break-words font-body-md text-body-md text-on-surface-variant mt-3">${esc(d.rationale || '')}</pre>`;
    }
    return `<p class="font-body-md text-body-md text-on-surface-variant mt-3">${esc(it.summary || '')}</p>`;
  }

  function payloadFor(it, action) {
    const p = {};
    if (it.dismiss_kind === 'mail' && action === 'edit') {
      const t = $('edit-text'); if (t) p.text = t.value;
    }
    if (it.dismiss_kind === 'outreach' && action === 'approve') {
      const s = $('edit-subject'); const t = $('edit-text');
      if (s) p.subject = s.value;
      if (t) p.body = t.value;
    }
    return p;
  }

  function btnCls(a) {
    if (a === 'reject') return 'flex-1 bg-transparent border border-white/10 text-error font-headline-sm text-sm py-3 rounded-lg font-semibold hover:bg-white/5 active:scale-[0.98] transition-all';
    if (a === 'dismiss' || a === 'edit') return 'flex-1 bg-transparent border border-white/10 text-on-surface font-headline-sm text-sm py-3 rounded-lg font-semibold hover:bg-white/5 active:scale-[0.98] transition-all';
    return 'flex-1 bg-primary text-on-primary font-headline-sm text-sm py-3 rounded-lg font-semibold hover:opacity-90 active:scale-[0.98] transition-all iris-glow';
  }

  function openDetail(it) {
    const m = KIND_META[it.dismiss_kind] || { icon: 'radio_button_unchecked', label: it.dismiss_kind };
    const acts = (ACTIONS_PER_KIND[it.dismiss_kind] || []).concat(['dismiss']);
    $('detail-card').innerHTML = `
      <div class="flex justify-between items-start gap-3">
        <div>
          <p class="font-label-caps text-label-caps text-primary uppercase mb-1">${esc(m.label)}</p>
          <h2 class="font-headline-sm text-headline-sm">${esc(it.title)}</h2>
        </div>
        <button id="detail-close" class="text-on-surface-variant hover:text-primary shrink-0">
          <span class="material-symbols-outlined">close</span>
        </button>
      </div>
      ${decisionBadge(it)}
      ${detailHtml(it)}
      <div class="flex flex-wrap gap-stack-sm pt-4">
        ${acts.map((a) => `<button class="${btnCls(a)}" data-action="${a}">
          ${ACTION_LABELS[a]?.[it.dismiss_kind] || (a === 'dismiss' ? 'Wegklikken' : a)}</button>`).join('')}
      </div>
      <p id="detail-status" class="font-body-md text-body-md text-on-surface-variant mt-3"></p>`;
    $('detail-overlay').hidden = false;
    $('detail-close').onclick = closeDetail;
    document.querySelectorAll('#detail-card [data-action]').forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await api('decide', 'POST', {
            item_key: it.key, action: btn.dataset.action,
            payload: payloadFor(it, btn.dataset.action),
          });
          $('detail-status').innerHTML = '<span class="text-primary">✓ Besluit vastgelegd — AgentOS voert het uit bij de volgende sync.</span>';
          setTimeout(() => { closeDetail(); refresh(); }, 900);
        } catch (e) {
          $('detail-status').textContent = `Fout: ${e.message}`;
          btn.disabled = false;
        }
      };
    });
  }
  function closeDetail() { $('detail-overlay').hidden = true; }
  $('detail-overlay').onclick = (e) => { if (e.target.id === 'detail-overlay') closeDetail(); };

  // ── Briefings ────────────────────────────────────────────────────────────
  async function loadBriefing() {
    const el = $('briefing');
    el.innerHTML = '<div class="glass-panel rounded-xl p-6 font-body-md text-on-surface-variant">Laden…</div>';
    try {
      const data = await api('briefing');
      const p = data.payload || {};
      let html = '';
      if (p.funnel) {
        const stages = Object.entries(p.funnel).filter(([, v]) => typeof v === 'number');
        const max = Math.max(1, ...stages.map(([, v]) => v));
        html += `<div class="glass-panel rounded-xl p-gutter">
          <h3 class="font-label-caps text-label-caps text-primary mb-stack-md">ACQUISITIE-FUNNEL</h3>
          <div class="space-y-4">${stages.map(([k, v]) => `
            <div class="space-y-2">
              <div class="flex justify-between items-end">
                <span class="font-body-md text-body-md capitalize">${esc(k.replace(/_/g, ' '))}</span>
                <span class="font-label-caps text-label-caps text-primary">${v}</span>
              </div>
              <div class="h-2 w-full bg-surface-variant rounded-full overflow-hidden">
                <div class="h-full bg-primary iris-glow" style="width:${Math.max(3, Math.round((v / max) * 100))}%"></div>
              </div>
            </div>`).join('')}
          </div></div>`;
      }
      if (p.iris) {
        html += `<div class="glass-panel rounded-xl p-gutter">
          <div class="flex items-center justify-between mb-stack-md">
            <h3 class="font-label-caps text-label-caps text-primary">IRIS — ${esc(p.iris.date || '')}</h3>
            ${p.iris.llm_ok ? '' : '<span class="font-label-caps text-label-caps text-error">TERUGVAL</span>'}
          </div>
          <div class="markdown font-body-md text-body-md text-on-surface-variant leading-relaxed">${mdLite(p.iris.markdown || '')}</div>
        </div>`;
      }
      el.innerHTML = html || `<div class="glass-panel rounded-xl p-10 text-center">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">auto_awesome</span>
        <p class="font-body-lg text-on-surface-variant">Nog geen briefing gesynchroniseerd.</p></div>`;
    } catch (e) {
      el.innerHTML = `<div class="glass-panel rounded-xl p-6 text-error font-body-md">${esc(e.message)}</div>`;
    }
  }

  // Mini-markdown: koppen, bold, bullets — genoeg voor de briefing.
  function mdLite(md) {
    return esc(md)
      .replace(/^### (.*)$/gm, '<h4 class="font-headline-sm text-[16px] text-on-surface mt-4 mb-1">$1</h4>')
      .replace(/^## (.*)$/gm, '<h3 class="font-headline-sm text-headline-sm text-on-surface mt-5 mb-1">$1</h3>')
      .replace(/^# (.*)$/gm, '<h3 class="font-headline-sm text-headline-sm text-on-surface mt-5 mb-1">$1</h3>')
      .replace(/\*\*(.+?)\*\*/g, '<b class="text-on-surface">$1</b>')
      .replace(/^[-*] (.*)$/gm, '<li class="ml-4">$1</li>')
      .replace(/\n{2,}/g, '<br><br>');
  }

  // ── Notities ─────────────────────────────────────────────────────────────
  $('note-text').addEventListener('input', () => {
    $('charCount').textContent = `${$('note-text').value.length} tekens`;
  });
  $('saveBtn').onclick = async () => {
    const text = $('note-text').value.trim();
    if (!text) return;
    const btn = $('saveBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-outlined animate-spin">progress_activity</span> Syncen...';
    try {
      await api('note', 'POST', { text });
      btn.innerHTML = '<span class="material-symbols-outlined">check_circle</span> Opgeslagen';
      $('note-text').value = '';
      $('charCount').textContent = '0 tekens';
      loadNotes();
    } catch (e) {
      btn.innerHTML = '<span class="material-symbols-outlined">error</span> Fout';
    }
    setTimeout(() => {
      btn.innerHTML = '<span class="material-symbols-outlined">cloud_upload</span> Opslaan voor sync';
      btn.disabled = false;
    }, 1600);
  };

  async function loadNotes() {
    try {
      const data = await api('notes');
      const el = $('notes-list');
      const notes = data.notes || [];
      el.innerHTML = notes.length ? notes.map((n) => `
        <div class="glass-panel rounded-lg p-4 group hover:border-primary/40 transition-colors">
          <div class="flex justify-between items-start mb-2">
            <span class="font-label-caps text-label-caps text-on-surface-variant">${esc(fmtDate(n.created_at))}</span>
            <span class="font-label-caps text-label-caps ${n.status === 'synced' ? 'text-green-400' : 'text-primary'}">${n.status === 'synced' ? 'GESYNCT' : 'PENDING'}</span>
          </div>
          <p class="font-body-md text-body-md text-on-surface line-clamp-2">${esc(n.text)}</p>
        </div>`).join('')
        : '<p class="font-body-md text-body-md text-on-surface-variant">Nog geen notities.</p>';
    } catch (e) { /* login afgehandeld */ }
  }

  // ── Systeem ──────────────────────────────────────────────────────────────
  async function loadSystem() {
    try {
      const [itemsData, outboxData] = await Promise.all([api('items'), api('outbox')]);
      const lastPush = itemsData.last_push ? new Date(itemsData.last_push) : null;
      const age = lastPush ? Math.round((Date.now() - lastPush) / 60000) : null;
      const online = age !== null && age < 10;
      $('system-status').innerHTML = `
        <div class="glass-panel rounded-xl p-6 border-l-4 ${online ? 'border-l-primary' : 'border-l-error'}">
          <div class="flex items-start justify-between mb-4">
            <div>
              <h3 class="font-headline-sm text-headline-sm">Sync status</h3>
              <div class="flex items-center gap-2 mt-1">
                <span class="status-dot ${online ? 'pulse' : ''}" ${online ? '' : 'style="background:#ffb4ab"'}></span>
                <p class="font-body-md text-body-md text-on-surface-variant">Lokale AgentOS-bridge:
                  <span class="${online ? 'text-primary' : 'text-error'} font-medium">${online ? 'Online' : 'Offline / uit'}</span></p>
              </div>
            </div>
            <span class="font-label-caps text-label-caps text-on-surface-variant bg-surface-variant/50 px-3 py-1 rounded-full">${online ? 'ACTIVE' : 'QUEUED'}</span>
          </div>
          <div class="flex items-center justify-between pt-4 border-t border-white/5">
            <p class="font-body-md text-body-md text-outline">Laatste sync: ${age === null ? 'nooit' : age < 1 ? 'zojuist' : age + ' min geleden'}</p>
          </div>
        </div>`;
      const dec = outboxData.decisions || [];
      const pending = dec.filter((d) => d.status === 'pending').length;
      $('outbox-count').textContent = `${pending} PENDING`;
      const ICON = { pending: 'schedule', applied: 'check_circle', failed: 'warning' };
      $('outbox-list').innerHTML = dec.length ? dec.map((d) => `
        <div class="glass-panel rounded-lg p-4 flex items-center justify-between">
          <div class="flex items-center gap-4 min-w-0">
            <div class="w-10 h-10 shrink-0 rounded-full bg-surface-variant flex items-center justify-center">
              <span class="material-symbols-outlined ${d.status === 'failed' ? 'text-error' : d.status === 'applied' ? 'text-green-400' : 'text-on-surface-variant'}">${ICON[d.status] || 'schedule'}</span>
            </div>
            <div class="min-w-0">
              <p class="font-body-lg text-body-lg text-on-surface truncate">${esc(d.action)} · ${esc(d.item_kind)} · ${esc(d.item_key)}</p>
              <p class="font-body-md text-body-md text-on-surface-variant truncate">${d.status === 'pending' ? 'Wacht op dispatch…' : esc(d.result || d.status)}</p>
            </div>
          </div>
        </div>`).join('')
        : '<p class="font-body-md text-body-md text-on-surface-variant">Nog geen besluiten.</p>';
    } catch (e) { /* login afgehandeld */ }
  }

  // ── Cloud-Iris chat ──────────────────────────────────────────────────────
  const chatHistory = [];

  function renderChat(pending = false) {
    const el = $('chat-messages');
    el.innerHTML = chatHistory.map((m) => m.role === 'user'
      ? `<div class="flex justify-end"><div class="bg-primary/15 border border-primary/20 rounded-xl rounded-br-sm px-3 py-2 max-w-[85%] font-body-md text-body-md">${esc(m.content)}</div></div>`
      : `<div class="flex justify-start"><div class="bg-surface-container-lowest/60 border border-white/5 rounded-xl rounded-bl-sm px-3 py-2 max-w-[85%] font-body-md text-body-md text-on-surface-variant">${mdLite(m.content)}</div></div>`
    ).join('') + (pending
      ? '<div class="flex justify-start"><div class="px-3 py-2 text-primary font-label-caps text-label-caps animate-pulse">IRIS DENKT NA…</div></div>' : '');
    el.scrollTop = el.scrollHeight;
  }

  $('chat-form').onsubmit = async (e) => {
    e.preventDefault();
    const input = $('chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    chatHistory.push({ role: 'user', content: text });
    renderChat(true);
    $('chat-send').disabled = true;
    try {
      const r = await fetch('/api/iris', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: chatHistory.slice(-12) }),
      });
      if (r.status === 401) { show('login'); return; }
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      chatHistory.push({ role: 'assistant', content: data.reply });
    } catch (err) {
      chatHistory.push({ role: 'assistant', content: `⚠ ${err.message}` });
    }
    $('chat-send').disabled = false;
    renderChat();
  };

  // ── Web-push (Systeem-tab) ───────────────────────────────────────────────
  function b64ToUint8(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  $('notifBtn').onclick = async () => {
    const status = $('notif-status');
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        status.textContent = 'Deze browser ondersteunt geen web-push. Op iPhone: eerst "Zet op beginscherm" en open de app vanaf daar.';
        return;
      }
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { status.textContent = 'Toestemming geweigerd.'; return; }
      const { key } = await api('vapid');
      if (!key) { status.textContent = 'VAPID-keys niet gezet in de Vercel-env (zie README).'; return; }
      const reg = await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: b64ToUint8(key),
      });
      await api('push-subscribe', 'POST', sub.toJSON());
      status.innerHTML = '<span class="text-primary">✓ Meldingen staan aan op dit apparaat.</span>';
    } catch (e) {
      status.textContent = `Fout: ${e.message}`;
    }
  };

  if ('serviceWorker' in navigator && Notification.permission === 'granted') {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // ── Start ────────────────────────────────────────────────────────────────
  show('inbox');
  setInterval(() => { if (!$('view-inbox').hidden) refresh(); }, 60000);
})();
