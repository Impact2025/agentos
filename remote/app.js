/* Iris Remote — vanilla SPA in de glass/iris-blauw designtaal. Praat alleen met /api/ui.
 * Upgrade: realtime polling, skeleton-loaders, toasts, pull-to-refresh, retry op fouten. */
(() => {
  const $ = (id) => document.getElementById(id);
  let items = [];
  let polling = false;
  let loadToken = 0; // breekt verouderde async loads bij tab-wissel
  let lastPushAt = null; // laatste geslaagde bridge-sync — ook het notitie-ophaalmoment

  // ── API ──────────────────────────────────────────────────────────────────
  async function api(op, method = 'GET', body = null) {
    const r = await fetch(`/api/ui?op=${op}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : null,
    });
    const data = await r.json().catch(() => ({}));
    // Een 401 op 'login' is een fout wachtwoord en moet zijn eigen melding
    // houden; een 401 elders betekent dat de sessie verlopen of ingetrokken is.
    if (r.status === 401 && op !== 'login') { show('login'); throw new Error('login'); }
    if (!r.ok) {
      const err = new Error(data.error || `HTTP ${r.status}`);
      err.status = r.status;
      err.retryAfter = data.retry_after || 0;
      throw err;
    }
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
    lastPushAt = lastPush;
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
    setStaleness(lastPush, age);
  }

  // Een pilletje in de kopregel is te weinig waarschuwing: 32 kaarten van een
  // week oud zien er exact zo uit als 32 verse. Bij een oude push zeggen we in
  // de lijst zélf hoe oud de stand is en dat besluiten pas landen zodra AgentOS
  // weer draait — die blijven namelijk gewoon werken, ze wachten alleen.
  function setStaleness(lastPush, age) {
    const dot = document.querySelector('#view-inbox .status-dot');
    const info = $('sync-info');
    const old = !lastPush || age >= 60;
    if (dot) dot.classList.toggle('offline', old);
    if (dot) dot.classList.toggle('pulse', !old);
    if (!info) return;
    if (!old) { info.textContent = ''; info.hidden = true; return; }
    info.hidden = false;
    info.className = 'font-body-md text-body-md stale-note';
    info.textContent = lastPush
      ? `Bevroren stand van ${fmtDate(lastPush)} — AgentOS synct niet. Je besluiten blijven in de wachtrij staan en worden uitgevoerd zodra de verbinding terug is.`
      : 'Nog nooit gesynchroniseerd — AgentOS heeft deze cloud nog niet bereikt.';
  }

  function spinSync(on) {
    const icon = $('syncTrigger')?.querySelector('.material-symbols-outlined');
    if (icon) icon.classList.toggle('animate-spin', on);
  }

  // ── Views + nav ──────────────────────────────────────────────────────────
  const views = ['login', 'today', 'inbox', 'briefing', 'note', 'system'];

  // Historie: op Android is de terugknop (of de veeg vanaf de rand) het eerste
  // wat iemand probeert. Zonder deze koppeling sloot dat de hele app af in
  // plaats van het sheet of de vorige tab — inclusief een half ingevulde
  // notitie. Elke view krijgt daarom een history-entry en het bottom-sheet een
  // extra bovenop. Wat we bewust NIET doen is de gebruiker vasthouden: vanaf
  // 'today' (de startview) verlaat terug gewoon de app, zoals het hoort.
  let navReady = false;
  function pushView(view) {
    if (view === 'login') return;             // sessie verlopen is geen navigatie
    if (!navReady) { history.replaceState({ view }, ''); navReady = true; return; }
    const st = history.state || {};
    if (st.view === view && !st.sheet) return; // dezelfde tab nogmaals: geen entry
    history.pushState({ view }, '');
  }

  function currentView() {
    return views.find((v) => !$(`view-${v}`).hidden) || 'today';
  }

  window.addEventListener('popstate', (e) => {
    // Staat het sheet open, dan hoort terug dát te sluiten en niets anders.
    if (!$('detail-overlay').hidden) { hideDetail(); return; }
    const target = (e.state && e.state.view) || 'today';
    if (target !== currentView()) show(target, false);
  });

  function show(view, push = true) {
    const myToken = ++loadToken;
    if (push) pushView(view);
    views.forEach((v) => { $(`view-${v}`).hidden = v !== view; });
    document.querySelectorAll('.nav-btn').forEach((b) => {
      const on = b.dataset.view === view;
      b.classList.toggle('nav-active', on);
      b.setAttribute('aria-current', on ? 'page' : 'false');
    });
    // Vandaag leunt op de itemtelling ('3 besluiten wachten op je'), dus die
    // halen we mee op — anders staat er bij een koude start altijd 0.
    if (view === 'today') { refresh(myToken).then(() => loadToday(myToken)); }
    if (view === 'inbox') refresh(myToken);
    if (view === 'briefing') loadBriefing(myToken);
    if (view === 'note') loadNotes();
    if (view === 'system') loadSystem(myToken);
  }

  // Herlaad het actieve scherm — gebruikt door de sync-knop en pull-to-refresh.
  async function reloadActive() {
    const active = views.find((v) => !$(`view-${v}`).hidden) || 'today';
    await refreshRaw();
    if (active === 'today') { await refresh(); loadToday(); }
    else if (active === 'inbox') refresh();
    else if (active === 'briefing') loadBriefing();
    else if (active === 'note') loadNotes();
    else if (active === 'system') loadSystem();
  }

  document.querySelectorAll('.nav-btn').forEach((b) => { b.onclick = () => show(b.dataset.view); });

  $('syncTrigger').onclick = async () => {
    spinSync(true);
    await reloadActive();
    spinSync(false);
  };

  let lockTimer = null;
  function lockLogin(seconds) {
    clearInterval(lockTimer);
    const btn = $('login-form').querySelector('button[type=submit]');
    const tick = () => {
      if (seconds <= 0) {
        clearInterval(lockTimer);
        btn.disabled = false;
        $('login-error').textContent = 'Je kunt het opnieuw proberen.';
        return;
      }
      btn.disabled = true;
      const m = Math.floor(seconds / 60);
      const s = seconds % 60;
      $('login-error').textContent = `Te veel pogingen — wacht nog ${m ? `${m}m ` : ''}${s}s.`;
      seconds -= 1;
    };
    tick();
    lockTimer = setInterval(tick, 1000);
  }

  $('login-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api('login', 'POST', { password: $('login-pw').value });
      clearInterval(lockTimer);
      $('login-pw').value = '';
      $('login-error').textContent = '';
      toast('Welkom terug', 'ok', 'lock_open');
      show('today');
    } catch (err) {
      if (err.retryAfter > 0) lockLogin(err.retryAfter);
      else $('login-error').textContent = err.message;
    }
  };

  $('logoutBtn').onclick = async () => { await api('logout', 'POST', {}); toast('Uitgelogd'); show('login'); };

  // Logout-icoon rechtsboven in de hoofdheader — snelle uitweg zonder naar
  // het Systeem-scherm te gaan. Bevestiging voorkomt een onbedoelde klik.
  $('logoutIconBtn').onclick = async () => {
    if (!confirm('Uitloggen uit Iris Remote?')) return;
    try {
      await api('logout', 'POST', {});
      toast('Uitgelogd', 'ok', 'logout');
      show('login');
    } catch (e) { if (e.message !== 'login') toast(e.message, 'err'); }
  };

  // "Hi 👋"-knop in de chat-header: korte, persoonlijke teruggroet van Iris
  // (lokaal gescript — geen backend-call nodig, het is een begroeting).
  $('chat-hi').onclick = () => { irisHi(); };

  // ── Realtime polling ──────────────────────────────────────────────────────
  async function refreshRaw() {
    try {
      const data = await api('items');
      return data;
    } catch (e) { if (e.message !== 'login') console.warn('refreshRaw', e); return null; }
  }

  async function refresh(token = loadToken) {
    // Skeleton hoort vóór de fetch, niet erna: hem tonen mét de data al in de
    // hand liet de kop "32 besluiten in de wachtrij" boven drie grijze blokken
    // staan, tot de volgende poll-tick 20s later alsnog rendeerde.
    if (!items.length && !$('items').firstElementChild) $('items').innerHTML = skeletons(3);
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
    personal_mail: { icon: 'mark_email_read', label: 'Postvak · Concept' },
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

  // Groep + prioriteit. Mislukte besluiten en fouten horen bovenaan, niet op
  // datum tussen 14 identieke wachtrij-kaarten.
  // Mail heeft een eigen groep (niet 'actie'): een klaarstaand conceptantwoord
  // was alleen te vinden door tussen de artikelen in de Wachtrij te scrollen,
  // en de enige ingang vanaf Vandaag verdween zodra de urgent-lijst leeg was.
  const GROUP_OF = {
    content: 'actie', outreach: 'actie', calendar: 'actie',
    mail: 'mail', personal_mail: 'mail',
    error: 'fout',
  };
  const GROUPS = [
    ['all', 'Alles', 'inbox'],
    ['fout', 'Fouten', 'error'],
    ['mail', 'Mail', 'mail'],
    ['actie', 'Wachtrij', 'pending_actions'],
    ['info', 'Info', 'info'],
  ];
  const groupOf = (it) => GROUP_OF[it.dismiss_kind] || 'info';
  function rankOf(it) {
    if (it.decision_status === 'failed') return 0;
    if (it.dismiss_kind === 'error') return 1;
    // Mail telt hier als 'actie': een eigen filtergroep mag niet betekenen dat
    // een wachtend antwoord onder de artikelen zakt.
    if (['actie', 'mail'].includes(groupOf(it))) return it.decision_status === 'pending' ? 3 : 2;
    return 4;
  }
  let inboxFilter = 'all';

  function renderFilters() {
    const host = $('inbox-filters');
    if (!host) return;
    const counts = { all: items.length, fout: 0, mail: 0, actie: 0, info: 0 };
    items.forEach((it) => { counts[groupOf(it)] += 1; });
    host.innerHTML = GROUPS.filter(([k]) => k === 'all' || counts[k]).map(([k, label, icon]) => {
      const on = inboxFilter === k;
      return `<button class="chip ${on ? 'chip-on' : ''} shrink-0" data-filter="${k}">
        <span class="material-symbols-outlined text-[16px]">${icon}</span>
        <span>${label}</span><span class="chip-count">${counts[k]}</span></button>`;
    }).join('');
    host.querySelectorAll('[data-filter]').forEach((b) => {
      b.onclick = () => { inboxFilter = b.dataset.filter; renderFilters(); renderItems(); };
    });
  }

  // "Artikel klaar (SEO 82.0/100) — goedkeuren publiceert echt." staat bij elk
  // artikel identiek in de samenvatting; de score wordt een chip, de rest weg.
  function summaryBits(it) {
    const s = it.summary || '';
    const m = s.match(/SEO\s+([\d.]+)\s*\/\s*100/i);
    if (!m) return { chip: '', text: s };
    const score = parseFloat(m[1]);
    const cls = score >= 85 ? 'text-green-400 border-green-400/30' : 'text-primary border-primary/30';
    return {
      chip: `<span class="font-label-caps text-[10px] ${cls} border rounded px-1.5 py-0.5 shrink-0">SEO ${score}</span>`,
      text: s.replace(/^[^—]*—\s*/, '').trim(),
    };
  }

  function renderItems() {
    const el = $('items');
    renderFilters();
    if (!items.length) {
      el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center fade-up">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">task_alt</span>
        <p class="font-body-lg text-body-lg text-on-surface-variant">Niets wacht op je. <span class="text-on-surface">Alles afgehandeld.</span></p></div>`;
      return;
    }
    const view = items
      .map((it, idx) => ({ it, idx }))
      .filter(({ it }) => inboxFilter === 'all' || groupOf(it) === inboxFilter)
      .sort((a, b) => rankOf(a.it) - rankOf(b.it)
        || String(b.it.created_at || '').localeCompare(String(a.it.created_at || '')));

    if (!view.length) {
      el.innerHTML = `<div class="glass-panel rounded-xl p-8 text-center">
        <p class="font-body-md text-body-md text-on-surface-variant">Niets in deze categorie.</p></div>`;
      return;
    }

    el.innerHTML = `<div class="stagger space-y-stack-sm">${view.map(({ it, idx }) => {
      const m = KIND_META[it.dismiss_kind] || { icon: 'radio_button_unchecked', label: it.dismiss_kind };
      const { chip, text } = summaryBits(it);
      const urgent = it.decision_status === 'failed' || it.dismiss_kind === 'error';
      const quick = it.dismiss_kind === 'content' && !it.decision_status;
      return `
      <div class="glass-panel p-3 rounded-xl cursor-pointer hover:border-primary/40 transition-colors item ${urgent ? 'border-l-4 border-l-error/70' : ''}" data-idx="${idx}">
        <div class="flex gap-3 min-w-0">
          <div class="w-9 h-9 shrink-0 rounded-lg bg-primary-container/20 flex items-center justify-center border border-primary/20">
            <span class="material-symbols-outlined text-primary text-[20px]">${m.icon}</span>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 mb-0.5">
              <p class="font-label-caps text-[10px] text-primary uppercase truncate min-w-0">${esc(m.label)}${it.project ? ' · ' + esc(it.project) : ''}</p>
              ${chip}
              <span class="font-label-caps text-[10px] text-on-surface-variant/60 ml-auto shrink-0">${esc(fmtDate(it.created_at))}</span>
            </div>
            <h3 class="font-headline-sm text-[15px] leading-snug text-on-surface line-clamp-2">${esc(it.title)}</h3>
            ${text ? `<p class="font-body-md text-[12px] text-on-surface-variant/80 leading-snug mt-1 line-clamp-2">${esc(text)}</p>` : ''}
            ${decisionBadge(it)}
          </div>
        </div>
        ${quick ? `<div class="flex gap-2 mt-2.5 pt-2.5 border-t border-white/5">
          <button class="quick flex-1 bg-primary/90 text-on-primary font-headline-sm text-[13px] py-2 rounded-lg" data-quick="approve" data-idx="${idx}">Goedkeuren</button>
          <button class="quick px-4 border border-white/10 text-error font-headline-sm text-[13px] py-2 rounded-lg" data-quick="reject" data-idx="${idx}">Afwijzen</button>
          <button class="quick px-3 border border-white/10 text-on-surface-variant rounded-lg" data-open="${idx}" title="Preview">
            <span class="material-symbols-outlined text-[18px]">visibility</span></button>
        </div>` : ''}
      </div>`;
    }).join('')}</div>`;

    el.querySelectorAll('.item').forEach((card) => {
      card.onclick = (e) => {
        if (e.target.closest('.quick')) return; // knoppen hebben hun eigen handler
        openDetail(items[card.dataset.idx]);
      };
    });
    el.querySelectorAll('[data-open]').forEach((b) => {
      b.onclick = () => openDetail(items[b.dataset.open]);
    });
    el.querySelectorAll('[data-quick]').forEach((btn) => {
      btn.onclick = async () => {
        const it = items[btn.dataset.idx];
        const action = btn.dataset.quick;
        btn.disabled = true;
        btn.textContent = '…';
        try {
          await api('decide', 'POST', { item_key: it.key, action, payload: {} });
          toast(action === 'approve' ? 'Goedkeuring vastgelegd' : 'Afwijzing vastgelegd', 'ok', 'check_circle');
          refresh();
        } catch (e) {
          toast(e.message, 'err', 'error');
          btn.disabled = false;
          btn.textContent = action === 'approve' ? 'Goedkeuren' : 'Afwijzen';
        }
      };
    });
  }

  // ── Detail bottom-sheet + acties ─────────────────────────────────────────
  const ACTION_LABELS = {
    approve: { content: 'Goedkeuren & publiceren', outreach: 'Versturen', calendar: 'Goedkeuren' },
    send: { mail: 'Versturen', personal_mail: 'Versturen' },
    reject: { content: 'Afwijzen', mail: 'Afwijzen', personal_mail: 'Afwijzen', outreach: 'Afwijzen (→ lost)', calendar: 'Afwijzen' },
    edit: { mail: 'Bewerking opslaan' },
  };
  const ACTIONS_PER_KIND = {
    content: ['approve', 'reject'], mail: ['send', 'edit', 'reject'],
    personal_mail: ['send', 'reject'],
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
    if (it.dismiss_kind === 'personal_mail') {
      return `
        <p class="font-body-md text-body-md text-on-surface-variant mt-2">Van <b class="text-on-surface">${esc(d.from_name || d.from_addr || '?')}</b> — ${esc(d.subject || '')}</p>
        ${d.ai_summary ? `<p class="font-body-md text-[12px] text-on-surface-variant/70 mt-1">${esc(d.ai_summary)}</p>` : ''}
        <p class="font-body-md text-body-md text-on-surface-variant mt-3">Iris' conceptantwoord — bewerk gerust vóór versturen:</p>
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
    if (it.dismiss_kind === 'personal_mail' && action === 'send') {
      const t = $('edit-text'); if (t) p.text = t.value;
    }
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
      <div class="sheet-grabber" aria-hidden="true"></div>
      <div class="flex justify-between items-start gap-3">
        <div>
          <p class="font-label-caps text-label-caps text-primary uppercase mb-1">${esc(m.label)}</p>
          <h2 id="detail-title" class="font-headline-sm text-headline-sm">${esc(it.title)}</h2>
        </div>
        <button id="detail-close" class="tap-target text-on-surface-variant hover:text-primary shrink-0"
          aria-label="Sluiten">
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
    showDetail();
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
  // ── Bottom-sheet: openen, sluiten, wegvegen ──────────────────────────────
  //
  // Het sheet is de plek waar besluiten vallen, dus het moet zich gedragen als
  // een sheet en niet als een div die toevallig onderaan staat:
  //   • de achtergrond scrollt niet mee (op iOS anders gegarandeerd);
  //   • toetsenbord-focus blijft binnen het sheet, Escape sluit;
  //   • terug (Android) sluit het sheet, niet de app;
  //   • omlaag vegen sluit het — mits de inhoud al bovenaan staat, anders
  //     vecht het gebaar met het scrollen van een lange artikelpreview.
  let lastFocus = null;

  function showDetail() {
    const ov = $('detail-overlay');
    lastFocus = document.activeElement;
    lockScroll(true);
    ov.hidden = false;
    // display:none → flex en de opacity-overgang in dezelfde stijlberekening
    // betekent géén overgang; dit is waarom de slide-in nooit draaide. Een
    // requestAnimationFrame is hier niet genoeg — de browser mag beide
    // wijzigingen alsnog samenvatten (gemeten: opacity al op 1 na 60ms van een
    // overgang van 280ms). Een afgedwongen reflow legt de startwaarden wél vast.
    void ov.offsetWidth;
    ov.classList.add('open');
    history.pushState({ view: currentView(), sheet: true }, '');
    setTimeout(() => $('detail-close')?.focus(), 60);
  }

  // Sluit écht (opruimen + animatie). Wordt aangeroepen vanuit popstate.
  function hideDetail() {
    const ov = $('detail-overlay');
    if (ov.hidden) return;
    ov.classList.remove('open');
    const card = $('detail-card');
    card.classList.remove('dragging');
    card.style.transform = '';
    setTimeout(() => {
      ov.hidden = true;
      lockScroll(false);
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
      lastFocus = null;
    }, 280);
  }

  // Sluit via de knop, de backdrop, Escape of een veeg: we lopen bewust langs
  // history.back(), zodat de sheet-entry uit de stack verdwijnt. Deed hij dat
  // niet, dan moest je na tien besluiten tien keer terug om de app te verlaten.
  function closeDetail() {
    if (history.state && history.state.sheet) history.back();
    else hideDetail();
  }

  function lockScroll(on) {
    const b = document.body;
    if (on) {
      b.style.setProperty('--scroll-lock-top', `-${window.scrollY}px`);
      b.classList.add('sheet-open');
    } else {
      const y = parseInt(b.style.getPropertyValue('--scroll-lock-top') || '0', 10);
      b.classList.remove('sheet-open');
      b.style.removeProperty('--scroll-lock-top');
      window.scrollTo(0, Math.abs(y));
    }
  }

  $('detail-overlay').onclick = (e) => { if (e.target.id === 'detail-overlay') closeDetail(); };

  document.addEventListener('keydown', (e) => {
    if ($('detail-overlay').hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); closeDetail(); return; }
    if (e.key !== 'Tab') return;
    // Focus-trap: zonder dit tabt een toetsenbordgebruiker achter het sheet
    // langs door een lijst die hij niet ziet, en drukt daar op 'Publiceren'.
    const f = [...$('detail-card').querySelectorAll(
      'button:not([disabled]), input, textarea, select, a[href], [tabindex]:not([tabindex="-1"])')]
      .filter((el) => el.offsetParent !== null);
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  // Swipe-to-dismiss.
  (() => {
    const card = $('detail-card');
    let y0 = 0, dy = 0, dragging = false;
    card.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || card.scrollTop > 0) return;
      y0 = e.touches[0].clientY; dy = 0; dragging = true;
      card.classList.add('dragging');
    }, { passive: true });
    card.addEventListener('touchmove', (e) => {
      if (!dragging) return;
      dy = e.touches[0].clientY - y0;
      // Omhoog vegen is scrollen, niet sluiten: dan het gebaar teruggeven.
      if (dy < 0) { dy = 0; dragging = false; card.classList.remove('dragging'); card.style.transform = ''; return; }
      card.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    card.addEventListener('touchend', () => {
      if (!dragging) return;
      dragging = false;
      card.classList.remove('dragging');
      // 96px is ruim boven een onbedoeld schokje en ruim onder een halve veeg.
      if (dy > 96) { card.style.transform = ''; closeDetail(); }
      else card.style.transform = '';
    });
  })();

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
  // Compacte regel voor de middenmoot: cijfer + naam + twee delta-chips.
  // 11 volle kaarten onder elkaar is de tweede overzichtskiller; alleen de
  // projecten die aandacht vragen krijgen de volledige kaart.
  function projectRow(p, idx) {
    const t = p.trend || {};
    const g = Number(p.grade) || 0;
    const cls = g >= 7 ? 'text-green-400' : g >= 5.5 ? 'text-primary' : 'text-error';
    return `<button class="proj-row w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/5 transition-colors" data-proj="${idx}" aria-expanded="false">
      <span class="font-label-caps text-[13px] font-bold ${cls} w-8 shrink-0">${g.toFixed(1)}</span>
      <span class="font-body-md text-[14px] text-on-surface truncate flex-1 min-w-0">${esc(p.project)}</span>
      <span class="flex items-center gap-2 shrink-0">
        ${deltaChip(t.delta_clicks, 'clicks')}
        ${deltaChip(t.delta_position, 'pos', true)}
      </span>
      <span class="material-symbols-outlined text-[18px] text-on-surface-variant/50 shrink-0">expand_more</span>
    </button>`;
  }

  function projectsPanel(projects, seriesById) {
    // Zwakste eerst: dat is waar de aandacht heen moet.
    const sorted = [...projects].sort((a, b) => (Number(a.grade) || 0) - (Number(b.grade) || 0));
    const FULL = 3;
    const full = sorted.slice(0, FULL);
    const rest = sorted.slice(FULL);
    let html = `<div class="flex items-center justify-between mt-2">
      <h3 class="font-label-caps text-label-caps text-primary uppercase">Vraagt aandacht</h3>
      <span class="font-label-caps text-[10px] text-on-surface-variant">${sorted.length} projecten</span>
    </div>`;
    html += full.map((x) => projectCard(x, seriesById[x.site_id])).join('');
    if (rest.length) {
      html += `<div class="glass-panel rounded-xl p-2 fade-up">
        <p class="font-label-caps text-label-caps text-on-surface-variant uppercase px-3 pt-2 pb-1">Overige projecten</p>
        <div class="divide-y divide-white/5">${rest.map((p, i) => `
          <div>${projectRow(p, i)}
            <div class="proj-detail px-1 pb-2" data-detail="${i}" hidden></div>
          </div>`).join('')}</div>
      </div>`;
    }
    return { html, rest };
  }

  function bottleneckPanel(list) {
    const items = (list || []).filter((b) => b && b.actie).slice(0, 3);
    if (!items.length) return '';
    const [first, ...others] = items;
    return `<div class="glass-panel rounded-xl p-4 fade-up border-l-4 border-l-error/70">
      <div class="flex items-center gap-2 mb-2">
        <span class="material-symbols-outlined text-error text-[18px]">priority_high</span>
        <h3 class="font-label-caps text-label-caps text-error uppercase">Probleem nummer één</h3>
      </div>
      <p class="font-body-lg text-[15px] text-on-surface leading-snug">${esc(first.actie)}</p>
      ${first.waarom ? `<p class="font-body-md text-[12px] text-on-surface-variant/80 mt-1">${esc(first.waarom)}</p>` : ''}
      ${others.length ? `<ul class="mt-3 pt-3 border-t border-white/5 space-y-1.5">${others.map((b) => `
        <li class="flex gap-2 font-body-md text-[13px] text-on-surface-variant">
          <span class="text-on-surface-variant/40">${b.prio ?? '·'}</span><span>${esc(b.actie)}</span>
        </li>`).join('')}</ul>` : ''}
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
      let restProjects = [];
      if (p.iris) {
        html += `<div class="flex items-center justify-between">
          <p class="font-label-caps text-label-caps text-on-surface-variant uppercase">Briefing ${esc(p.iris.date || '')}</p>
          ${p.iris.llm_ok === false ? '<span class="font-label-caps text-[10px] text-error border border-error/30 rounded px-2 py-0.5">TERUGVAL — alleen cijfers</span>' : ''}
        </div>`;
      }
      html += bottleneckPanel(p.bottlenecks);
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
        const panel = projectsPanel(projects, seriesById);
        html += panel.html;
        restProjects = panel.rest;
      }
      if (p.funnel) html += funnelPanel(p.funnel);
      if (p.iris && p.iris.markdown) {
        // Per ##-sectie een eigen inklapbaar paneel; de tabel-sectie met de
        // projectcijfers slaan we over — die staat hierboven al als kaarten.
        const secs = mdSections(p.iris.markdown)
          .filter((s) => !/cijfers per project/i.test(s.title))
          .filter((s) => s.title && s.body);
        if (secs.length) {
          html += `<div class="mt-2"><h3 class="font-label-caps text-label-caps text-primary uppercase mb-2">Iris' analyse</h3>
            <div class="space-y-2">${secs.map((s) => `
              <details class="glass-panel rounded-xl group">
                <summary class="p-4 cursor-pointer select-none flex items-center justify-between gap-3">
                  <span class="font-headline-sm text-[15px] text-on-surface">${esc(s.title)}</span>
                  <span class="material-symbols-outlined text-on-surface-variant/60 text-[20px] transition-transform group-open:rotate-180">expand_more</span>
                </summary>
                <div class="markdown px-4 pb-4">${mdLite(s.body)}</div>
              </details>`).join('')}</div></div>`;
        }
      }
      if (!projects.length && !html && p.iris) {
        html = `<div class="glass-panel rounded-xl p-gutter">
          <div class="markdown font-body-md text-body-md text-on-surface-variant leading-relaxed">${mdLite(p.iris.markdown || '')}</div></div>`;
      }
      // Analytics en pagina-bewegingen komen uit de contextsnapshot, niet uit
      // de briefing: ze verversen elk uur i.p.v. één keer per dag. Ze horen
      // wél hier — dit is het scherm waar je naar cijfers komt kijken.
      let ctx = contextCache;
      if (!ctx) {
        try { ctx = (await api('context')).payload; contextCache = ctx; } catch { ctx = null; }
      }
      if (token !== loadToken) return;
      if (ctx) html += analyticsPanel(ctx.analytics) + seoMoversPanel(ctx.seo);

      el.innerHTML = html || `<div class="glass-panel rounded-xl p-10 text-center fade-up">
        <span class="material-symbols-outlined text-primary text-4xl mb-2">auto_awesome</span>
        <p class="font-body-lg text-body-lg text-on-surface-variant">Nog geen briefing gesynchroniseerd.</p></div>`;
      el.querySelectorAll('[data-cmd]').forEach((btn) => {
        btn.onclick = async () => {
          btn.disabled = true;
          await sendCommand(btn.dataset.cmd,
            btn.dataset.siteId ? { site: btn.dataset.siteId } : {});
          setTimeout(() => { btn.disabled = false; }, 1500);
        };
      });
      bindSparkTips(el, seriesById);
      // Compacte regel → volledige kaart bij tikken (lazy, incl. sparkline).
      el.querySelectorAll('.proj-row').forEach((row) => {
        row.onclick = () => {
          const i = row.dataset.proj;
          const box = el.querySelector(`[data-detail="${i}"]`);
          if (!box) return;
          const open = !box.hidden;
          if (open) { box.hidden = true; }
          else {
            const proj = restProjects[i];
            if (!box.innerHTML) {
              box.innerHTML = projectCard(proj, seriesById[proj.site_id]);
              bindSparkTips(box, seriesById);
            }
            box.hidden = false;
          }
          row.setAttribute('aria-expanded', String(!open));
          row.querySelector('.material-symbols-outlined').style.transform = open ? '' : 'rotate(180deg)';
        };
      });
    } catch (e) {
      if (e.message === 'login') return;
      el.innerHTML = `<div class="glass-panel rounded-xl p-6 fade-up text-error font-body-md">
        Kon de briefing niet laden: ${esc(e.message)}<br>
        <button class="mt-3 bg-primary text-on-primary px-4 py-2 rounded-lg font-headline-sm" onclick="__retryBriefing()">Opnieuw proberen</button></div>`;
    }
  }
  window.__retryBriefing = () => loadBriefing();

  // ── Markdown ─────────────────────────────────────────────────────────────
  // Regel-gebaseerde mini-renderer. De vorige versie plakte alles aan elkaar
  // met <br><br>; Iris' tabellen ("| Project | Cijfer |…") werden daardoor één
  // onleesbare lange regel. Tabellen, lijsten en alinea's krijgen nu elk hun
  // eigen blok-element.
  function inline(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<b class="text-on-surface">$1</b>')
      .replace(/(^|[^*])\*([^*]+)\*/g, '$1<i>$2</i>')
      .replace(/`([^`]+)`/g, '<code class="font-label-caps text-[12px] bg-white/5 rounded px-1">$1</code>');
  }
  const isTableRow = (l) => /^\s*\|.*\|\s*$/.test(l);
  const isDivider = (l) => /^\s*\|?[\s:|-]*-{2,}[\s:|-]*\|?\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());

  function renderTable(rows) {
    if (!rows.length) return '';
    const head = cells(rows[0]);
    const body = rows.slice(isDivider(rows[1] || '') ? 2 : 1).filter((r) => !isDivider(r));
    return `<div class="md-table-wrap"><table class="md-table">
      <thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>
      <tbody>${body.map((r) => {
        const cs = cells(r);
        return `<tr>${head.map((_, i) => `<td>${inline(cs[i] || '')}</td>`).join('')}</tr>`;
      }).join('')}</tbody></table></div>`;
  }

  function mdLite(md) {
    const lines = String(md || '').replace(/\r/g, '').split('\n');
    const out = [];
    let list = null, table = null, para = [];
    const flushPara = () => {
      if (para.length) { out.push(`<p class="md-p">${inline(para.join(' '))}</p>`); para = []; }
    };
    const flushList = () => {
      if (list) { out.push(`<${list.tag} class="md-list">${list.items.join('')}</${list.tag}>`); list = null; }
    };
    const flushTable = () => { if (table) { out.push(renderTable(table)); table = null; } };
    const flushAll = () => { flushPara(); flushList(); flushTable(); };

    for (const raw of lines) {
      const l = raw.trimEnd();
      if (isTableRow(l)) { flushPara(); flushList(); (table = table || []).push(l); continue; }
      flushTable();
      if (!l.trim()) { flushPara(); flushList(); continue; }
      let m;
      if ((m = l.match(/^(#{1,6})\s+(.*)$/))) {
        flushPara(); flushList();
        const lvl = m[1].length;
        const cls = lvl <= 2 ? 'font-headline-sm text-headline-sm text-on-surface mt-5 mb-2'
          : 'font-headline-sm text-[15px] text-on-surface mt-4 mb-1';
        out.push(`<h4 class="${cls}">${inline(m[2])}</h4>`);
      } else if ((m = l.match(/^\s*[-*+]\s+(.*)$/))) {
        flushPara();
        if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; }
        list.items.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = l.match(/^\s*\d+[.)]\s+(.*)$/))) {
        flushPara();
        if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [] }; }
        list.items.push(`<li>${inline(m[1])}</li>`);
      } else if (/^\s*(---|___|\*\*\*)\s*$/.test(l)) {
        flushPara(); flushList(); out.push('<hr class="md-hr">');
      } else {
        flushList(); para.push(l.trim());
      }
    }
    flushAll();
    return out.join('');
  }

  // Splitst de briefing in secties op ## / # -koppen, zodat elke sectie een
  // eigen inklapbaar paneel krijgt in plaats van één doorlopende lap.
  function mdSections(md) {
    const lines = String(md || '').replace(/\r/g, '').split('\n');
    const secs = [];
    let cur = { title: '', lines: [] };
    for (const l of lines) {
      const m = l.match(/^(#{1,3})\s+(.*)$/);
      if (m && m[1].length <= 2) {
        if (cur.title || cur.lines.some((x) => x.trim())) secs.push(cur);
        cur = { title: m[2].trim(), lines: [] };
      } else cur.lines.push(l);
    }
    if (cur.title || cur.lines.some((x) => x.trim())) secs.push(cur);
    return secs.map((s) => ({ title: s.title, body: s.lines.join('\n').trim() }))
      .filter((s) => s.title || s.body);
  }

  // ── Notities ─────────────────────────────────────────────────────────────
  const noteText = $('note-text');
  // Start klein (3 regels) en groeit mee met de inhoud — een lege textarea van
  // vijf regels is vooral veel leeg glas op een telefoonscherm, en een vaste
  // hoogte knipt een langere thought-dump af achter een scrollbalkje.
  function autoGrowNote() {
    noteText.style.height = 'auto';
    noteText.style.height = `${Math.min(noteText.scrollHeight, 320)}px`;
  }
  noteText.addEventListener('input', () => {
    $('charCount').textContent = `${noteText.value.length} tekens`;
    autoGrowNote();
  });
  $('saveBtn').onclick = async () => {
    const text = noteText.value.trim();
    if (!text) return;
    const btn = $('saveBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="material-symbols-outlined animate-spin">progress_activity</span> Opslaan...';
    try {
      await api('note', 'POST', { text });
      btn.innerHTML = '<span class="material-symbols-outlined">check_circle</span> Opgeslagen';
      noteText.value = ''; $('charCount').textContent = '0 tekens'; autoGrowNote();
      toast('Notitie klaargezet voor sync', 'ok', 'cloud_done');
      loadNotes();
    } catch (e) {
      btn.innerHTML = '<span class="material-symbols-outlined">error</span> Fout';
      toast(e.message, 'err', 'error');
    }
    setTimeout(() => { btn.innerHTML = '<span class="material-symbols-outlined">cloud_upload</span> Opslaan voor sync'; btn.disabled = false; }, 1600);
  };

  // De belofte "landt bij de volgende sync" is alleen waar als de sync ook
  // écht draait. Dezelfde leeftijdsgrenzen als de sync-pill in de kop, zodat
  // de twee elkaar nooit tegenspreken.
  function noteSyncHint(pendingCount) {
    const el = $('note-sync-hint');
    if (!pendingCount) { el.innerHTML = ''; return; }
    const label = pendingCount === 1 ? '1 notitie wacht op sync' : `${pendingCount} notities wachten op sync`;
    if (!lastPushAt) {
      el.innerHTML = `<div class="flex items-start gap-2 text-warn font-body-md text-body-md">
        <span class="material-symbols-outlined text-[18px] mt-0.5">warning</span>
        <span>${label} — AgentOS heeft nog nooit gesynchroniseerd. Controleer of de lokale machine draait.</span></div>`;
      return;
    }
    const age = Math.round((Date.now() - new Date(lastPushAt)) / 60000);
    if (age < 180) {
      el.innerHTML = `<div class="flex items-start gap-2 text-on-surface-variant font-body-md text-body-md">
        <span class="material-symbols-outlined text-[18px] mt-0.5 text-primary">schedule</span>
        <span>${label} — AgentOS haalt ze elke ~3 min op, laatste sync ${age < 1 ? 'net' : `${age}m geleden`}.</span></div>`;
    } else {
      el.innerHTML = `<div class="flex items-start gap-2 text-warn font-body-md text-body-md">
        <span class="material-symbols-outlined text-[18px] mt-0.5">warning</span>
        <span>${label} — AgentOS lijkt al ${Math.round(age / 60)}u offline. Ze staan veilig te wachten en landen zodra de machine terug is.</span></div>`;
    }
  }

  async function deleteNote(id, cardEl) {
    try {
      const r = await api('note-delete', 'POST', { id });
      if (r.deleted) {
        cardEl.classList.add('leaving');
        setTimeout(() => loadNotes(), 200);
      } else {
        toast('Al gesynct — deze staat al in je vault', '', 'info');
        loadNotes();
      }
    } catch (e) { toast(e.message, 'err', 'error'); }
  }

  async function loadNotes() {
    try {
      const data = await api('notes');
      const el = $('notes-list');
      const notes = data.notes || [];
      const pending = notes.filter((n) => n.status !== 'synced').length;
      noteSyncHint(pending);
      el.innerHTML = notes.length ? notes.map((n) => {
        const synced = n.status === 'synced';
        return `
        <div class="glass-panel rounded-lg p-4 group hover:border-primary/40 transition-colors fade-up" data-note-id="${n.id}">
          <div class="flex justify-between items-start mb-2 gap-2">
            <span class="font-label-caps text-label-caps text-on-surface-variant">${esc(fmtDate(n.created_at))}</span>
            <div class="flex items-center gap-2 shrink-0">
              <span class="note-status ${synced ? 'is-synced' : 'is-pending'}">
                <span class="material-symbols-outlined text-[14px]">${synced ? 'cloud_done' : 'schedule'}</span>
                ${synced ? 'In vault' : 'Wacht op sync'}
              </span>
              ${synced ? '' : `<button class="note-delete tap-target text-on-surface-variant hover:text-error transition-colors" title="Verwijderen" aria-label="Notitie verwijderen">
                <span class="material-symbols-outlined text-[18px]">delete</span></button>`}
            </div>
          </div>
          <p class="font-body-md text-body-md text-on-surface line-clamp-3">${esc(n.text)}</p>
        </div>`;
      }).join('')
        : `<div class="glass-panel rounded-xl p-8 text-center fade-up">
             <span class="material-symbols-outlined text-on-surface-variant text-3xl mb-2">sticky_note_2</span>
             <p class="font-body-md text-body-md text-on-surface-variant">Nog geen notities — typ hierboven je eerste thought-dump.</p>
           </div>`;
      el.querySelectorAll('.note-delete').forEach((btn) => {
        btn.onclick = () => {
          const card = btn.closest('[data-note-id]');
          deleteNote(Number(card.dataset.noteId), card);
        };
      });
    } catch (e) { /* login afgehandeld */ }
  }

  // ── Systeem ──────────────────────────────────────────────────────────────
  async function loadSystem(token = loadToken) {
    try {
      const [itemsData, outboxData, sessionData] = await Promise.all([
        api('items'), api('outbox'), api('sessions'),
      ]);
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
                <span class="status-dot ${online ? 'pulse' : 'offline'}"></span>
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

      const sess = sessionData.sessions || [];
      $('sessions-list').innerHTML = sess.map((s) => `
        <div class="flex items-center justify-between gap-3 py-2 border-b border-white/5 last:border-0">
          <div class="min-w-0">
            <p class="font-body-lg text-body-lg text-on-surface truncate">${esc(s.label)}${s.current ? ' <span class="font-label-caps text-label-caps text-primary">· DIT APPARAAT</span>' : ''}</p>
            <p class="font-body-md text-body-md text-on-surface-variant">Laatst actief ${fmtDate(s.last_seen)}</p>
          </div>
        </div>`).join('');
    } catch (e) { /* login afgehandeld */ }
  }

  $('logoutAllBtn').onclick = async () => {
    try {
      await api('logout-all', 'POST', {});
      toast('Alle apparaten uitgelogd', 'ok', 'logout');
      show('login');
    } catch (e) { if (e.message !== 'login') toast(e.message, 'err'); }
  };

  // ── Vandaag ──────────────────────────────────────────────────────────────
  // Het scherm dat van Iris Remote een assistent maakt in plaats van een
  // afstandsbediening: je dag, je mailbox en het oordeel over hoe het gaat —
  // vóórdat je iets hoeft te vragen.
  let contextCache = null;

  async function sendCommand(action, payload = {}, label = '') {
    try {
      const r = await api('command', 'POST', { action, payload });
      toast(r.queued ? `Klaargezet: ${r.label || label}` : 'Stond al in de rij',
        r.queued ? 'ok' : '', r.queued ? 'bolt' : 'schedule');
    } catch (e) {
      if (e.message !== 'login') toast(e.message, 'err');
    }
  }

  // Een sectie die uit staat is géén sectie zonder nieuws. Dat onderscheid
  // expliciet tonen voorkomt dat een kapotte koppeling als rust leest.
  function sectionOff(icon, title, sec) {
    return `<div class="glass-panel rounded-xl p-4">
      <div class="card-head">
        <div class="card-head-icon" style="background:rgba(255,255,255,0.05); color:var(--on-surface-variant)">
          <span class="material-symbols-outlined">${icon}</span>
        </div>
        <div class="min-w-0">
          <p class="card-head-title">${esc(title)}</p>
          <p class="card-head-meta mt-0.5">${esc(sec.reason || sec.error || 'Niet beschikbaar')}</p>
        </div>
      </div>
      ${sec.action_hint ? `<p class="font-body-md text-[13px] text-primary mt-3 pl-10">${esc(sec.action_hint)}</p>` : ''}
    </div>`;
  }

  const SEV = { hoog: 'sev-hoog', midden: 'sev-midden', laag: 'sev-laag' };

  function pulsePanel(pulse, excludeAreas = []) {
    if (!pulse) return '';
    // Op Vandaag staan mail/agenda al concreet in hun eigen kaarten erboven —
    // dezelfde regel daar nóg een keer in 'Vraagt aandacht'/'Gaat goed' is
    // geen extra informatie, alleen ruis. Elders (chat-context) blijft pulse
    // compleet — dit filtert alleen de weergave, niet de berekening.
    const bad = (pulse.bad || []).filter((b) => !excludeAreas.includes(b.area));
    const good = (pulse.good || []).filter((g) => !excludeAreas.includes(g.area));
    if (!bad.length && !good.length) return '';
    return `<div class="space-y-stack-sm">
      ${bad.length ? `<div class="glass-panel rounded-xl p-4">
        <div class="card-head mb-3">
          <div class="card-head-icon" style="background:rgba(255,156,146,0.14); color:var(--err)">
            <span class="material-symbols-outlined">priority_high</span>
          </div>
          <p class="card-head-title">Vraagt aandacht</p>
        </div>
        <ul class="space-y-3">${bad.map((b) => `
          <li class="flex gap-3">
            <span class="sev-pill h-fit shrink-0 mt-0.5 ${SEV[b.severity] || SEV.laag}">${esc((b.severity || '').toUpperCase() || b.area.toUpperCase())}</span>
            <div class="min-w-0">
              <p class="font-body-md text-[13px] text-on-surface leading-snug">${esc(b.what)}</p>
              ${b.detail ? `<p class="font-body-md text-[12px] text-on-surface-variant/70 leading-snug mt-0.5 truncate">${esc(b.detail)}</p>` : ''}
              ${b.why ? `<p class="font-body-md text-[12px] text-on-surface-variant/60 leading-snug mt-0.5">${esc(b.why)}</p>` : ''}
            </div>
          </li>`).join('')}</ul>
      </div>` : ''}
      ${good.length ? `<div class="glass-panel rounded-xl p-4">
        <div class="card-head mb-3">
          <div class="card-head-icon" style="background:rgba(74,222,128,0.14); color:var(--ok)">
            <span class="material-symbols-outlined">check</span>
          </div>
          <p class="card-head-title">Gaat goed</p>
        </div>
        <ul class="space-y-2">${good.map((g) => `
          <li class="flex gap-2 items-start">
            <span class="material-symbols-outlined text-[16px] mt-0.5" style="color:var(--ok)">check_small</span>
            <p class="font-body-md text-[13px] text-on-surface-variant leading-snug">${esc(g.what)}</p>
          </li>`).join('')}</ul>
      </div>` : ''}
    </div>`;
  }

  // Generieke variant van het detail-sheet voor volledige overzichten (Postvak,
  // Agenda) die geen item uit `items` zijn en dus geen goedkeuren/afwijzen-rij
  // nodig hebben — alleen een titel, sluitknop en inhoud. Retourneert de kaart
  // zodat de aanroeper er zelf click-handlers op kan binden.
  function openSheet(eyebrow, title, bodyHtml) {
    $('detail-card').innerHTML = `
      <div class="sheet-grabber" aria-hidden="true"></div>
      <div class="flex justify-between items-start gap-3">
        <div>
          <p class="font-label-caps text-label-caps text-primary uppercase mb-1">${esc(eyebrow)}</p>
          <h2 id="detail-title" class="font-headline-sm text-headline-sm">${esc(title)}</h2>
        </div>
        <button id="detail-close" class="tap-target text-on-surface-variant hover:text-primary shrink-0" aria-label="Sluiten">
          <span class="material-symbols-outlined">close</span>
        </button>
      </div>
      <div class="mt-3 space-y-3">${bodyHtml}</div>`;
    showDetail();
    $('detail-close').onclick = closeDetail;
    return $('detail-card');
  }

  function agendaEventRow(e) {
    return `<li class="flex gap-3 items-baseline ${e.declined ? 'opacity-40 line-through' : ''}">
      <span class="font-body-md text-[12px] text-primary stat-num w-12 shrink-0">${esc(e.time)}</span>
      <div class="min-w-0">
        <p class="font-body-md text-[13px] text-on-surface leading-snug truncate">${esc(e.summary)}</p>
        ${e.location || e.online ? `<p class="font-body-md text-[12px] text-on-surface-variant/60 truncate">${e.online ? 'online' : esc(e.location)}</p>` : ''}
        ${(e.attendees || []).length ? `<div class="flex flex-wrap gap-1 mt-1">${e.attendees.slice(0, 4).map((att) => `
          <span class="font-body-md text-[10.5px] text-on-surface-variant bg-white/5 rounded-full px-2 py-0.5">${esc(att.name)}</span>`).join('')}</div>` : ''}
        ${e.watch_for ? `<p class="font-body-md text-[12px] text-warn italic mt-1 leading-snug">⚠ ${esc(e.watch_for)}</p>` : ''}
      </div>
    </li>`;
  }

  // Gedeeld door de compacte kaart op Vandaag (daysLimit 6, ingeklapt) en het
  // volledige Agenda-scherm dat opent zodra je de kaart tikt (daysLimit 14,
  // altijd open) — twee weergaven van dezelfde waarheid, niet twee sjablonen.
  function agendaBodyHtml(a, { daysLimit = 6, collapseDays = true } = {}) {
    const free = (a.free_today || []).map((g) => `${g.start}–${g.end}`).join(' · ');
    const daysList = `<ul class="${collapseDays ? 'mt-2' : ''} space-y-1.5">${a.days.slice(1, daysLimit).map((d) => `
          <li class="flex justify-between gap-3 font-body-md text-[12px]">
            <span class="text-on-surface-variant truncate">${esc(d.date)} · ${esc((d.titles || []).join(', '))}</span>
            <span class="stat-num ${d.count >= 6 ? 'text-warn' : 'text-on-surface-variant/60'} shrink-0">${d.count}</span>
          </li>`).join('')}</ul>`;
    return `
      ${a.unreachable && a.unreachable.length ? `<p class="font-body-md text-[12px] text-error">⚠ Niet alle agenda's leesbaar (${esc(a.unreachable.map((u) => u.id).join(', '))}) — dit overzicht is mogelijk onvolledig.</p>` : ''}
      ${a.next ? `<div class="rounded-lg p-3" style="background:rgba(142,213,255,0.07); border:1px solid rgba(142,213,255,0.18)">
        <p class="font-body-md text-[11px] font-semibold uppercase tracking-wide text-primary">Hierna · ${esc(a.next.time)}</p>
        <p class="font-body-lg text-[15px] text-on-surface mt-1">${esc(a.next.summary)}</p>
        ${a.next.location || a.next.online ? `<p class="font-body-md text-[12px] text-on-surface-variant mt-0.5">${a.next.online ? 'online' : esc(a.next.location)}</p>` : ''}
      </div>` : ''}
      ${(a.today || []).length ? `<ul class="space-y-2">${a.today.map(agendaEventRow).join('')}</ul>`
        : '<p class="font-body-md text-[13px] text-on-surface-variant">Geen afspraken vandaag.</p>'}
      <p class="font-body-md text-[12px] text-on-surface-variant/70 pt-2 divider-line">
        ${free ? `Nog vrij: <span class="text-on-surface">${esc(free)}</span>` : 'Geen vrij blok van 45+ min meer vandaag.'}
      </p>
      ${(a.days || []).length > 1
        ? (collapseDays
          ? `<details class="pt-1"><summary class="font-body-md text-[12px] text-on-surface-variant cursor-pointer">Komende dagen</summary>${daysList}</details>`
          : `<div class="pt-1"><p class="font-body-md text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant mb-2">Komende dagen</p>${daysList}</div>`)
        : ''}`;
  }

  function agendaPanel(a) {
    if (!a || a.status !== 'ok') return a ? sectionOff('event_busy', 'Agenda', a) : '';
    return `<div class="glass-panel rounded-xl p-4 space-y-3">
      <button class="card-head card-head-btn" data-open-agenda-sheet="1">
        <div class="card-head-icon"><span class="material-symbols-outlined">calendar_month</span></div>
        <div class="min-w-0 flex-1">
          <p class="card-head-title">Agenda</p>
          <p class="card-head-meta">${(a.today || []).length} afspra${(a.today || []).length === 1 ? 'ak' : 'ken'} vandaag</p>
        </div>
        <span class="material-symbols-outlined text-on-surface-variant/50 text-[20px] shrink-0">chevron_right</span>
      </button>
      ${agendaBodyHtml(a, { daysLimit: 6, collapseDays: true })}
    </div>`;
  }

  function openAgendaSheet() {
    const a = contextCache && contextCache.agenda;
    if (!a || a.status !== 'ok') { toast('Agenda nog niet beschikbaar', '', 'schedule'); return; }
    openSheet('Agenda', `${(a.today || []).length} afspra${(a.today || []).length === 1 ? 'ak' : 'ken'} vandaag`,
      agendaBodyHtml(a, { daysLimit: 14, collapseDays: false }));
  }

  // ── Postvak ───────────────────────────────────────────────────────────────
  // Twee weergaven van dezelfde waarheid, bewust ongelijk: de kaart op Vandaag
  // is een sámenvatting (wat wacht er op mijn antwoord — hooguit drie regels),
  // het Postvak-scherm is de werkplek (alles, gegroepeerd). Tot 10 aug 2026
  // renderden beide exact dezelfde HTML, dus stond de vólle inbox midden in het
  // dagoverzicht: drie grote cijfers, een rode alinea en vijftien regels met op
  // élke regel het label van de sectie erboven ("REAGEREN" boven zeven pillen
  // "Reageren"). Dat pilletje leek bovendien een knop terwijl alleen regels mét
  // conceptantwoord iets doen — een aanwijzing die vaker liegt dan klopt.

  function relTime(v) {
    if (!v) return '';
    const t = new Date(v).getTime();
    if (Number.isNaN(t)) return '';
    const min = Math.round((Date.now() - t) / 60000);
    if (min < 1) return 'nu';
    if (min < 60) return `${min}m`;
    const u = Math.round(min / 60);
    if (u < 24) return `${u}u`;
    const d = Math.round(u / 24);
    if (d === 1) return 'gisteren';
    if (d < 8) return `${d}d`;
    return new Date(t).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' });
  }

  // Een avatar met initialen doet hier échte navigatie-arbeid: een postvak
  // scan je op afzender, niet op onderwerp. De kleur komt uit de naam zodat
  // dezelfde afzender altijd dezelfde tint krijgt — herkenning zonder tekst.
  const _AVATAR_TINTS = [
    'rgba(142,213,255,0.16)', 'rgba(167,181,255,0.16)', 'rgba(134,239,172,0.14)',
    'rgba(251,191,36,0.14)', 'rgba(244,164,196,0.14)', 'rgba(153,246,228,0.13)',
  ];
  function initials(name, email) {
    const src = (name || '').trim() || (email || '').split('@')[0].replace(/[._-]+/g, ' ');
    const parts = src.split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    const first = parts[0][0] || '';
    const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
    return (first + last).toUpperCase();
  }
  function avatarTint(seed) {
    let h = 0;
    for (const ch of String(seed || '')) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return _AVATAR_TINTS[h % _AVATAR_TINTS.length];
  }

  // Eén regel in het postvak. `tapbaar` is niet cosmetisch: alleen een mail met
  // conceptantwoord kan iets openen, en dus krijgt alleen díé regel een
  // aanwijzing (chevron + hover). De rest is tekst en ziet er ook zo uit.
  // Op het volle scherm krijgt elke regel drie handelingen in plaats van één.
  // Tot 11 aug 2026 kon een mailregel precies één ding: opengaan als er toevallig
  // een conceptantwoord onder lag. Zeven regels, één werkende tik — de rest was
  // een lijst om naar te kijken. Archiveren en blokkeren zijn bewust twee
  // knoppen: "ik ben klaar met dit bericht" en "ik wil deze afzender nooit meer"
  // zijn verschillende besluiten, en ze op één knop leggen is hoe je per
  // ongeluk een klant wegfiltert.
  function mailRow(u, { compact = false } = {}) {
    const who = u.from_name || u.from_email || 'Onbekend';
    const tapbaar = !!u.suggested_reply;
    const ongelezen = u.is_read === 0 || u.is_read === false;
    const samenvatting = u.ai_summary || u.ai_action || '';
    const acties = compact ? '' : `<div class="mail-acties">
        <button class="mail-actie" data-mail-archive="${esc(u.id)}"
          aria-label="Archiveren" title="Deze mail hoeft niets van je">
          <span class="material-symbols-outlined">inbox</span></button>
        ${u.from_email ? `<button class="mail-actie is-blok" data-mail-block="${esc(u.from_email)}"
          data-mail-id="${esc(u.id)}" aria-label="Afzender blokkeren"
          title="Nooit meer van ${esc(u.from_email)}">
          <span class="material-symbols-outlined">block</span></button>` : ''}
      </div>`;
    return `<li class="mail-row${tapbaar ? ' is-tapbaar' : ''}"${tapbaar ? ` data-open-mail="${esc(u.id)}" role="button" tabindex="0"` : ''}>
      <span class="mail-avatar" style="background:${avatarTint(who)}">${esc(initials(u.from_name, u.from_email))}</span>
      <div class="min-w-0 flex-1">
        <div class="mail-row-top">
          <span class="mail-from${ongelezen ? ' is-ongelezen' : ''}">${esc(who)}</span>
          <span class="mail-time">${esc(relTime(u.received_at))}</span>
        </div>
        <p class="mail-subject">${esc(u.subject || '(geen onderwerp)')}</p>
        ${!compact && samenvatting ? `<p class="mail-summary">${esc(samenvatting)}</p>` : ''}
      </div>
      ${tapbaar ? `<span class="mail-draft-hint"><span class="material-symbols-outlined">edit_note</span></span>` : ''}
      ${acties}
    </li>`;
  }

  // Wat de afzenderregels weghielden, mét de reden en een weg terug. Dit blok
  // is de tegenprestatie voor strenger filteren: zonder zichtbare uitgefilterde
  // bak is "0 urgent" niet te onderscheiden van "alles weggegooid", en dat is
  // een gevaarlijker leugen dan een dubbele mail (zelfde afweging als de
  // Uitgefilterd-lijst bij de SEO-kansen). Ingeklapt, want het is naslag.
  function filteredNote(m) {
    const n = m.filtered_week || 0;
    const lijst = m.filtered_recent || [];
    if (!n && !lijst.length) return '';
    return `<details class="mail-note is-stil">
      <summary>
        <span class="material-symbols-outlined">shield</span>
        <span class="flex-1 min-w-0">${n} weggehouden deze week</span>
        <span class="material-symbols-outlined mail-note-caret">expand_more</span>
      </summary>
      <ul class="mail-blocklist">
        ${lijst.map((u) => `<li>
          <div class="min-w-0 flex-1">
            <p class="mail-blocked-from">${esc(u.from_name || u.from_email || 'Onbekend')}</p>
            <p class="mail-blocked-sub">${esc(u.subject || '(geen onderwerp)')}</p>
            <p class="mail-blocked-why">${esc(u.filter_reason || 'afzenderregel')}</p>
          </div>
          ${u.from_email ? `<button class="mail-actie" data-mail-allow="${esc(u.from_email)}"
            aria-label="Toch toelaten" title="Deze afzender voortaan tonen">
            <span class="material-symbols-outlined">undo</span></button>` : ''}
        </li>`).join('')}
      </ul>
    </details>`;
  }

  // Een leeg urgent-blok is niet hetzelfde als een rustige mailbox. Urgentie
  // én conceptantwoorden hangen allebei aan de triage; draait die niet, dan is
  // "0 urgent" honger en geen rust. Zwijgen daarover is precies hoe 68
  // ongetrieerde mails er als een opgeruimd postvak uitzagen (7 aug 2026).
  // De uitleg zit sinds 10 aug 2026 in een <details>: de waarschuwing moet
  // opvallen, de alinea eronder hoeft niet elke keer meegelezen te worden.
  function triageNote(m) {
    const n = m.untriaged || 0;
    if (!n) return '';
    const blocked = m.llm_paused;
    // Onder de 10 zonder blokkade is dit gewoon werk in uitvoering: de
    // volgende sync trieert ze. Melden zou dan ruis zijn.
    if (n < 10 && !blocked) return '';
    return `<details class="mail-note ${blocked ? 'is-err' : 'is-warn'}">
      <summary>
        <span class="material-symbols-outlined">${blocked ? 'error' : 'schedule'}</span>
        <span class="flex-1 min-w-0">${n} mail${n === 1 ? '' : 's'} nog niet getrieerd${blocked ? ' — AI staat op pauze' : ''}</span>
        <span class="material-symbols-outlined mail-note-caret">expand_more</span>
      </summary>
      <p>${blocked
        ? 'Zolang dat zo is komt er geen urgentie-oordeel en geen conceptantwoord — een leeg "urgent" betekent hier niet "niets aan de hand".'
        : 'Urgentie en conceptantwoorden volgen zodra ze getrieerd zijn.'}</p>
    </details>`;
  }

  const _SORT_TITLE = {
    needs_reply: 'Wacht op jouw antwoord',
    waiting: 'Wacht op hen',
    fyi: 'Ter info',
  };

  // Concepten zijn het enige in dit scherm dat met één tik de deur uit kan.
  // Een tegel met "0" erin is geen informatie maar ruis, dus bij nul verdwijnt
  // de regel in plaats van dat hij grijs blijft staan.
  function draftsCta(m) {
    const n = (m.helpdesk_pending || 0) + (m.personal_drafts || 0);
    if (!n) return '';
    return `<button class="mail-cta" data-goto-mail="1">
      <span class="material-symbols-outlined">drafts</span>
      <span class="flex-1 text-left">${n} concept${n === 1 ? '' : 'en'} ${n === 1 ? 'ligt' : 'liggen'} klaar om te versturen</span>
      <span class="material-symbols-outlined">chevron_right</span>
    </button>`;
  }

  // De koptekst zegt wat er van jóu wordt gevraagd, niet hoe groot de stapel
  // is. "121 open" was het grootste getal op het scherm terwijl geen enkele
  // handeling dat getal verandert; het aantal dat op een antwoord wacht wél.
  function mailLead(m) {
    const n = ((m.sorted || {}).needs_reply || []).length;
    if (n) return `${n} ${n === 1 ? 'mail wacht' : 'mails wachten'} op jouw antwoord`;
    if (m.untriaged) return 'Nog niets beoordeeld — de triage moet eerst draaien';
    return 'Niets wacht op jouw antwoord';
  }

  // De cijfers blijven staan, maar als kleine context onder de zin die ze
  // samenvat — niet als drie tegels die de helft van het scherm opeisen.
  function mailMeta(m) {
    const w = m.week || {};
    const old = m.oldest_open;
    const bits = [`<span><b>${m.backlog}</b> open</span>`];
    // Alleen tonen als het waargenomen ís. `is_replied` kwam tot 11 aug 2026
    // uitsluitend van onze eigen verstuurknop, dus stond er permanent "0%
    // beantwoord" — een cijfer dat nooit iets anders kón worden leest als een
    // oordeel over jou, en dat was het niet.
    if (w.measured && w.reply_rate != null) bits.push(`<span><b>${w.reply_rate}%</b> beantwoord (7d)</span>`);
    if (old && old.days != null) {
      bits.push(`<span class="${old.days >= 3 ? 'is-warn' : ''}">oudste <b>${old.days}d</b></span>`);
    }
    return `<p class="mail-meta">${bits.join('<i>·</i>')}</p>`;
  }

  // Vandaag: samenvatting. Hooguit drie regels, compact (zonder AI-samenvatting)
  // en met één uitgang naar het volledige scherm.
  function mailPanel(m) {
    if (!m || m.status !== 'ok') return m ? sectionOff('mail', 'Postvak', m) : '';
    const top = ((m.sorted || {}).needs_reply || []).slice(0, 3);
    return `<div class="glass-panel rounded-xl overflow-hidden">
      <div class="mail-head">
        <button class="mail-head-main" data-open-mail-sheet="1">
          <div class="card-head-icon"><span class="material-symbols-outlined">mail</span></div>
          <div class="min-w-0 flex-1 text-left">
            <p class="card-head-title">Postvak</p>
            <p class="card-head-meta">${esc(mailLead(m))}</p>
          </div>
        </button>
        <button class="mail-head-btn" data-cmd="mail_sync">Ophalen</button>
      </div>
      <div class="px-4 pb-4 space-y-3">
        ${draftsCta(m)}
        ${triageNote(m)}
        ${top.length ? `<ul class="mail-list">${top.map((u) => mailRow(u, { compact: true })).join('')}</ul>` : ''}
        <button class="mail-open-all" data-open-mail-sheet="1">
          Postvak openen<span class="mail-open-all-count">${m.backlog}</span>
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
      </div>
    </div>`;
  }

  // Het scherm: alles, gegroepeerd naar wat het van je vraagt. 'Ter info' staat
  // ingeklapt — dat is per definitie de bak waar je niets mee hoeft.
  function mailScreenHtml(m) {
    const sorted = m.sorted || {};
    const secties = ['needs_reply', 'waiting', 'fyi'].filter((k) => (sorted[k] || []).length);
    return `
      ${draftsCta(m)}
      ${triageNote(m)}
      ${filteredNote(m)}
      ${mailMeta(m)}
      ${secties.length ? secties.map((key) => {
        const rows = sorted[key];
        const lijst = `<ul class="mail-list">${rows.map((u) => mailRow(u)).join('')}</ul>`;
        if (key === 'fyi') {
          return `<details class="mail-sectie">
            <summary class="mail-sectie-kop"><span class="flex-1 text-left">${_SORT_TITLE[key]}</span>
              <span class="mail-sectie-num">${rows.length}</span>
              <span class="material-symbols-outlined mail-note-caret">expand_more</span></summary>
            ${lijst}</details>`;
        }
        return `<section class="mail-sectie">
          <p class="mail-sectie-kop"><span class="flex-1">${_SORT_TITLE[key]}</span>
            <span class="mail-sectie-num">${rows.length}</span></p>
          ${lijst}</section>`;
      }).join('') : `<p class="mail-leeg">Geen beoordeelde mail in het postvak.</p>`}
      ${sorted.untriaged ? `<p class="mail-voet">+ ${sorted.untriaged} nog niet getrieerd — die verschijnen zodra de triage draait.</p>` : ''}`;
  }

  function openMailSheet() {
    const m = contextCache && contextCache.mail;
    if (!m || m.status !== 'ok') { toast('Postvak nog niet beschikbaar', '', 'schedule'); return; }
    const card = openSheet('Postvak', mailLead(m), mailScreenHtml(m));
    bindMailRows(card, true);
    const goto = card.querySelector('[data-goto-mail]');
    if (goto) goto.onclick = () => { closeDetail(); inboxFilter = 'mail'; show('inbox'); renderItems(); };
  }

  // Eén bindfunctie voor kaart én scherm: twee plekken die dezelfde regel
  // anders openen is precies hoe ze uit elkaar gaan lopen.
  function bindMailRows(root, viaSheet) {
    // Blokkeren/archiveren/toelaten lopen via het commando-pad en niet via
    // `decide`: alleen mail mét conceptantwoord bestaat als besluit-item in de
    // cloud, en juist de rest van het postvak moest handelingen krijgen.
    // De regel verdwijnt meteen uit de lijst — de bridge is een pull-model, dus
    // wachten op bevestiging zou drie minuten "er gebeurt niets" betekenen. Wat
    // er werkelijk gebeurde komt terug in de volgende sync (en de uitgefilterde
    // bak eronder), dus dit is optimistisch, niet ongecontroleerd.
    const weg = (btn) => { const li = btn.closest('li'); if (li) li.remove(); };

    root.querySelectorAll('[data-mail-archive]').forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        btn.disabled = true;
        weg(btn);
        await sendCommand('mail_archive', { email_id: btn.dataset.mailArchive });
      };
    });
    root.querySelectorAll('[data-mail-block]').forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const adres = btn.dataset.mailBlock;
        if (!confirm(`Nooit meer mail van ${adres}?\n\nAlles wat er al ligt van deze afzender wordt opgeruimd. Terugdraaien kan via "weggehouden deze week".`)) return;
        btn.disabled = true;
        weg(btn);
        await sendCommand('mail_rule', {
          email_id: btn.dataset.mailId, email: adres, scope: 'adres', action: 'spam',
        });
      };
    });
    root.querySelectorAll('[data-mail-allow]').forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        btn.disabled = true;
        weg(btn);
        await sendCommand('mail_rule', {
          email: btn.dataset.mailAllow, scope: 'adres', action: 'altijd-tonen',
        });
      };
    });

    root.querySelectorAll('[data-open-mail]').forEach((row) => {
      const open = () => {
        const it = items.find((i) => i.dismiss_kind === 'personal_mail'
          && String(i.item_id) === String(row.dataset.openMail));
        if (!it) { toast('Concept nog niet gesynchroniseerd — probeer zo opnieuw', '', 'schedule'); return; }
        if (viaSheet) { closeDetail(); setTimeout(() => openDetail(it), 300); }
        else openDetail(it);
      };
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
  }

  // Snelle acties: alles wat hier staat landt achter een review-gate. Bewust
  // géén knop die publiceert of verstuurt — dat blijft per item een besluit.
  const QUICK = [
    { cmd: 'content_run', icon: 'edit_document', label: 'Artikelen schrijven', hint: 'naar Wachtrij', needsSite: true },
    { cmd: 'seo_refresh', icon: 'trending_up', label: 'Pagina’s verrijken', hint: 'wegzakkers → Wachtrij', needsSite: true },
    { cmd: 'outreach_run', icon: 'campaign', label: 'Outreach klaarzetten', hint: 'concepten ter review' },
    { cmd: 'helpdesk_run', icon: 'support_agent', label: 'Helpdesk-concepten', hint: 'antwoorden schrijven' },
    { cmd: 'iris_briefing', icon: 'auto_awesome', label: 'Iris laten analyseren', hint: 'nieuwe briefing' },
    { cmd: 'context_refresh', icon: 'refresh', label: 'Cijfers verversen', hint: 'cache legen' },
  ];

  function quickPanel(ctx) {
    const sites = ((ctx.seo || {}).sites || []).map((s) => s.site_id);
    return `<div class="glass-panel rounded-xl p-4">
      <div class="card-head mb-1">
        <div class="card-head-icon"><span class="material-symbols-outlined">bolt</span></div>
        <p class="card-head-title">Zet werk in gang</p>
      </div>
      <p class="font-body-md text-[12px] text-on-surface-variant/70 mb-3 pl-10">Alles landt achter de review-gate — er gaat niets live zonder jouw tik.</p>
      ${sites.length ? `<select id="quick-site" class="w-full bg-[#020617] border-none rounded-lg p-2 mb-3 text-on-surface font-body-md text-[13px]">
        ${sites.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
      </select>` : ''}
      <div class="grid grid-cols-2 gap-2">
        ${QUICK.filter((q) => !q.needsSite || sites.length).map((q) => `
          <button data-cmd="${q.cmd}" ${q.needsSite ? 'data-site="1"' : ''}
            class="quick-card text-left rounded-lg p-3">
            <span class="material-symbols-outlined text-primary text-[20px]">${q.icon}</span>
            <p class="font-headline-sm text-[13px] text-on-surface mt-1.5 leading-snug">${q.label}</p>
            <p class="font-body-md text-[11px] text-on-surface-variant/60 leading-snug mt-0.5">${q.hint}</p>
          </button>`).join('')}
      </div>
    </div>`;
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 6) return 'Nog wakker?';
    if (h < 12) return 'Goedemorgen';
    if (h < 18) return 'Goedemiddag';
    return 'Goedenavond';
  }

  // Iris opent het gesprek: bij de eerste keer dat Vandaag laadt, plaatst ze
  // een welkomst-bubble (met de live besluit-count + de top-3 urgente
  // besluiten als klikbare bullets) zodat ze aanwezig voelt vóórdat je iets
  // typt — niet een leeg invoerveld.
  let irisGreeted = false;
  function topDecisions(n = 3) {
    return items
      .filter((it) => !it.decision_status || it.decision_status === 'failed')
      .sort((a, b) => rankOf(a) - rankOf(b))
      .slice(0, n);
  }
  function irisGreet() {
    if (irisGreeted) return;
    irisGreeted = true;
    const sub = ($('today-sub') && $('today-sub').textContent || '').trim();
    const tops = topDecisions(3);
    let line = sub ? `${sub}.` : 'Ik ben er.';
    line += ' Waarmee kan ik je helpen? Of zal ik er meteen een voor je afhandelen?';
    // De top-3 als klikbare bullets: elke tik opent de detailweergave
    // (net als in de Besluiten-lijst). We bewaren de index in items[] zodat
    // de handler openDetail(items[idx]) kan aanroepen.
    let bullets = '';
    if (tops.length) {
      bullets = '<ul class="iris-greet-list">';
      tops.forEach((it) => {
        const idx = items.indexOf(it);
        const tag = it.project ? `${esc(it.project)} · ` : '';
        bullets += `<li><button type="button" class="iris-greet-item" data-greet-idx="${idx}">`
          + `<span class="material-symbols-outlined">chevron_right</span>`
          + `<span class="iris-greet-txt">${tag}${esc(it.title)}</span></button></li>`;
      });
      bullets += '</ul>';
    }
    chatHistory.push({ role: 'assistant', content: line, greeting: true, greetHtml: bullets });
    renderChat();
  }
  // Persoonlijke teruggroet op de "Hi 👋"-knop: kort, warm, met de stand van
  // de dag — alsof Iris opkijkt als je binnenkomt.
  function irisHi() {
    if (chatHistory.some((m) => m.hi)) return;
    chatHistory.push({ role: 'user', content: 'Hi 👋' });
    const open = items.filter((it) => !it.decision_status || it.decision_status === 'failed').length;
    const line = open
      ? `Hoi Vincent 👋 Fijn dat je er bent. ${open} besluit(en) wachten, ik hou ze voor je vast — zeg het als ik er eentje voor je moet uitvoeren.`
      : `Hoi Vincent 👋 Fijn dat je er bent. Niets wacht op je — rustig ritje vandaag.`;
    chatHistory.push({ role: 'assistant', content: line, hi: true });
    renderChat();
  }

  async function loadToday(token = loadToken) {
    const el = $('today-body');
    if (token === loadToken && !contextCache) el.innerHTML = skeletons(3);
    // Topbar toont de live begroeting ("Goedemiddag"); de grote body-titel
    // wordt de naam zodat de begroeting niet dubbel staat.
    const g = greeting();
    $('today-greeting').textContent = 'Vincent';
    const tbGreet = $('topbar-greeting');
    if (tbGreet) tbGreet.textContent = g;
    try {
      const data = await api('context');
      if (token !== loadToken) return;
      const ctx = data.payload;
      if (!ctx) {
        el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center">
          <span class="material-symbols-outlined text-primary text-4xl mb-2">cloud_off</span>
          <p class="font-body-lg text-body-lg text-on-surface-variant">Nog geen context gesynchroniseerd.<br>Draait AgentOS?</p></div>`;
        return;
      }
      contextCache = ctx;
      const stamp = data.generated_at ? `Stand van ${fmtDate(data.generated_at)}` : '';
      $('today-stamp').textContent = stamp;
      // Iris begroet zodra er context is (ze "ziet" je dag).
      const open = items.filter((i) => !i.decision_status || i.decision_status === 'failed').length;
      $('today-sub').textContent = open ? `${open} besluit(en) wachten op je` : 'Niets wacht op je';
      // Iris begroet pas ná de count-update, zodat ze de juiste "N wachten op
      // je" in haar welkomst zet.
      irisGreet();
      // Dit ís de dag — agenda en postvak eerst, precies zo opent een
      // secretaresse het gesprek ook. Pulse (bredere signalen) erna, met
      // mail/agenda eruit gefilterd want die staan al concreet hierboven.
      // Delegeren (snel-starten) komt pas na het overzicht, niet ervoor.
      el.innerHTML = [
        agendaPanel(ctx.agenda),
        mailPanel(ctx.mail),
        pulsePanel(ctx.pulse, ['mail', 'agenda']),
        quickPanel(ctx),
      ].filter(Boolean).join('');

      el.querySelectorAll('[data-cmd]').forEach((btn) => {
        btn.onclick = async () => {
          btn.disabled = true;
          const payload = {};
          if (btn.dataset.site) {
            const sel = $('quick-site');
            if (sel) payload.site = sel.value;
          }
          await sendCommand(btn.dataset.cmd, payload);
          setTimeout(() => { btn.disabled = false; }, 1500);
        };
      });
      const goto = el.querySelector('[data-goto-mail]');
      if (goto) {
        goto.onclick = () => { inboxFilter = 'mail'; show('inbox'); renderItems(); };
      }
      el.querySelectorAll('[data-open-mail-sheet]').forEach((b) => { b.onclick = () => openMailSheet(); });
      const agendaSheetBtn = el.querySelector('[data-open-agenda-sheet]');
      if (agendaSheetBtn) agendaSheetBtn.onclick = () => openAgendaSheet();
      bindMailRows(el, false);
    } catch (e) {
      if (e.message === 'login') return;
      el.innerHTML = `<div class="glass-panel rounded-xl p-6 text-error font-body-md">
        Kon je dag niet laden: ${esc(e.message)}</div>`;
    }
  }

  // ── Cijfers: analytics + SEO (onderaan de Briefings-tab) ─────────────────
  function analyticsPanel(ga) {
    if (!ga || ga.status !== 'ok') return ga ? sectionOff('analytics', 'Analytics', ga) : '';
    const c = ga.compare || {};
    const tile = (label, cmp) => {
      if (!cmp) return statTile(label, '–', '');
      const chip = cmp.pct == null ? '' :
        `<span class="font-label-caps text-[11px] ${cmp.pct > 0 ? 'text-green-400' : cmp.pct < 0 ? 'text-error' : 'text-on-surface-variant'}">${cmp.pct > 0 ? '▲' : cmp.pct < 0 ? '▼' : '·'} ${Math.abs(cmp.pct)}% vs vorige wk</span>`;
      return statTile(label, cmp.now, chip);
    };
    const ch = (ga.channels || []).slice(0, 5);
    const chMax = Math.max(1, ...ch.map((x) => x.sessions));
    return `<div class="space-y-stack-sm">
      <h3 class="font-label-caps text-label-caps text-primary uppercase">Websiteverkeer · 7 dagen</h3>
      <div class="grid grid-cols-2 gap-3">
        ${tile('Sessies', c.sessions)}
        ${tile('Gebruikers', c.users)}
      </div>
      ${ch.length ? `<div class="glass-panel rounded-xl p-4">
        <p class="font-label-caps text-label-caps text-on-surface-variant uppercase mb-3">Verkeersbronnen</p>
        <div class="space-y-2">${ch.map((x) => `
          <div>
            <div class="flex justify-between font-body-md text-[12px]">
              <span class="text-on-surface truncate">${esc(x.channel)}</span>
              <span class="text-on-surface-variant shrink-0">${x.sessions}</span>
            </div>
            <div class="h-1.5 bg-white/5 rounded mt-1 overflow-hidden">
              <div class="h-full bg-primary/70 rounded" style="width:${Math.round(100 * x.sessions / chMax)}%"></div>
            </div>
          </div>`).join('')}</div>
      </div>` : ''}
      ${(ga.top_pages || []).length ? `<details class="glass-panel rounded-xl">
        <summary class="p-4 cursor-pointer font-headline-sm text-[15px]">Best bekeken pagina's</summary>
        <ul class="px-4 pb-4 space-y-2">${ga.top_pages.slice(0, 6).map((p) => `
          <li class="flex justify-between gap-3 font-body-md text-[12px]">
            <span class="text-on-surface-variant truncate">${esc(p.title || p.path)}</span>
            <span class="text-on-surface shrink-0">${p.pageviews}</span>
          </li>`).join('')}</ul>
      </details>` : ''}
    </div>`;
  }

  function seoMoversPanel(seo) {
    if (!seo || seo.status !== 'ok') return '';
    const sites = (seo.sites || []).filter((s) => (s.risers || []).length || (s.fallers || []).length);
    if (!sites.length) return '';
    const row = (p, up) => `<li class="flex justify-between gap-3 font-body-md text-[12px]">
      <span class="text-on-surface-variant truncate">${esc(String(p.page_url || '').replace(/^https?:\/\/[^/]+/, '') || '/')}</span>
      <span class="${up ? 'text-green-400' : 'text-error'} shrink-0">${up ? '▲' : '▼'} ${Math.abs(p.delta_clicks)}</span>
    </li>`;
    return `<div class="space-y-stack-sm">
      <h3 class="font-label-caps text-label-caps text-primary uppercase">Pagina's in beweging</h3>
      ${sites.map((s) => `<div class="glass-panel rounded-xl p-4">
        <p class="font-headline-sm text-[15px] mb-2">${esc(s.name)}</p>
        <ul class="space-y-1">
          ${(s.risers || []).slice(0, 3).map((p) => row(p, true)).join('')}
          ${(s.fallers || []).slice(0, 3).map((p) => row(p, false)).join('')}
        </ul>
        ${(s.fallers || []).length ? `<button data-cmd="seo_refresh" data-site-id="${esc(s.site_id)}"
          class="mt-3 w-full bg-transparent border border-primary/30 text-primary font-headline-sm text-[13px] py-2 rounded-lg active:scale-[0.98] transition-all">
          Laat Iris de dalers verrijken</button>` : ''}
      </div>`).join('')}
    </div>`;
  }

  // ── Cloud-Iris chat ──────────────────────────────────────────────────────
  const chatHistory = [];
  const PROPOSAL_LABELS = { approve: 'Goedkeuren', send: 'Versturen', reject: 'Afwijzen', dismiss: 'Wegklikken' };

  // Wat Iris zélf startte (commando's, altijd achter een gate) en wat ze
  // vóórstelt (gate-passerende besluiten — die blijven een menselijke tik).
  function effectsHtml(m) {
    let html = '';
    for (const c of m.commands || []) {
      html += `<div class="flex items-center gap-2 mt-2 text-[12px] font-label-caps ${c.queued ? 'text-primary' : 'text-on-surface-variant'}">
        <span class="material-symbols-outlined text-[14px]">${c.queued ? 'bolt' : 'schedule'}</span>
        ${esc(c.queued ? `GESTART · ${c.label}` : `STOND AL IN DE RIJ · ${c.label}`)}
      </div>`;
    }
    (m.proposals || []).forEach((p, i) => {
      html += `<div class="mt-2 bg-surface-container-lowest/60 border border-primary/20 rounded-lg p-3">
        <p class="font-body-md text-[12px] text-on-surface-variant">${esc(p.why || '')}</p>
        <p class="font-body-md text-[13px] text-on-surface mt-1 truncate">${esc(p.title || p.item_key)}</p>
        <button class="mt-2 w-full bg-primary text-on-primary font-headline-sm text-[13px] py-2 rounded-lg active:scale-[0.98] transition-all"
          data-prop="${m.idx}:${i}">${esc(PROPOSAL_LABELS[p.action] || p.action)} — jij beslist</button>
      </div>`;
    });
    return html;
  }

  function renderChat(pending = false) {
    const el = $('chat-messages');
    chatHistory.forEach((m, i) => { m.idx = i; });
    el.innerHTML = chatHistory.map((m) => m.role === 'user'
      ? `<div class="flex justify-end fade-up"><div class="chat-msg me">${esc(m.content)}</div></div>`
      : `<div class="flex justify-start fade-up"><div class="chat-msg iris${m.greeting ? ' is-greeting' : ''}">${mdLite(m.content)}${m.greetHtml || ''}${effectsHtml(m)}</div></div>`).join('')
      // Levendige typing-indicator i.p.v. een statische "IRIS DENKT NA…" —
      // drie stippen die stuiteren, zodat je ziet dat Iris écht aan het
      // schrijven is (niet een vast label dat altijd aanstaat).
      + (pending ? '<div class="flex justify-start fade-up"><div class="chat-msg iris"><div class="chat-typing"><span></span><span></span><span></span></div></div></div>' : '');
    el.querySelectorAll('[data-prop]').forEach((btn) => {
      btn.onclick = async () => {
        const [mi, pi] = btn.dataset.prop.split(':').map(Number);
        const p = (chatHistory[mi].proposals || [])[pi];
        if (!p) return;
        btn.disabled = true;
        try {
          const r = await api('decide', 'POST', { item_key: p.item_key, action: p.action, payload: {} });
          btn.textContent = r.queued ? '✓ Vastgelegd — AgentOS voert het uit' : 'Stond al in de wachtrij';
          toast('Besluit vastgelegd', 'ok', 'check_circle');
          refresh();
        } catch (e) {
          if (e.message !== 'login') { toast(e.message, 'err'); btn.disabled = false; }
        }
      };
    });
    // Klikbare bullets in Iris' openingsgroet → open de detailweergave.
    el.querySelectorAll('[data-greet-idx]').forEach((btn) => {
      btn.onclick = () => {
        const it = items[Number(btn.dataset.greetIdx)];
        if (it) openDetail(it);
      };
    });
    el.scrollTop = el.scrollHeight;
  }
  // ── Agenda-opdracht (spraak/tekst -> calendar_add) ────────────────────────
  // Vrije zin of ingesproken commando -> parser in de backend -> agenda-voorstel
  // (review-gate: boeken gebeurt pas als Vincent het in Iris Remote goedkeurt).
  $('agenda-form').onsubmit = async (e) => {
    e.preventDefault();
    const input = $('agenda-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
      const r = await api('command', 'POST', { action: 'calendar_add', payload: { text } });
      toast(r.queued ? `Klaargezet: ${r.label || 'Afspraak'}` : 'Stond al in de wachtrij',
        r.queued ? 'ok' : '', r.queued ? 'event_available' : 'schedule');
    } catch (err) {
      if (err.message !== 'login') toast(err.message, 'err');
    }
  };

  // Microfoon: gebruik de browser Web Speech API (geen API-key nodig). Bij
  // geen ondersteuning blijft het veld gewoon typbaar.
  (function setupMic() {
    const mic = $('agenda-mic');
    const icon = $('agenda-mic-icon');
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { mic.title = 'Spraak niet ondersteund in deze browser — typ je opdracht'; return; }
    const rec = new SR();
    rec.lang = 'nl-NL';
    rec.interimResults = false;
    rec.continuous = false;
    let listening = false;
    rec.onresult = (ev) => {
      const txt = ev.results[0][0].transcript;
      $('agenda-input').value = txt;
    };
    // `is-luistert` i.p.v. `bg-primary`: die Tailwind-klasse verloor het van
    // `.chat-composer button` in style.css, dus zag je niet dát hij opnam —
    // bij spraak is dat het enige signaal dat je hebt.
    const stoppen = () => {
      listening = false;
      icon.textContent = 'mic';
      mic.classList.remove('is-luistert');
      mic.setAttribute('aria-label', 'Spraakopname');
    };
    rec.onend = stoppen;
    rec.onerror = stoppen;
    mic.onclick = () => {
      if (listening) { rec.stop(); return; }
      try {
        rec.start();
        listening = true;
        icon.textContent = 'stop';
        mic.classList.add('is-luistert');
        mic.setAttribute('aria-label', 'Opname stoppen');
      } catch (_) { /* al actief — negeer */ }
    };
  })();

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
      // Alleen role+content terugsturen: de effect-velden zijn UI-staat en
      // horen niet in de modelgeschiedenis.
      const history = chatHistory.slice(-12).map((m) => ({ role: m.role, content: m.content }));
      const r = await fetch('/api/iris', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages: history }) });
      if (r.status === 401) { show('login'); return; }
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      chatHistory.push({ role: 'assistant', content: data.reply,
        commands: data.commands || [], proposals: data.proposals || [] });
      if ((data.commands || []).some((c) => c.queued)) refresh();
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
  // De guard stond op `main.scrollTop`, maar `main` is geen scroll-container —
  // het document scrolt. `main.scrollTop` was dus altijd 0 en élke neerwaartse
  // veeg activeerde de indicator: scrollde je halverwege een lange briefing
  // omhoog, dan herlaadde de app het scherm onder je vinger vandaan. De juiste
  // vraag is of de pagína bovenaan staat.
  let ptrStartY = 0, ptrStartX = 0, ptrPull = 0, ptrActive = false;
  const ptr = $('ptr');
  const main = document.querySelector('main');
  const atTop = () => (window.scrollY || document.documentElement.scrollTop || 0) <= 0;
  function ptrReset() { ptrActive = false; ptrPull = 0; ptr.style.transform = 'translateY(-64px)'; }
  if (main) {
    main.addEventListener('touchstart', (e) => {
      // Eén vinger, pagina bovenaan, geen sheet open (dat heeft zijn eigen gebaar).
      if (e.touches.length !== 1 || !atTop() || ptrActive) return;
      if (!$('detail-overlay').hidden) return;
      ptrStartY = e.touches[0].clientY; ptrStartX = e.touches[0].clientX; ptrActive = true;
    }, { passive: true });
    main.addEventListener('touchmove', (e) => {
      if (!ptrActive) return;
      const dy = e.touches[0].clientY - ptrStartY;
      const dx = Math.abs(e.touches[0].clientX - ptrStartX);
      // Een horizontale veeg is de filter-chiprij of een brede tabel, geen refresh.
      if (dx > Math.abs(dy) && dx > 12) { ptrReset(); return; }
      // Tijdens de veeg alsnog van bovenaan weggescrold: afbreken.
      if (dy < 0 || !atTop()) { ptrReset(); return; }
      ptrPull = dy;
      if (ptrPull > 0) { ptr.style.transform = `translateY(${Math.min(ptrPull, 64) - 40}px)`; }
    }, { passive: true });
    main.addEventListener('touchend', async () => {
      if (!ptrActive) return;
      ptrActive = false;
      if (ptrPull > 56) {
        ptr.classList.add('spinning'); ptr.style.transform = 'translateY(0)';
        await reloadActive();
        setTimeout(() => { ptr.classList.remove('spinning'); ptr.style.transform = 'translateY(-64px)'; }, 500);
      } else { ptr.style.transform = 'translateY(-64px)'; }
      ptrPull = 0;
    });
    main.addEventListener('touchcancel', ptrReset, { passive: true });
  }

  // ── Start + realtime loop ────────────────────────────────────────────────
  function startPolling() {
    if (polling) return;
    polling = true;
    let tick = 0;
    setInterval(async () => {
      const hidden = document.visibilityState === 'hidden';
      const onInbox = !$('view-inbox').hidden;
      const onSystem = !$('view-system').hidden;
      const onToday = !$('view-today').hidden;
      const onNote = !$('view-note').hidden;
      if (hidden) return; // batterij/CPU besparen in achtergrond
      tick += 1;
      const data = await refreshRaw();
      if (!data) return;
      setSyncPill(data.last_push);
      items = data.items || [];
      if (onInbox) renderItems();
      else if (onSystem) loadSystem();
      // De context ververst lokaal hooguit elke paar minuten; hem elke 20
      // seconden ophalen kost alleen data zonder nieuwe informatie.
      else if (onToday && tick % 3 === 0) loadToday();
      // Zonder deze tak bleef een notitie op dit scherm eeuwig op "PENDING"
      // staan totdat je wegnavigeerde en terugkwam — de achtergrondpoll liep
      // overal behalve hier, dus de klok tikte wél maar het scherm loog erover.
      else if (onNote) loadNotes();
    }, 20000);
  }
  show('today');
  startPolling();
})();
