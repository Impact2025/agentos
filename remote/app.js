/* Iris Remote — vanilla SPA in de glass/iris-blauw designtaal. Praat alleen met /api/ui.
 * Upgrade: realtime polling, skeleton-loaders, toasts, pull-to-refresh, retry op fouten. */
(() => {
  const $ = (id) => document.getElementById(id);
  let items = [];
  let polling = false;
  let loadToken = 0; // breekt verouderde async loads bij tab-wissel

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

  // ── Toasts ────────────────────────────────────────────────────────────────
  function toast(msg, kind = '', icon) {
    const host = $('toast-host');
    if (!host) return;
    const ic = icon || (kind === 'ok' ? 'check_circle' : kind === 'err' ? 'error' : 'info');
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<span class="material-symbols-outlined">${ic}</span><span>${esc(msg)}</span>`;
    host.appendChild(el);
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 320);
    }, 2800);
  }

  // ── Sync-status pill (header) ─────────────────────────────────────────────
  function setSyncPill(lastPush) {
    const pill = $('sync-pill');
    const txt = $('sync-pill-txt');
    if (!pill || !txt) return;
    if (!lastPush) { pill.className = 'dead'; txt.textContent = 'NOOIT GESYNCED'; return; }
    const age = Math.round((Date.now() - new Date(lastPush)) / 60000);
    if (age < 3) { pill.className = 'fresh'; txt.textContent = 'LIVE'; }
    else if (age < 15) { pill.className = 'fresh'; txt.textContent = `${age}m geleden`; }
    else if (age < 60) { pill.className = 'recent'; txt.textContent = `${age}m geleden`; }
    else if (age < 180) { pill.className = 'stale'; txt.textContent = `${age}m — machine uit?`; }
    else { pill.className = 'dead'; txt.textContent = `${Math.round(age / 60)}u offline`; }
  }

  function spinSync(on) {
    const icon = $('syncTrigger')?.querySelector('.material-symbols-outlined');
    if (icon) icon.classList.toggle('animate-spin', on);
  }

  // ── Views + nav ──────────────────────────────────────────────────────────
  const views = ['login', 'inbox', 'briefing', 'note', 'system'];
  function show(view) {
    const myToken = ++loadToken;
    views.forEach((v) => { $(`view-${v}`).hidden = v !== view; });
    document.querySelectorAll('.nav-btn').forEach((b) =>
      b.classList.toggle('nav-active', b.dataset.view === view));
    if (view === 'inbox') refresh(myToken);
    if (view === 'briefing') loadBriefing(myToken);
    if (view === 'note') loadNotes();
    if (view === 'system') loadSystem(myToken);
  }

  document.querySelectorAll('.nav-btn').forEach((b) => { b.onclick = () => show(b.dataset.view); });

  $('syncTrigger').onclick = async () => {
    spinSync(true);
    const active = views.find((v) => !$(`view-${v}`).hidden) || 'inbox';
    await refreshRaw();
    if (active === 'inbox') refresh();
    else if (active === 'briefing') loadBriefing();
    else if (active === 'system') loadSystem();
    spinSync(false);
  };

  $('login-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api('login', 'POST', { password: $('login-pw').value });
      $('login-error').textContent = '';
      toast('Welkom terug', 'ok', 'lock_open');
      show('inbox');
    } catch (err) { $('login-error').textContent = err.message; }
  };

  $('logoutBtn').onclick = async () => { await api('logout', 'POST', {}); toast('Uitgelogd'); show('login'); };

  // ── Realtime polling ──────────────────────────────────────────────────────
  async function refreshRaw() {
    try {
      const data = await api('items');
      return data;
    } catch (e) { if (e.message !== 'login') console.warn('refreshRaw', e); return null; }
  }

  async function refresh(token = loadToken) {
    const data = await refreshRaw();
    if (!data || token !== loadToken) return;
    items = data.items || [];
    setSyncPill(data.last_push);
    renderItems();
    const open = items.filter((i) => !i.decision_status || i.decision_status === 'failed').length;
    $('inbox-sub').textContent = open === 1 ? '1 besluit in de wachtrij' : `${open} besluiten in de wachtrij`;
  }

  // ── Actiecentrum ──────────────────────────────────────────────────────────
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
    // Bij een gefaald besluit met een nutteloze 'Onbekende fout' als result,
    // toon liever de echte oorzaak uit summary (die is menselijk leesbaar).
    let failedTxt = it.decision_result;
    if ((!failedTxt || failedTxt === 'Onbekende fout') && it.summary) {
      failedTxt = it.summary.length > 60 ? it.summary.slice(0, 57) + '…' : it.summary;
    }
    const map = {
      pending: ['schedule', 'bdg pending', 'Wacht op AgentOS-sync'],
      applied: ['check_circle', 'bdg applied', it.decision_result || 'Uitgevoerd'],
      failed: ['warning', 'bdg failed', failedTxt || 'Mislukt — tik voor details'],
    };
    const [icon, cls, txt] = map[it.decision_status] || map.pending;
    return `<div class="${cls}">
      <span class="material-symbols-outlined text-[16px]">${icon}</span>
      <span class="font-body-md text-body-md">${esc(txt)}</span></div>`;
  }

  function skeletons(n = 3) {
    return Array.from({ length: n }).map(() =>
      `<div class="sk-card p-4 space-y-3"><div class="flex gap-4"><div class="skeleton" style="width:48px;height:48px;border-radius:12px"></div><div class="flex-1 space-y-2"><div class="skeleton" style="height:12px;width:40%"></div><div class="skeleton" style="height:16px;width:80%"></div></div></div><div class="skeleton" style="height:48px;width:100%"></div></div>`
    ).join('');
  }

  let firstLoad = true;
  function renderItems() {
    const el = $('items');
    if (firstLoad) { el.innerHTML = skeletons(3); firstLoad = false; return; }
    if (!items.length) {
      el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center fade-up">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">task_alt</span>
        <p class="font-body-lg text-body-lg text-on-surface-variant">Niets wacht op je. <span class="text-on-surface">Alles afgehandeld.</span></p></div>`;
      return;
    }
    el.innerHTML = `<div class="stagger">${items.map((it, idx) => {
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
    }).join('')}</div>`;
    el.querySelectorAll('.item').forEach((card) => { card.onclick = () => openDetail(items[card.dataset.idx]); });
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
    if (it.dismiss_kind === 'error') {
      return `<div class="bg-error-container/20 border border-error/30 rounded-lg p-3 mt-3">
        <p class="font-body-md text-body-md text-error">${esc(it.summary || 'Er ging iets mis bij dit item.')}</p>
        ${it.decision_result ? `<p class="font-body-md text-body-md text-on-surface-variant mt-2">Uitslag: ${esc(it.decision_result)}</p>` : ''}
      </div>`;
    }
    return `<p class="font-body-md text-body-md text-on-surface-variant mt-3">${esc(it.summary || '')}</p>`;
  }

  function payloadFor(it, action) {
    const p = {};
    if (it.dismiss_kind === 'mail' && action === 'edit') { const t = $('edit-text'); if (t) p.text = t.value; }
    if (it.dismiss_kind === 'outreach' && action === 'approve') {
      const s = $('edit-subject'); const t = $('edit-text');
      if (s) p.subject = s.value; if (t) p.body = t.value;
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
        const status = $('detail-status');
        const action = btn.dataset.action;
        try {
          const res = await api('decide', 'POST', {
            item_key: it.key, action, payload: payloadFor(it, action),
          });
          if (res.queued) {
            status.innerHTML = '<span class="text-primary">✓ Besluit vastgelegd — AgentOS voert het uit bij de volgende sync.</span>';
            toast('Besluit vastgelegd', 'ok', 'check_circle');
          } else {
            status.innerHTML = '<span class="text-warn">Al vastgelegd — staat nog in de wachtrij.</span>';
          }
          setTimeout(() => { closeDetail(); refresh(); }, 900);
        } catch (e) {
          status.innerHTML = `<span class="text-error">Fout: ${esc(e.message)}</span>`;
          toast(e.message, 'err', 'error');
          btn.disabled = false;
        }
      };
    });
  }
  function closeDetail() { $('detail-overlay').hidden = true; }
  $('detail-overlay').onclick = (e) => { if (e.target.id === 'detail-overlay') closeDetail(); };

  // ── Briefings — dashboard ────────────────────────────────────────────────
  function deltaChip(delta, unit, invert = false) {
    if (delta === null || delta === undefined) return `<span class="font-label-caps text-[11px] text-on-surface-variant/60">— geen historie</span>`;
    const good = invert ? delta < 0 : delta > 0;
    const neutral = delta === 0;
    const cls = neutral ? 'text-on-surface-variant' : good ? 'text-green-400' : 'text-error';
    const arrow = neutral ? '·' : delta > 0 ? '▲' : '▼';
    const abs = Math.abs(delta);
    const val = abs % 1 === 0 ? abs : abs.toFixed(1);
    const u = abs === 1 ? unit.replace('clicks', 'click') : unit;
    return `<span class="font-label-caps text-[11px] ${cls} whitespace-nowrap">${arrow} ${val} ${esc(u)}</span>`;
  }
  function statTile(label, value, sub) {
    return `<div class="glass-panel rounded-xl p-4">
      <p class="font-label-caps text-label-caps text-on-surface-variant uppercase mb-1">${esc(label)}</p>
      <p class="text-[28px] leading-8 font-bold text-on-surface tracking-tight">${value}</p>
      <div class="mt-1 min-h-[16px]">${sub || ''}</div>
    </div>`;
  }
  function sparkline(series, id) {
    if (!series || series.length < 2) return '<p class="font-label-caps text-[11px] text-on-surface-variant/60 mt-3">Nog geen GSC-dagreeks</p>';
    const vals = series.map((d) => d[1]);
    if (!vals.some((v) => v > 0)) return `<p class="font-label-caps text-[11px] text-on-surface-variant/60 mt-3">Geen clicks in ${series.length} dagen</p>`;
    const W = 320, H = 56, PAD = 4, LABEL_W = 30;
    const max = Math.max(1, ...vals);
    const x = (i) => PAD + (i / (series.length - 1)) * (W - PAD * 2 - LABEL_W);
    const y = (v) => H - PAD - (v / max) * (H - PAD * 2);
    const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const line = `M${pts.join(' L')}`;
    const area = `${line} L${x(vals.length - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;
    const li = vals.length - 1;
    return `<div class="relative mt-3" data-spark="${id}">
      <svg viewBox="0 0 ${W} ${H}" class="w-full h-14 block" preserveAspectRatio="none" aria-label="Clicks per dag, laatste ${series.length} dagen">
        <path d="${area}" fill="rgba(142,213,255,0.12)"></path>
        <path d="${line}" fill="none" stroke="#8ed5ff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
        <circle cx="${x(li).toFixed(1)}" cy="${y(vals[li]).toFixed(1)}" r="3.5" fill="#8ed5ff" stroke="#101415" stroke-width="2"></circle>
        <text x="${(x(li) + 7).toFixed(1)}" y="${Math.min(H - 6, Math.max(11, y(vals[li]) + 4)).toFixed(1)}" fill="#e0e3e5" font-size="11" font-family="JetBrains Mono, monospace">${vals[li]}</text>
      </svg>
      <div class="spark-tip absolute pointer-events-none bg-surface-container-high border border-white/10 rounded px-2 py-1 font-label-caps text-[10px] text-on-surface z-10 whitespace-nowrap" style="display:none"></div>
    </div>`;
  }
  function bindSparkTips(root, seriesById) {
    root.querySelectorAll('[data-spark]').forEach((wrap) => {
      const series = seriesById[wrap.dataset.spark];
      if (!series || series.length < 2) return;
      const tip = wrap.querySelector('.spark-tip');
      const svg = wrap.querySelector('svg');
      const move = (ev) => {
        const r = svg.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
        const i = Math.round(frac * (series.length - 1));
        const [date, clicks] = series[i];
        tip.textContent = `${fmtDay(date)} · ${clicks} ${clicks === 1 ? 'click' : 'clicks'}`;
        tip.style.display = 'block';
        const left = Math.min(r.width - tip.offsetWidth - 4, Math.max(0, frac * r.width - tip.offsetWidth / 2));
        tip.style.left = `${left}px`; tip.style.top = '-22px';
      };
      wrap.addEventListener('pointermove', move);
      wrap.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
    });
  }
  function fmtDay(iso) {
    try { return new Date(iso).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' }); } catch { return iso; }
  }
  const PILLAR_MAX = { content: 25, seo: 35, uitvoering: 20, hygiene: 20 };
  const PILLAR_LABEL = { content: 'Content', seo: 'SEO', uitvoering: 'Uitvoering', hygiene: 'Hygiëne' };
  function pillarBars(pillars) {
    return `<div class="grid grid-cols-4 gap-2 mt-3">${Object.keys(PILLAR_MAX).map((k) => {
      const v = pillars?.[k];
      const pct = v == null ? 0 : Math.round((v / PILLAR_MAX[k]) * 100);
      return `<div>
        <div class="h-1.5 w-full bg-surface-variant/60 rounded-full overflow-hidden">
          <div class="h-full bg-primary/70 rounded-full" style="width:${pct}%"></div>
        </div>
        <p class="font-label-caps text-[9px] text-on-surface-variant/70 mt-1 truncate">${PILLAR_LABEL[k]} ${v == null ? '–' : Math.round(v)}</p>
      </div>`;
    }).join('')}</div>`;
  }
  function gradeBadge(grade) {
    const g = Number(grade) || 0;
    const cls = g >= 7 ? 'text-green-400 border-green-400/30' : g >= 5.5 ? 'text-primary border-primary/30' : 'text-error border-error/30';
    return `<div class="w-14 h-14 shrink-0 rounded-xl border ${cls} bg-surface-container-lowest/60 flex flex-col items-center justify-center">
      <span class="text-[20px] font-bold leading-none">${g.toFixed(1)}</span>
      <span class="font-label-caps text-[8px] text-on-surface-variant mt-0.5">CIJFER</span>
    </div>`;
  }
  function projectCard(p, series) {
    const t = p.trend || {}, seo = p.seo || {};
    return `<div class="glass-panel rounded-xl p-4 fade-up">
      <div class="flex items-start gap-3">
        ${gradeBadge(p.grade)}
        <div class="min-w-0 flex-1">
          <div class="flex items-center justify-between gap-2">
            <h3 class="font-headline-sm text-[16px] text-on-surface truncate">${esc(p.project)}</h3>
            <span class="font-label-caps text-[10px] text-on-surface-variant/60 shrink-0">${seo.pages ?? 0} pag.</span>
          </div>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
            ${deltaChip(t.delta_clicks, 'clicks/wk')}
            ${deltaChip(t.delta_position, 'positie', true)}
          </div>
          <div class="flex flex-wrap gap-x-4 gap-y-0.5 mt-2 font-label-caps text-[10px] text-on-surface-variant">
            <span>${seo.clicks ?? 0} clicks · 30d</span>
            ${seo.avg_position ? `<span>pos ${seo.avg_position}</span>` : ''}
            ${seo.ctr_pct != null ? `<span>CTR ${seo.ctr_pct}%</span>` : ''}
          </div>
        </div>
      </div>
      ${sparkline(series, p.site_id)}
      ${pillarBars(p.pillars)}
      ${p.oordeel ? `<p class="font-body-md text-[13px] text-on-surface-variant leading-snug mt-3 border-t border-white/5 pt-3">${esc(p.oordeel)}</p>` : ''}
    </div>`;
  }
  const FUNNEL_STAGES = [['contacted', 'Benaderd'], ['replied', 'Reactie'], ['call', 'Gesprek'], ['won', 'Klant']];
  function funnelPanel(f) {
    const reached = f.reached || {};
    const max = Math.max(1, ...FUNNEL_STAGES.map(([k]) => reached[k] || 0));
    return `<div class="glass-panel rounded-xl p-4 fade-up">
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-label-caps text-label-caps text-primary uppercase">Acquisitie-funnel</h3>
        <span class="font-label-caps text-[10px] text-on-surface-variant">${f.total_leads ?? 0} leads totaal</span>
      </div>
      <div class="space-y-2.5">${FUNNEL_STAGES.map(([k, label]) => {
        const v = reached[k] || 0;
        return `<div class="flex items-center gap-3">
          <span class="font-body-md text-[13px] text-on-surface-variant w-20 shrink-0">${label}</span>
          <div class="h-2 flex-1 bg-surface-variant/60 rounded-full overflow-hidden">
            <div class="h-full bg-primary rounded-full" style="width:${Math.max(v ? 3 : 0, Math.round((v / max) * 100))}%"></div>
          </div>
          <span class="font-label-caps text-[11px] text-on-surface w-8 text-right shrink-0">${v}</span>
        </div>`;
      }).join('')}</div>
      ${f.formula ? `<p class="font-body-md text-[12px] text-on-surface-variant mt-3 border-t border-white/5 pt-3">${esc(f.formula)}</p>` : ''}
    </div>`;
  }
  function advicePanel(advice) {
    const items = (advice || []).map((a) => typeof a === 'string'
      ? { actie: a, waarom: '' }
      : { actie: (a && (a.actie || a.advies || a.text || a.titel)) || '', waarom: (a && a.waarom) || '' })
      .filter((a) => a.actie).slice(0, 5);
    if (!items.length) return '';
    return `<div class="glass-panel rounded-xl p-4 fade-up">
      <h3 class="font-label-caps text-label-caps text-primary uppercase mb-3">Beste stappen vandaag</h3>
      <ol class="space-y-3">${items.map((a, i) => `
        <li class="flex gap-3">
          <span class="w-6 h-6 shrink-0 rounded-full bg-primary-container/20 border border-primary/20 flex items-center justify-center font-label-caps text-[11px] text-primary">${i + 1}</span>
          <div class="min-w-0">
            <p class="font-body-md text-[13px] text-on-surface leading-snug">${esc(a.actie)}</p>
            ${a.waarom ? `<p class="font-body-md text-[12px] text-on-surface-variant/70 leading-snug mt-1">${esc(a.waarom)}</p>` : ''}
          </div>
        </li>`).join('')}</ol>
    </div>`;
  }
  async function loadBriefing(token = loadToken) {
    const el = $('briefing');
    if (token === loadToken) el.innerHTML = '<div class="glass-panel rounded-xl p-6 font-body-md text-on-surface-variant">Laden…</div>';
    try {
      const data = await api('briefing');
      if (token !== loadToken) return;
      const p = data.payload || {};
      const projects = p.projects || [];
      const seriesById = p.series || {};
      let html = '';
      if (p.iris) {
        html += `<div class="flex items-center justify-between">
          <p class="font-label-caps text-label-caps text-on-surface-variant uppercase">Briefing ${esc(p.iris.date || '')}</p>
          ${p.iris.llm_ok === false ? '<span class="font-label-caps text-[10px] text-error border border-error/30 rounded px-2 py-0.5">TERUGVAL — alleen cijfers</span>' : ''}
        </div>`;
      }
      if (projects.length) {
        const withTrend = projects.filter((x) => x.trend && x.trend.last7);
        const clicks7 = withTrend.reduce((s, x) => s + (x.trend.last7.clicks || 0), 0);
        const dClicks = withTrend.some((x) => x.trend.delta_clicks != null) ? withTrend.reduce((s, x) => s + (x.trend.delta_clicks || 0), 0) : null;
        const positions = withTrend.map((x) => x.trend.last7.avg_position).filter((v) => v != null);
        const avgPos = positions.length ? positions.reduce((a, b) => a + b, 0) / positions.length : null;
        const dPos = withTrend.map((x) => x.trend.delta_position).filter((v) => v != null);
        const avgDPos = dPos.length ? dPos.reduce((a, b) => a + b, 0) / dPos.length : null;
        const tr = p.track_record || {};
        html += `<div class="grid grid-cols-2 gap-3">
          ${statTile('Clicks · 7d', clicks7, deltaChip(dClicks, 'vs vorige wk'))}
          ${statTile('Gem. positie', avgPos == null ? '–' : avgPos.toFixed(1), deltaChip(avgDPos, 'vs vorige wk', true))}
          ${statTile('Iris-trefkans', tr.accuracy == null ? '–' : `${tr.accuracy}%`, `<span class="font-label-caps text-[11px] text-on-surface-variant">${tr.correct ?? 0} raak · ${tr.wrong ?? 0} mis · ${tr.open ?? 0} open</span>`)}
          ${statTile('Leads', (p.funnel || {}).total_leads ?? '–', `<span class="font-label-caps text-[11px] text-on-surface-variant">${((p.funnel || {}).reached || {}).contacted ?? 0} benaderd</span>`)}
        </div>`;
        if (p.iris) html += advicePanel(p.iris.advice);
        html += projects.map((x) => projectCard(x, seriesById[x.site_id])).join('');
      }
      if (p.funnel) html += funnelPanel(p.funnel);
      if (p.iris && p.iris.markdown) {
        html += `<details class="glass-panel rounded-xl">
          <summary class="p-4 cursor-pointer font-label-caps text-label-caps text-primary uppercase select-none">Volledige briefing van Iris</summary>
          <div class="markdown font-body-md text-body-md text-on-surface-variant leading-relaxed px-4 pb-4">${mdLite(p.iris.markdown)}</div>
        </details>`;
      }
      if (!projects.length && !html && p.iris) {
        html = `<div class="glass-panel rounded-xl p-gutter">
          <div class="markdown font-body-md text-body-md text-on-surface-variant leading-relaxed">${mdLite(p.iris.markdown || '')}</div></div>`;
      }
      el.innerHTML = html || `<div class="glass-panel rounded-xl p-10 text-center fade-up">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">auto_awesome</span>
        <p class="font-body-lg text-body-lg text-on-surface-variant">Nog geen briefing gesynchroniseerd.</p></div>`;
      bindSparkTips(el, seriesById);
    } catch (e) {
      if (e.message === 'login') return;
      el.innerHTML = `<div class="glass-panel rounded-xl p-6 fade-up text-error font-body-md">
        Kon de briefing niet laden: ${esc(e.message)}<br>
        <button class="mt-3 bg-primary text-on-primary px-4 py-2 rounded-lg font-headline-sm" onclick="__retryBriefing()">Opnieuw proberen</button></div>`;
    }
  }
  window.__retryBriefing = () => loadBriefing();

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
  $('note-text').addEventListener('input', () => { $('charCount').textContent = `${$('note-text').value.length} tekens`; });
  $('saveBtn').onclick = async () => {
    const text = $('note-text').value.trim();
    if (!text) return;
    const btn = $('saveBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-outlined animate-spin">progress_activity</span> Syncen...';
    try {
      await api('note', 'POST', { text });
      btn.innerHTML = '<span class="material-symbols-outlined">check_circle</span> Opgeslagen';
      $('note-text').value = ''; $('charCount').textContent = '0 tekens';
      toast('Notitie klaargezet voor sync', 'ok', 'cloud_done');
      loadNotes();
    } catch (e) {
      btn.innerHTML = '<span class="material-symbols-outlined">error</span> Fout';
      toast(e.message, 'err', 'error');
    }
    setTimeout(() => { btn.innerHTML = '<span class="material-symbols-outlined">cloud_upload</span> Opslaan voor sync'; btn.disabled = false; }, 1600);
  };
  async function loadNotes() {
    try {
      const data = await api('notes');
      const el = $('notes-list');
      const notes = data.notes || [];
      el.innerHTML = notes.length ? notes.map((n) => `
        <div class="glass-panel rounded-lg p-4 group hover:border-primary/40 transition-colors fade-up">
          <div class="flex justify-between items-start mb-2">
            <span class="font-label-caps text-label-caps text-on-surface-variant">${esc(fmtDate(n.created_at))}</span>
            <span class="font-label-caps text-label-caps ${n.status === 'synced' ? 'text-green-400' : 'text-primary'}">${n.status === 'synced' ? 'GESYNCT' : 'PENDING'}</span>
          </div>
          <p class="font-body-md text-body-md text-on-surface line-clamp-2">${esc(n.text)}</p>
        </div>`).join('')
        : `<p class="font-body-md text-body-md text-on-surface-variant">Nog geen notities.</p>`;
    } catch (e) { /* login afgehandeld */ }
  }

  // ── Systeem ──────────────────────────────────────────────────────────────
  async function loadSystem(token = loadToken) {
    try {
      const [itemsData, outboxData] = await Promise.all([api('items'), api('outbox')]);
      if (token !== loadToken) return;
      const lastPush = itemsData.last_push ? new Date(itemsData.last_push) : null;
      const age = lastPush ? Math.round((Date.now() - lastPush) / 60000) : null;
      const online = age !== null && age < 10;
      setSyncPill(itemsData.last_push);
      $('system-status').innerHTML = `
        <div class="glass-panel rounded-xl p-6 border-l-4 ${online ? 'border-l-primary' : 'border-l-error'} fade-up">
          <div class="flex items-start justify-between mb-4">
            <div>
              <h3 class="font-headline-sm text-headline-sm">Sync status</h3>
              <div class="flex items-center gap-2 mt-1">
                <span class="status-dot ${online ? 'pulse' : ''}" ${online ? '' : 'style="background:#ffb4ab"'}'></span>
                <p class="font-body-md text-body-md text-on-surface-variant">Lokale AgentOS-bridge:
                  <span class="${online ? 'text-primary' : 'text-error'} font-medium">${online ? 'Online' : 'Offline / uit'}</span></p>
              </div>
            </div>
            <span class="font-label-caps text-label-caps text-on-surface-variant bg-surface-variant/50 px-3 py-1 rounded-full">${online ? 'ACTIVE' : 'QUEUED'}</span>
          </div>
          <div class="flex items-center justify-between pt-4 border-t border-white/5">
            <p class="font-body-md text-body-md text-outline">Laatste sync: ${age === null ? 'nooit' : age < 1 ? 'zojuist' : age + ' min geleden'}</p>
            ${!online ? '<span class="font-label-caps text-label-caps text-warn">⚠ besluiten wachten op je machine</span>' : ''}
          </div>
        </div>`;
      const dec = outboxData.decisions || [];
      const pending = dec.filter((d) => d.status === 'pending').length;
      $('outbox-count').textContent = `${pending} PENDING`;
      const ICON = { pending: 'schedule', applied: 'check_circle', failed: 'warning' };
      $('outbox-list').innerHTML = dec.length ? dec.map((d) => `
        <div class="glass-panel rounded-lg p-4 flex items-center justify-between fade-up">
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
      ? `<div class="flex justify-end fade-up"><div class="bg-primary/15 border border-primary/20 rounded-xl rounded-br-sm px-3 py-2 max-w-[85%] font-body-md text-body-md">${esc(m.content)}</div></div>`
      : `<div class="flex justify-start fade-up"><div class="bg-surface-container-lowest/60 border border-white/5 rounded-xl rounded-bl-sm px-3 py-2 max-w-[85%] font-body-md text-body-md text-on-surface-variant">${mdLite(m.content)}</div></div>`).join('')
      + (pending ? '<div class="flex justify-start"><div class="px-3 py-2 text-primary font-label-caps text-label-caps animate-pulse">IRIS DENKT NA…</div></div>' : '');
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
      const r = await fetch('/api/iris', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages: chatHistory.slice(-12) }) });
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
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(key) });
      await api('push-subscribe', 'POST', sub.toJSON());
      status.innerHTML = '<span class="text-primary">✓ Meldingen staan aan op dit apparaat.</span>';
    } catch (e) { status.textContent = `Fout: ${e.message}`; }
  };
  if ('serviceWorker' in navigator && Notification.permission === 'granted') navigator.serviceWorker.register('/sw.js').catch(() => {});

  // ── Pull-to-refresh (mobiel) ─────────────────────────────────────────────
  let ptrStartY = 0, ptrPull = 0, ptrActive = false;
  const ptr = $('ptr');
  const main = document.querySelector('main');
  if (main) {
    main.addEventListener('touchstart', (e) => {
      if (main.scrollTop > 0 || ptrActive) return;
      ptrStartY = e.touches[0].clientY; ptrActive = true;
    }, { passive: true });
    main.addEventListener('touchmove', (e) => {
      if (!ptrActive) return;
      ptrPull = Math.max(0, e.touches[0].clientY - ptrStartY);
      if (ptrPull > 0) { ptr.style.transform = `translateY(${Math.min(ptrPull, 64) - 40}px)`; }
    }, { passive: true });
    main.addEventListener('touchend', async () => {
      if (!ptrActive) return;
      ptrActive = false;
      if (ptrPull > 56) {
        ptr.classList.add('spinning'); ptr.style.transform = 'translateY(0)';
        const active = views.find((v) => !$(`view-${v}`).hidden) || 'inbox';
        await refreshRaw();
        if (active === 'inbox') refresh(); else if (active === 'briefing') loadBriefing(); else if (active === 'system') loadSystem();
        setTimeout(() => { ptr.classList.remove('spinning'); ptr.style.transform = 'translateY(-64px)'; }, 500);
      } else { ptr.style.transform = 'translateY(-64px)'; }
      ptrPull = 0;
    });
  }

  // ── Start + realtime loop ────────────────────────────────────────────────
  function startPolling() {
    if (polling) return;
    polling = true;
    setInterval(async () => {
      const hidden = document.visibilityState === 'hidden';
      const onInbox = !$('view-inbox').hidden;
      const onSystem = !$('view-system').hidden;
      if (hidden) return; // batterij/CPU besparen in achtergrond
      const data = await refreshRaw();
      if (!data) return;
      setSyncPill(data.last_push);
      if (onInbox) { items = data.items || []; renderItems(); }
      else if (onSystem) { const [, out] = await Promise.all([api('items'), api('outbox')]); loadSystem(); }
    }, 20000);
  }
  show('inbox');
  startPolling();
})();
