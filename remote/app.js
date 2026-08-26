/* Impact OS Remote — vanilla SPA in de glass/iris-blauw designtaal. Praat alleen met /api/ui.
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
  // de lijst zélf hoe oud de stand is en dat besluiten pas landen zodra ImpactOS
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
      ? `Bevroren stand van ${fmtDate(lastPush)} — ImpactOS synct niet. Je besluiten blijven in de wachtrij staan en worden uitgevoerd zodra de verbinding terug is.`
      : 'Nog nooit gesynchroniseerd — ImpactOS heeft deze cloud nog niet bereikt.';
  }

  function spinSync(on) {
    const icon = $('syncTrigger')?.querySelector('.material-symbols-outlined');
    if (icon) icon.classList.toggle('animate-spin', on);
  }

  // ── Views + nav ──────────────────────────────────────────────────────────
  const views = ['login', 'today', 'inbox', 'briefing', 'rituals', 'note', 'system', 'onboarding'];

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
    // Het inlogscherm is geen werkscherm: geen begroeting, geen sync-/uitlogknop,
    // geen tabbalk — alleen het logo in de kop blijft staan.
    const isLogin = view === 'login';
    $('topbar-greeting').hidden = isLogin;
    $('sync-pill').hidden = isLogin;
    $('syncTrigger').hidden = isLogin;
    $('logoutIconBtn').hidden = isLogin;
    $('bottomnav').hidden = isLogin;
    // Een tab-wissel was een harde snap (hidden -> zichtbaar zonder overgang).
    // .fade-up opnieuw triggeren vergt een geforceerde reflow — anders ziet de
    // browser dezelfde klasse en speelt de animatie niet opnieuw af.
    const shown = $(`view-${view}`);
    shown.classList.remove('fade-up'); void shown.offsetWidth; shown.classList.add('fade-up');
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
    if (view === 'rituals') loadRituals(myToken);
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
    else if (active === 'rituals') loadRituals();
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
    if (!confirm('Uitloggen uit Impact OS Remote?')) return;
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
    // Zonder dit bleef een net beslist item in Iris' openingsgroet staan tot
    // de eerstvolgende chatactie — de bullets worden hier ná elke ophaal
    // opnieuw uit de verse items-stand gebouwd (zie greetBulletsHtml).
    if (chatHistory.some((m) => m.greeting)) renderChat();
    const open = items.filter((i) => !i.decision_status || i.decision_status === 'failed').length;
    $('inbox-sub').textContent = open === 1 ? '1 besluit in de wachtrij' : `${open} besluiten in de wachtrij`;
  }

  // ── Actiecentrum ──────────────────────────────────────────────────────────
  const KIND_META = {
    content: { icon: 'article', label: 'Wachtrij · Artikel' },
    mail: { icon: 'mail', label: 'Helpdesk · Mail' },
    personal_mail: { icon: 'mark_email_read', label: 'Postvak · Concept' },
    social: { icon: 'forum', label: 'Social · Concept' },
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
      pending: ['schedule', 'bdg pending', 'Wacht op ImpactOS-sync'],
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
    social: 'social',
    error: 'fout',
  };
  const GROUPS = [
    ['all', 'Alles', 'inbox'],
    ['fout', 'Fouten', 'error'],
    ['mail', 'Mail', 'mail'],
    ['social', 'Social', 'forum'],
    ['actie', 'Wachtrij', 'pending_actions'],
    ['info', 'Info', 'info'],
  ];
  const groupOf = (it) => GROUP_OF[it.dismiss_kind] || 'info';
  function rankOf(it) {
    if (it.decision_status === 'failed') return 0;
    if (it.dismiss_kind === 'error') return 1;
    // Mail en social tellen hier als 'actie': een eigen filtergroep mag niet
    // betekenen dat een wachtend antwoord onder de artikelen zakt.
    if (['actie', 'mail', 'social'].includes(groupOf(it))) return it.decision_status === 'pending' ? 3 : 2;
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
    send: { mail: 'Versturen', personal_mail: 'Versturen', social: 'Plaats antwoord' },
    reject: { content: 'Afwijzen', mail: 'Afwijzen', personal_mail: 'Afwijzen', social: 'Afwijzen', outreach: 'Afwijzen (→ lost)', calendar: 'Afwijzen' },
    edit: { mail: 'Bewerking opslaan', social: 'Bewerking opslaan' },
  };
  const ACTIONS_PER_KIND = {
    content: ['approve', 'reject'], mail: ['send', 'edit', 'reject'],
    personal_mail: ['send', 'reject'],
    social: ['send', 'edit', 'reject'],
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
    if (it.dismiss_kind === 'social') {
      return `
        <p class="font-body-md text-body-md text-on-surface-variant mt-2">
          <b class="text-on-surface">${esc(d.platform || '?')}</b> · ${esc(d.author_name || d.author_handle || 'iemand')}
          ${d.kind ? ` · ${esc(d.kind)}` : ''}</p>
        <pre class="whitespace-pre-wrap break-words bg-surface-container-lowest/50 border border-white/5 rounded-lg p-3 font-body-md text-body-md text-on-surface-variant mt-2">${esc(d.text || '')}</pre>
        <p class="font-body-md text-body-md text-on-surface-variant mt-3">Concept-antwoord — bewerk gerust vóór plaatsen:</p>
        <textarea id="edit-text" rows="8" class="${inputCls}">${esc(d.draft_body || '')}</textarea>`;
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
    if (it.dismiss_kind === 'social' && action === 'edit') { const t = $('edit-text'); if (t) p.text = t.value; }
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
            status.innerHTML = '<span class="text-primary">✓ Besluit vastgelegd — ImpactOS voert het uit bij de volgende sync.</span>';
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
        <path d="${area}" fill="rgba(156,143,255,0.12)"></path>
        <path d="${line}" fill="none" stroke="#9c8fff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
        <circle cx="${x(li).toFixed(1)}" cy="${y(vals[li]).toFixed(1)}" r="3.5" fill="#9c8fff" stroke="#121118" stroke-width="2"></circle>
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
      if (ctx) html += analyticsPanel(ctx.analytics) + seoMoversPanel(ctx.seo) + orchestratorPanel(ctx.orchestrator);

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
        <span>${label} — ImpactOS heeft nog nooit gesynchroniseerd. Controleer of de lokale machine draait.</span></div>`;
      return;
    }
    const age = Math.round((Date.now() - new Date(lastPushAt)) / 60000);
    if (age < 180) {
      el.innerHTML = `<div class="flex items-start gap-2 text-on-surface-variant font-body-md text-body-md">
        <span class="material-symbols-outlined text-[18px] mt-0.5 text-primary">schedule</span>
        <span>${label} — ImpactOS haalt ze elke ~3 min op, laatste sync ${age < 1 ? 'net' : `${age}m geleden`}.</span></div>`;
    } else {
      el.innerHTML = `<div class="flex items-start gap-2 text-warn font-body-md text-body-md">
        <span class="material-symbols-outlined text-[18px] mt-0.5">warning</span>
        <span>${label} — ImpactOS lijkt al ${Math.round(age / 60)}u offline. Ze staan veilig te wachten en landen zodra de machine terug is.</span></div>`;
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

  // ── Rituelen — eigen module: ochtend/avond, week, wins, doelen ────────────
  // Los van Vandaag (werk) en los van Cijfers (projecten): dit is Vincents
  // persoonlijke kant, en Iris leest 'm alleen voor tóón (zie system prompt
  // in api/iris.js) — hij levert hier dus bewust geen commando-knoppen op.
  let ritualsCache = null;

  async function loadRituals(token = loadToken) {
    const el = $('rituals-body');
    if (token === loadToken && !ritualsCache) el.innerHTML = skeletons(3);
    try {
      const data = await api('context');
      if (token !== loadToken) return;
      const ctx = data.payload;
      const r = ctx && ctx.rituals;
      if (!r) {
        el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center">
          <span class="material-symbols-outlined text-primary text-4xl mb-2">cloud_off</span>
          <p class="font-body-lg text-body-lg text-on-surface-variant">Nog geen context gesynchroniseerd.<br>Draait ImpactOS?</p></div>`;
        return;
      }
      ritualsCache = r;
      el.innerHTML = ritualsHtml(r);
      bindRituals(el);
    } catch (e) {
      if (e.message === 'login') return;
      el.innerHTML = `<div class="glass-panel rounded-xl p-6 text-error font-body-md">Kon rituelen niet laden: ${esc(e.message)}</div>`;
    }
  }

  function ritualsHtml(r) {
    if (r.status && r.status !== 'ok') return sectionOff('self_improvement', 'Rituelen', r);
    const t = r.today || {};
    const streaks = r.streaks || {};
    const ws = r.weekly_start || {};
    const goals = r.open_goals || [];
    const wins = r.recent_wins || [];
    const gratitude = (r.gratitude_today || '').trim();

    const rbadge = (done, label) => `<span class="rit-badge${done ? ' is-done' : ''}">${done ? '✓' : '–'} ${esc(label)}</span>`;

    return `
    <div class="glass-panel rounded-xl p-4 space-y-3">
      <div class="card-head">
        <div class="card-head-icon"><span class="material-symbols-outlined">self_improvement</span></div>
        <div class="min-w-0 flex-1">
          <p class="card-head-title">Vandaag</p>
          <p class="card-head-meta">${streaks.morning || 0}d ochtend · ${streaks.evening || 0}d avond op rij</p>
        </div>
      </div>
      <div class="flex flex-wrap gap-1.5">
        ${rbadge(t.morning_done, 'Ochtend')}${rbadge(t.evening_done, 'Avond')}
        ${rbadge(t.weekly_start_done, 'Weekstart')}${rbadge(t.weekly_review_done, 'Weekreview')}
      </div>
      ${r.energy_today != null ? `<p class="font-body-md text-[12px] text-on-surface-variant">Energie: <span class="text-on-surface">${esc(r.energy_today)}/10</span></p>` : ''}
      ${gratitude ? `<div class="rit-quote">"${esc(gratitude)}"</div>` : ''}
      <div class="grid grid-cols-2 gap-2">
        <button data-rit-open="morning" class="rit-btn">${t.morning_done ? 'Ochtend bekijken' : 'Ochtend loggen'}</button>
        <button data-rit-open="evening" class="rit-btn">${t.evening_done ? 'Avond bekijken' : 'Avond loggen'}</button>
      </div>
    </div>

    ${ws.week_intention || (ws.main_goals || []).length ? `<div class="glass-panel rounded-xl p-4 space-y-2">
      <p class="font-label-caps text-label-caps text-primary uppercase">Deze week</p>
      ${ws.week_intention ? `<p class="font-body-lg text-[14px] text-on-surface">${esc(ws.week_intention)}</p>` : ''}
      ${(ws.main_goals || []).filter(Boolean).length ? `<ul class="space-y-1 mt-1">
        ${ws.main_goals.filter(Boolean).map((g) => `<li class="font-body-md text-[12px] text-on-surface-variant flex gap-2"><span class="text-primary">•</span>${esc(g)}</li>`).join('')}
      </ul>` : ''}
    </div>` : ''}

    <div class="glass-panel rounded-xl p-4 space-y-3">
      <div class="flex items-center justify-between">
        <p class="font-label-caps text-label-caps text-primary uppercase">Persoonlijke doelen</p>
      </div>
      ${goals.length ? `<div class="space-y-3">${goals.slice(0, 6).map((g) => `
        <div class="rit-goal" data-goal-id="${esc(g.id)}">
          <div class="flex justify-between gap-2 font-body-md text-[12px]">
            <span class="text-on-surface truncate">${esc(g.title)}</span>
            <span class="text-primary shrink-0">${esc(g.progress)}%</span>
          </div>
          <div class="rit-bar"><div style="width:${Math.max(0, Math.min(100, g.progress))}%"></div></div>
          <div class="flex gap-1.5 mt-1.5">
            <button class="rit-mini" data-goal-step="-10">−10%</button>
            <button class="rit-mini" data-goal-step="10">+10%</button>
            ${g.progress < 100 ? `<button class="rit-mini" data-goal-done="1">Afgerond</button>` : ''}
          </div>
        </div>`).join('')}</div>`
        : `<p class="font-body-md text-[12px] text-on-surface-variant">Geen open persoonlijke doelen.</p>`}
    </div>

    <div class="glass-panel rounded-xl p-4 space-y-3">
      <div class="flex items-center justify-between">
        <p class="font-label-caps text-label-caps text-primary uppercase">Wins (cookie jar)</p>
        <button data-rit-open="win" class="rit-btn is-secundair !py-1.5 !px-3 !text-[12px]">+ Win</button>
      </div>
      ${wins.length ? `<ul class="space-y-1.5">${wins.map((w) => `
        <li class="flex justify-between gap-3 font-body-md text-[12px]">
          <span class="text-on-surface truncate">${esc(w.title)}</span>
          <span class="text-on-surface-variant shrink-0">${esc(w.date)}</span>
        </li>`).join('')}</ul>`
        : `<p class="font-body-md text-[12px] text-on-surface-variant">Nog geen wins gelogd.</p>`}
    </div>`;
  }

  function ritInput(id, label, value, opts = {}) {
    const tag = opts.textarea
      ? `<textarea id="${id}" rows="${opts.rows || 2}" class="rit-field" placeholder="${esc(opts.placeholder || '')}">${esc(value || '')}</textarea>`
      : `<input id="${id}" class="rit-field" type="${opts.type || 'text'}" ${opts.min !== undefined ? `min="${opts.min}"` : ''} ${opts.max !== undefined ? `max="${opts.max}"` : ''} value="${esc(value ?? '')}" placeholder="${esc(opts.placeholder || '')}">`;
    return `<label class="rit-label">${esc(label)}</label>${tag}`;
  }

  function openMorningSheet(r) {
    const m = r.morning || {};
    const dank = (m.dankbaarheid && m.dankbaarheid.length) ? m.dankbaarheid : ['', '', ''];
    const body = `
      ${ritInput('rit-m-intentie', 'Intentie voor vandaag', m.intentie, { textarea: true, placeholder: 'Vandaag focus ik op...' })}
      <label class="rit-label">Energie (1-10)</label>
      <input id="rit-m-energy" class="rit-field" type="number" min="1" max="10" value="${m.energy_level || 7}">
      <label class="rit-label">3× dankbaarheid</label>
      <div class="space-y-2">
        ${[0, 1, 2].map((i) => `<input id="rit-m-dank${i}" class="rit-field" value="${esc(dank[i] || '')}" placeholder="Ik ben dankbaar voor...">`).join('')}
      </div>
      <button id="rit-m-save" class="rit-btn is-primair w-full mt-1">Opslaan</button>`;
    const card = openSheet('Rituelen', 'Ochtendritueel', body);
    card.querySelector('#rit-m-save').onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      const payload = {
        nonce: String(Date.now()),
        intentie: $('rit-m-intentie').value,
        energyLevel: parseInt($('rit-m-energy').value, 10) || 7,
        dankbaarheid: [0, 1, 2].map((i) => $(`rit-m-dank${i}`).value).filter(Boolean),
      };
      await sendCommand('ritual_morning_save', payload);
      closeDetail();
      loadRituals();
    };
  }

  function openEveningSheet(r) {
    const e0 = r.evening || {};
    const top3 = (e0.tomorrow_top3 && e0.tomorrow_top3.length) ? e0.tomorrow_top3 : ['', '', ''];
    const body = `
      ${ritInput('rit-e-goed', 'Wat ging goed vandaag?', e0.what_went_well, { textarea: true })}
      <label class="rit-label">Energie nu (1-10)</label>
      <input id="rit-e-energy" class="rit-field" type="number" min="1" max="10" value="${e0.energy_level || 5}">
      <label class="rit-label">Top 3 voor morgen</label>
      <div class="space-y-2">
        ${[0, 1, 2].map((i) => `<input id="rit-e-top${i}" class="rit-field" value="${esc(top3[i] || '')}" placeholder="Prioriteit ${i + 1}">`).join('')}
      </div>
      ${ritInput('rit-e-dank', 'Waar ben je dankbaar voor vandaag?', e0.gratitude, { textarea: true })}
      <button id="rit-e-save" class="rit-btn is-primair w-full mt-1">Opslaan</button>`;
    const card = openSheet('Rituelen', 'Avondritueel', body);
    card.querySelector('#rit-e-save').onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      const payload = {
        nonce: String(Date.now()),
        whatWentWell: $('rit-e-goed').value,
        energyLevel: parseInt($('rit-e-energy').value, 10) || 5,
        tomorrowTop3: [0, 1, 2].map((i) => $(`rit-e-top${i}`).value).filter(Boolean),
        gratitude: $('rit-e-dank').value,
      };
      await sendCommand('ritual_evening_save', payload);
      closeDetail();
      loadRituals();
    };
  }

  function openWinSheet() {
    const body = `
      ${ritInput('rit-w-title', 'Titel', '', { placeholder: 'Wat heb je bereikt?' })}
      ${ritInput('rit-w-desc', 'Omschrijving (optioneel)', '', { textarea: true })}
      <button id="rit-w-save" class="rit-btn is-primair w-full mt-1">Toevoegen</button>`;
    const card = openSheet('Rituelen', 'Nieuwe win', body);
    card.querySelector('#rit-w-save').onclick = async (e) => {
      const title = $('rit-w-title').value.trim();
      if (!title) { toast('Titel is verplicht', 'err'); return; }
      e.currentTarget.disabled = true;
      await sendCommand('ritual_win_add', {
        nonce: String(Date.now()), title, description: $('rit-w-desc').value,
      });
      closeDetail();
      loadRituals();
    };
  }

  function bindRituals(el) {
    el.querySelectorAll('[data-rit-open]').forEach((btn) => {
      btn.onclick = () => {
        const kind = btn.dataset.ritOpen;
        if (kind === 'morning') openMorningSheet(ritualsCache || {});
        else if (kind === 'evening') openEveningSheet(ritualsCache || {});
        else if (kind === 'win') openWinSheet();
      };
    });
    // Doel-voortgang: optimistisch bijgewerkt in de kaart, echt commando erna.
    el.querySelectorAll('[data-goal-step]').forEach((btn) => {
      btn.onclick = async () => {
        const card = btn.closest('[data-goal-id]');
        const gid = Number(card.dataset.goalId);
        const bar = card.querySelector('.rit-bar > div');
        const cur = Math.max(0, Math.min(100, parseInt(bar.style.width, 10) || 0));
        const next = Math.max(0, Math.min(100, cur + Number(btn.dataset.goalStep)));
        bar.style.width = `${next}%`;
        card.querySelector('.text-primary').textContent = `${next}%`;
        await sendCommand('ritual_goal_progress', { goal_id: gid, progress: next });
      };
    });
    el.querySelectorAll('[data-goal-done]').forEach((btn) => {
      btn.onclick = async () => {
        const card = btn.closest('[data-goal-id]');
        const gid = Number(card.dataset.goalId);
        card.style.opacity = '0.4';
        await sendCommand('ritual_goal_progress', { goal_id: gid, progress: 100, completed: true });
        setTimeout(() => loadRituals(), 800);
      };
    });
  }

  // ── Systeem ──────────────────────────────────────────────────────────────
  async function loadSystem(token = loadToken) {
    try {
      const [itemsData, outboxData, sessionData, googleData] = await Promise.all([
        api('items'), api('outbox'), api('sessions'), api('google-status').catch(() => null),
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
                <p class="font-body-md text-body-md text-on-surface-variant">Lokale ImpactOS-bridge:
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

      const gStatus = $('google-status');
      if (gStatus) {
        if (!googleData || !googleData.configured) {
          gStatus.innerHTML = `
            <div class="glass-panel rounded-xl p-6 fade-up">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-on-surface-variant">calendar_month</span>
                <p class="font-body-md text-body-md text-on-surface-variant">Live agenda/GSC: nog niet gekoppeld — komt automatisch mee bij de eerstvolgende sync zodra ImpactOS een Google-agenda heeft.</p>
              </div>
            </div>`;
        } else {
          const gAge = googleData.google_synced_at ? Math.round((Date.now() - new Date(googleData.google_synced_at)) / 60000) : null;
          const hasError = !!googleData.google_last_error;
          gStatus.innerHTML = `
            <div class="glass-panel rounded-xl p-6 border-l-4 ${hasError ? 'border-l-error' : 'border-l-primary'} fade-up">
              <div class="flex items-center gap-2 mb-2">
                <span class="material-symbols-outlined ${hasError ? 'text-error' : 'text-primary'}">calendar_month</span>
                <h3 class="font-headline-sm text-headline-sm">Live agenda &amp; GSC</h3>
              </div>
              <p class="font-body-md text-body-md text-on-surface-variant">
                Agenda-credential ontvangen ${gAge === null ? 'nooit' : gAge < 1 ? 'zojuist' : gAge + ' min geleden'}
                · GSC-sites gekoppeld: ${googleData.gsc_site_count ?? 0}
              </p>
              ${hasError ? `<p class="font-body-md text-body-md text-error mt-2">⚠ ${esc(googleData.google_last_error)} (${fmtDate(googleData.google_last_error_at)})</p>` : ''}
            </div>`;
        }
      }

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
      renderOnboardingList();
    } catch (e) { /* login afgehandeld */ }
  }

  // ── Iris-onboarding-wizard ───────────────────────────────────────────────
  // Verhuisd hierheen vanaf de lokale ImpactOS-SPA (localhost:1250, onbereik-
  // baar voor een klant zoals Nicole) — zie backend/domains/bridge/context.py:
  // build_onboarding voor de databron en backend/domains/bridge/actions.py:
  // _cmd_onboarding_* voor wat er met een ingediende stap gebeurt. Alles hier
  // is *asynchroon*: een tik op "Volgende" zet een commando klaar (net als
  // elke andere knop in deze app) dat pas bij de eerstvolgende lokale sync
  // (standaard elke paar minuten) echt wordt toegepast — er is bewust geen
  // live round-trip naar de lokale backend, die praat deze app nooit direct.
  const ONBOARDING_PRESETS = {
    laag: ['Laag', 'Iris start voorzichtig, weinig per dag'],
    normaal: ['Normaal', 'een gezond dagelijks tempo'],
    hoog: ['Hoog', 'Iris mag flink doorpakken'],
  };

  async function ensureContext(force = false) {
    if (contextCache && !force) return contextCache;
    try {
      const data = await api('context');
      contextCache = data.payload || null;
    } catch (e) { if (e.message !== 'login') console.warn('ensureContext', e); }
    return contextCache;
  }

  function onboardingSite(ctx, siteId) {
    const sites = (ctx && ctx.onboarding && ctx.onboarding.sites) || [];
    return sites.find((s) => s.site_id === siteId) || null;
  }

  // Voor een tenant-eigen klant (zoals Nicole) die geen SEO-`site` heeft in
  // de backend: behandel de tenant zelf als de te onboarden "site", zodat de
  // OAuth-koppeling op site_id=<tenant> terechtkomt en de cloud-lezer die
  // per-tenant credentials ook weer uitleest. Valt terug op de echte sites.
  function tenantSite(ctx) {
    const sites = (ctx && ctx.onboarding && ctx.onboarding.sites) || [];
    if (sites.length) return null; // echte sites hebben voorrang
    const name = (ctx && ctx.tenant && ctx.tenant.name) || window.__tenantName || 'Klant';
    const slug = (ctx && ctx.tenant && ctx.tenant.slug) || '';
    if (!slug) return null;
    return {
      site_id: slug,
      project: name,
      onboarded_at: null,
      steps: {
        '1_bedrijfsdoel': { done: false },
        '2_schrijfstijl': { done: false },
        '4_autonomie': { done: false, current: null, presets: {} },
        '3_kanalen': { google: null, google_configured: false,
                       microsoft: null, microsoft_configured: false },
      },
    };
  }

  async function renderOnboardingList() {
    const host = $('onboarding-list');
    if (!host) return;
    const ctx = await ensureContext();
    const sites = (ctx && ctx.onboarding && ctx.onboarding.sites) || [];
    // Een tenant zonder SEO-sites krijgt zijn eigen onboarding-rij.
    const ts = (!sites.length) ? tenantSite(ctx) : null;
    const all = ts ? [ts] : sites;
    if (!all.length) {
      host.innerHTML = '<p class="font-body-md text-body-md text-on-surface-variant">Nog geen klanten.</p>';
      return;
    }
    host.innerHTML = all.map((s) => {
      const done = [s.steps['1_bedrijfsdoel'].done, s.steps['2_schrijfstijl'].done, s.steps['4_autonomie'].done]
        .filter(Boolean).length;
      const label = s.onboarded_at ? 'Onboard' : `${done}/3 stappen`;
      return `<div class="flex items-center justify-between gap-3 py-2 border-b border-white/5 last:border-0">
        <div class="min-w-0">
          <p class="font-body-lg text-body-lg text-on-surface truncate">${esc(s.project)}</p>
          <p class="font-body-md text-body-md text-on-surface-variant">${esc(label)}</p>
        </div>
        <button class="font-label-caps text-label-caps text-primary shrink-0" data-onb-open="${esc(s.site_id)}">
          ${s.onboarded_at ? 'BEKIJK' : 'DOORGAAN'}</button>
      </div>`;
    }).join('');
    host.querySelectorAll('[data-onb-open]').forEach((b) => {
      b.onclick = () => openOnboarding(b.dataset.onbOpen);
    });
  }

  $('onboardingNewBtn').onclick = async () => {
    const name = prompt('Naam van de nieuwe klant (zoals hij overal in Impact OS moet heten):');
    if (!name || !name.trim()) return;
    await sendCommand('onboarding_new_client', { name: name.trim() }, name.trim());
    toast('Verschijnt hieronder zodra ImpactOS gesynchroniseerd heeft.', '', 'hourglass_top');
  };

  $('onboardingBack').onclick = () => show('system');

  let onbNotice = null; // {step:'3', kind:'connecting'|'error', text} — ná OAuth-redirect
  const onbProgress = (step) => Array.from({ length: 4 }, (_, i) =>
    `<span class="h-[3px] flex-1 rounded-full ${i < step ? 'bg-primary' : 'bg-white/10'}"></span>`).join('');

  async function openOnboarding(siteId, step = 1) {
    show('onboarding');
    const host = $('onboarding-wizard');
    host.innerHTML = skeletons(1);
    const ctx = await ensureContext(true);
    let site = onboardingSite(ctx, siteId);
    if (!site) {
      // Geen echte SEO-site gevonden — probeer de virtuele tenant-site
      // (bijv. Nicole, die geen eigen site-rij heeft maar wel een tenant).
      const ts = tenantSite(ctx);
      if (ts && ts.site_id === siteId) site = ts;
    }
    if (!site) {
      host.innerHTML = `<div class="glass-panel rounded-xl p-6"><p class="font-body-md text-body-md text-on-surface-variant">
        Deze klant is nog niet gesynchroniseerd — probeer over een paar minuten opnieuw.</p></div>`;
      return;
    }
    if (site.onboarded_at) { renderOnboardingDone(host, site); return; }
    renderOnboardingStep(host, site, step);
  }

  function renderOnboardingStep(host, site, step) {
    const notice = onbNotice && onbNotice.step === String(step)
      ? `<p class="font-body-md text-body-md ${onbNotice.kind === 'error' ? 'text-error' : 'text-primary'} mt-3">${esc(onbNotice.text)}</p>`
      : '';
    let body = '';
    if (step === 1) body = onbStep1(site);
    else if (step === 2) body = onbStep2(site);
    else if (step === 3) body = onbStep3(site);
    else body = onbStep4(site);
    host.innerHTML = `
      <div class="flex gap-1.5 mb-6">${onbProgress(step)}</div>
      ${body}${notice}`;
    bindOnboardingStep(host, site, step);
  }

  function onbStep1(site) {
    const min = site.steps['1_bedrijfsdoel'].min_length;
    const val = site.steps['1_bedrijfsdoel'].profile || '';
    return `
      <p class="font-label-caps text-label-caps text-primary mb-1">STAP 1 VAN 4</p>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-2">Vertel Iris wat ${esc(site.project)} doet.</h2>
      <p class="font-body-md text-body-md text-on-surface-variant mb-4">Waar het bedrijf voor staat, voor wie, en wat nu de belangrijkste prioriteit is.</p>
      <textarea id="onb-profile" rows="7" class="w-full bg-[#020617] border-none rounded-lg p-4 text-on-surface font-body-lg focus:ring-2 focus:ring-primary/50"
        placeholder="Bijv.: Wij helpen zelfstandige coaches aan hun eerste 10 klanten via LinkedIn...">${esc(val)}</textarea>
      <p class="font-label-caps text-label-caps text-on-surface-variant mt-2" id="onb-count">${val.trim().length}/${min} tekens</p>
      <div class="flex justify-end mt-6">
        <button id="onb-next" class="bg-primary text-on-primary px-6 py-3 rounded-lg font-headline-sm disabled:opacity-40" ${val.trim().length >= min ? '' : 'disabled'}>Volgende</button>
      </div>`;
  }

  function onbStep2(site) {
    return `
      <p class="font-label-caps text-label-caps text-primary mb-1">STAP 2 VAN 4</p>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-2">Hoe klinkt ${esc(site.project)}?</h2>
      <p class="font-body-md text-body-md text-on-surface-variant mb-4">Formeel of informeel, kort of uitgebreid, wat wel/nooit gezegd wordt.</p>
      <textarea id="onb-tone" rows="7" class="w-full bg-[#020617] border-none rounded-lg p-4 text-on-surface font-body-lg focus:ring-2 focus:ring-primary/50"
        placeholder="Bijv.: Informeel maar deskundig, je-vorm, korte zinnen..."></textarea>
      <p class="font-label-caps text-label-caps text-on-surface-variant mt-2" id="onb-count">min. 20 tekens</p>
      <div class="flex justify-between mt-6">
        <button id="onb-back" class="font-headline-sm text-on-surface-variant">Terug</button>
        <button id="onb-next" class="bg-primary text-on-primary px-6 py-3 rounded-lg font-headline-sm disabled:opacity-40" disabled>Volgende</button>
      </div>`;
  }

  function onbStep3(site) {
    const ch = site.steps['3_kanalen'];
    function row(label, info, configured, provider) {
      const connected = !!info;
      const right = connected
        ? '<span class="font-label-caps text-label-caps text-primary">GEKOPPELD</span>'
        : configured
          ? `<a href="/api/oauth?provider=${provider}&op=authorize&site=${encodeURIComponent(site.site_id)}"
               class="font-label-caps text-label-caps text-primary border border-primary/30 rounded-lg px-3 py-1.5">KOPPELEN</a>`
          : '<span class="font-label-caps text-label-caps text-on-surface-variant/50">NIET GECONFIGUREERD</span>';
      return `<div class="flex items-center justify-between gap-3 py-3 border-b border-white/5 last:border-0">
        <div class="min-w-0"><p class="font-body-lg text-body-lg text-on-surface">${label}</p>
        ${connected ? `<p class="font-body-md text-body-md text-on-surface-variant truncate">${esc(info.account_email)}</p>` : ''}</div>
        ${right}</div>`;
    }
    return `
      <p class="font-label-caps text-label-caps text-primary mb-1">STAP 3 VAN 4</p>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-2">Koppel de kanalen van ${esc(site.project)}.</h2>
      <p class="font-body-md text-body-md text-on-surface-variant mb-4">Iris draaft en agendeert alleen in wat je hier koppelt — nooit in jouw eigen account. Overslaan mag.</p>
      <div class="glass-panel rounded-xl p-2">
        ${row('Outlook (mail + agenda)', ch.microsoft, ch.microsoft_configured, 'microsoft')}
        ${row('Google (Search Console + agenda)', ch.google, ch.google_configured, 'google')}
      </div>
      <div class="flex justify-between mt-6">
        <button id="onb-back" class="font-headline-sm text-on-surface-variant">Terug</button>
        <button id="onb-next" class="bg-primary text-on-primary px-6 py-3 rounded-lg font-headline-sm">Volgende</button>
      </div>`;
  }

  function onbStep4(site) {
    const current = site.steps['4_autonomie'].current;
    const presets = site.steps['4_autonomie'].presets;
    let selected = 'normaal';
    if (current) {
      for (const key in presets) {
        if (JSON.stringify(presets[key]) === JSON.stringify({
          content_run_max: current.content_run_max, outreach_max: current.outreach_max,
          seo_refresh_max: current.seo_refresh_max, linkbuild_max: current.linkbuild_max,
        })) selected = key;
      }
    }
    const presetHtml = ['laag', 'normaal', 'hoog'].map((key) => `
      <button type="button" data-preset="${key}"
        class="onb-preset-btn w-full text-left glass-panel rounded-xl p-4 mb-2 ${key === selected ? 'border border-primary' : ''}">
        <span class="font-headline-sm text-headline-sm">${ONBOARDING_PRESETS[key][0]}</span>
        <span class="font-body-md text-body-md text-on-surface-variant"> — ${ONBOARDING_PRESETS[key][1]}</span>
      </button>`).join('');
    return `
      <p class="font-label-caps text-label-caps text-primary mb-1">STAP 4 VAN 4</p>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-2">Hoeveel mag Iris zelf klaarzetten?</h2>
      <p class="font-body-md text-body-md text-on-surface-variant mb-4">Iris publiceert nooit zelf — alles wacht in de Wachtrij op jouw goedkeuring. Dit bepaalt alleen hóéveel ze per dag maximaal vóórbereidt.</p>
      <div id="onb-presets">${presetHtml}</div>
      <div class="flex justify-between mt-6">
        <button id="onb-back" class="font-headline-sm text-on-surface-variant">Terug</button>
        <button id="onb-finish" class="bg-primary text-on-primary px-6 py-3 rounded-lg font-headline-sm">Onboarding afronden</button>
      </div>`;
  }

  function bindOnboardingStep(host, site, step) {
    const back = $('onb-back');
    if (back) back.onclick = () => renderOnboardingStep(host, site, step - 1);

    if (step === 1) {
      const ta = $('onb-profile'), count = $('onb-count'), next = $('onb-next');
      const min = site.steps['1_bedrijfsdoel'].min_length;
      ta.addEventListener('input', () => {
        const n = ta.value.trim().length;
        count.textContent = `${n}/${min} tekens`;
        next.disabled = n < min;
      });
      next.onclick = async () => {
        next.disabled = true;
        await sendCommand('onboarding_step1', { site_id: site.site_id, profile: ta.value.trim() }, 'Bedrijfsdoel');
        site.steps['1_bedrijfsdoel'] = { ...site.steps['1_bedrijfsdoel'], profile: ta.value.trim(), done: true };
        renderOnboardingStep(host, site, 2);
      };
    }
    if (step === 2) {
      const ta = $('onb-tone'), count = $('onb-count'), next = $('onb-next');
      ta.addEventListener('input', () => {
        const n = ta.value.trim().length;
        count.textContent = `${n} tekens (min. 20)`;
        next.disabled = n < 20;
      });
      next.onclick = async () => {
        next.disabled = true;
        await sendCommand('onboarding_step2', { site_id: site.site_id, tone_text: ta.value.trim() }, 'Schrijfstijl');
        site.steps['2_schrijfstijl'] = { done: true };
        renderOnboardingStep(host, site, 3);
      };
    }
    if (step === 3) {
      const next = $('onb-next');
      if (next) next.onclick = () => renderOnboardingStep(host, site, 4);
    }
    if (step === 4) {
      let chosen = (host.querySelector('.onb-preset-btn[data-preset].border-primary') || {}).dataset?.preset || 'normaal';
      host.querySelectorAll('.onb-preset-btn').forEach((btn) => {
        btn.onclick = () => {
          host.querySelectorAll('.onb-preset-btn').forEach((b) => b.classList.remove('border', 'border-primary'));
          btn.classList.add('border', 'border-primary');
          chosen = btn.dataset.preset;
        };
      });
      $('onb-finish').onclick = async (e) => {
        e.target.disabled = true;
        await sendCommand('onboarding_step4', { site_id: site.site_id, preset: chosen }, 'Werk-grenzen');
        await sendCommand('onboarding_complete', { site_id: site.site_id }, 'Onboarding afgerond');
        renderOnboardingTour($('onboarding-wizard'), site);
      };
    }
  }

  // Welkomsttour: vijf schermen met de eigen data van de klant. Draait
  // volledig op wat al lokaal in de browser staat (site.steps is bij elke
  // stap optimistisch bijgewerkt) — geen wachten op een sync nodig, in
  // tegenstelling tot "is dit al écht toegepast?" (dat duurt wél een paar
  // minuten, zie het bevestigingsschermpje in stap 3 hierboven).
  function renderOnboardingTour(host, site) {
    const profile = (site.steps['1_bedrijfsdoel'].profile || '').trim();
    const screens = [
      { n: 1, h: `Iris weet nu wat ${esc(site.project)} doet.`,
        p: `"${esc(profile.slice(0, 220))}${profile.length > 220 ? '…' : ''}"` },
      { n: 2, h: 'De Control Room',
        p: 'Elk project heeft daar een kaart: content, SEO-score, doelen en Iris\' laatste oordeel in één oogopslag. Zodra Iris begint te werken, vult die kaart zich vanzelf.' },
      { n: 3, h: 'Het Actiecentrum',
        p: 'De inbox van alles wat op jóu wacht — een concept klaar om te versturen, een artikel om goed te keuren. Niets verdwijnt hier stil, en het staat ook hier in Impact OS Remote onder Vandaag/Inbox.' },
      { n: 4, h: 'Iris publiceert nooit zelf',
        p: 'Alles wat ze schrijft of voorstelt landt in de Wachtrij en wacht op jouw klik. Pas na jouw goedkeuring gaat er iets live of de deur uit.' },
      { n: 5, h: 'Morgen om 06:45 komt haar eerste briefing',
        p: `Daarin staan haar cijfers, wat ze van plan is, en — zodra er genoeg te melden is — haar advies voor ${esc(site.project)}.` },
    ];
    host.innerHTML = `
      <p class="font-label-caps text-label-caps text-primary mb-1">KLAAR</p>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-6">${esc(site.project)} is onboard.</h2>
      <div class="space-y-4 mb-6">
        ${screens.map((s) => `
          <div class="glass-panel rounded-xl p-4 flex gap-3">
            <div class="w-7 h-7 shrink-0 rounded-full bg-primary/15 text-primary flex items-center justify-center font-headline-sm">${s.n}</div>
            <div class="min-w-0">
              <p class="font-headline-sm text-headline-sm mb-1">${s.h}</p>
              <p class="font-body-md text-body-md text-on-surface-variant">${s.p}</p>
            </div>
          </div>`).join('')}
      </div>
      <button id="onb-to-system" class="w-full bg-primary text-on-primary px-6 py-3 rounded-lg font-headline-sm">Naar Systeem</button>`;
    $('onb-to-system').onclick = () => show('system');
  }

  function renderOnboardingDone(host, site) {
    host.innerHTML = `<div class="glass-panel rounded-xl p-8 text-center">
      <span class="material-symbols-outlined text-primary text-5xl mb-3">verified</span>
      <h2 class="font-display-lg-mobile text-display-lg-mobile mb-2">${esc(site.project)} is onboard.</h2>
      <p class="font-body-md text-body-md text-on-surface-variant">Sinds ${fmtDate(site.onboarded_at)}. Wil je iets aanpassen? Doorloop de wizard opnieuw via de knoppen hieronder.</p>
      <div class="flex flex-col gap-2 mt-6">
        <button data-onb-step="1" class="glass-panel rounded-lg px-4 py-3 font-body-lg text-body-lg">Bedrijfsdoel</button>
        <button data-onb-step="2" class="glass-panel rounded-lg px-4 py-3 font-body-lg text-body-lg">Schrijfstijl</button>
        <button data-onb-step="3" class="glass-panel rounded-lg px-4 py-3 font-body-lg text-body-lg">Kanalen</button>
        <button data-onb-step="4" class="glass-panel rounded-lg px-4 py-3 font-body-lg text-body-lg">Werk-grenzen</button>
      </div>
    </div>`;
    host.querySelectorAll('[data-onb-step]').forEach((b) => {
      b.onclick = () => renderOnboardingStep(host, site, parseInt(b.dataset.onbStep, 10));
    });
  }

  // Ná een Google/Microsoft-consent-redirect stuurt remote/api/oauth.js de
  // browser terug naar #onboarding?site=..&step=3&connecting=<provider> (of
  // &connect_error=...). Alleen de hash overleeft die rondreis (zelfde reden
  // als de oude lokale wizard) — dit is dus bewust de ENE plek waar deze app
  // de hash leest, verder is alles knop-gedreven.
  function handleOnboardingRedirect() {
    const h = location.hash || '';
    if (h.indexOf('#onboarding') !== 0) return;
    const q = new URLSearchParams(h.slice(h.indexOf('?') + 1));
    const site = q.get('site');
    const step = parseInt(q.get('step') || '3', 10) || 3;
    if (!site) return;
    if (q.get('connecting')) {
      onbNotice = { step: String(step), kind: 'connecting',
        text: `Bezig met koppelen — dit duurt tot een paar minuten (wacht op de volgende ImpactOS-sync).` };
    } else if (q.get('connect_error')) {
      onbNotice = { step: String(step), kind: 'error', text: `Koppelen mislukt: ${q.get('connect_error')}` };
    }
    history.replaceState(null, '', location.pathname);
    openOnboarding(site, step);
  }

  $('logoutAllBtn').onclick = async () => {
    try {
      await api('logout-all', 'POST', {});
      toast('Alle apparaten uitgelogd', 'ok', 'logout');
      show('login');
    } catch (e) { if (e.message !== 'login') toast(e.message, 'err'); }
  };

  // ── Vandaag ──────────────────────────────────────────────────────────────
  // Het scherm dat van Impact OS Remote een assistent maakt in plaats van een
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

  // Het #1 signaal voor de Vandaag-kop: het zwaarste 'bad'-item (severity
  // hoog eerst), anders het eerste 'good'-item, anders niets. Bewust géén
  // mail/agenda-filter zoals pulsePanel(excludeAreas) — dit is de kop van het
  // hele scherm, dus een urgente mail mag hier best de opener zijn.
  function topPulseSignal(pulse) {
    if (!pulse) return null;
    const bad = pulse.bad || [];
    const worst = bad.find((b) => b.severity === 'hoog') || bad[0];
    if (worst) return { icon: '⚠', text: worst.what, color: 'var(--err)' };
    const good = (pulse.good || [])[0];
    if (good) return { icon: '✓', text: good.what, color: 'var(--ok)' };
    return null;
  }

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
      ${a.next ? `<div class="rounded-lg p-3" style="background:rgba(156,143,255,0.07); border:1px solid rgba(156,143,255,0.18)">
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

  // `live`: opgehaald rechtstreeks bij Google (api/_google.js), dus actueel
  // óók als ImpactOS niet draait — i.t.t. de rest van het scherm, dat uit de
  // laatste bridge_sync-snapshot komt. Zonder dit onderscheid ziet een verse
  // live-agenda er identiek uit aan een agenda van drie dagen geleden, en dat
  // is precies het soort onzichtbaar verschil dat vertrouwen kost.
  function agendaPanel(a, live) {
    if (!a || a.status !== 'ok') return a ? sectionOff('event_busy', 'Agenda', a) : '';
    return `<div class="glass-panel rounded-xl p-4 space-y-3">
      <button class="card-head card-head-btn" data-open-agenda-sheet="1">
        <div class="card-head-icon"><span class="material-symbols-outlined">calendar_month</span></div>
        <div class="min-w-0 flex-1">
          <p class="card-head-title">Agenda${live ? ' <span class="font-label-caps text-[10px] text-primary align-middle">· LIVE</span>' : ''}</p>
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

  // ── Het Iris-merkteken: een aperture (spaken + pupil) i.p.v. het generieke
  // Material-icoon 'hub' dat hier tot nu toe als logo fungeerde. Zelfde
  // geometrie-taal als de mascotte in de desktop-onboarding
  // (frontend/js/tabs-onboarding.js:_irisMascot) — hier als kale SVG-string
  // omdat Impact OS Remote geen gedeelde JS-module met de hoofd-app heeft.
  // `currentColor` laat 'm meekleuren met de tekstkleur van zijn context
  // (topbar: text-primary, sync-pill: de fresh/recent/stale/dead-kleur).
  function apertureMark(size = 20, cls = '') {
    const spokes = Array.from({ length: 8 }, (_, i) => {
      const a = i * 45;
      return `<line x1="12" y1="3" x2="12" y2="6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" transform="rotate(${a} 12 12)"></line>`;
    }).join('');
    return `<svg class="aperture-mark ${cls}" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">` +
      `<g class="aperture-spokes">${spokes}</g><circle cx="12" cy="12" r="2.6" fill="currentColor"></circle></svg>`;
  }

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
    'rgba(156,143,255,0.16)', 'rgba(125,211,252,0.14)', 'rgba(134,239,172,0.14)',
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
  // Klanten die via WhatsApp appten en waar klant-Iris niet zeker van was (of
  // waar de vraag gevolgen heeft: offerte/afspraak/klacht/persoonsgegevens) —
  // zie backend `_customer_core.js`. Bewust een eigen, simpel paneel i.p.v.
  // het sync_items-mechanisme van de rest van deze pagina: dat wordt bij elke
  // bridge_sync-push volledig overschreven door wat ImpactOS lokaal aanlevert,
  // en een WhatsApp-escalatie ontstaat rechtstreeks in Neon — hij zou binnen
  // een paar minuten weer verdwijnen. Antwoorden gaat dan ook niet via
  // `decide` (dat wacht op de eerstvolgende sync) maar rechtstreeks naar Meta
  // via `whatsapp-reply`, want versturen vergt geen lokale data.
  // Managementstrip boven de escalatielijst: volume + escalatiegraad +
  // reactietijd, uit `whatsapp-stats` (aggregeert whatsapp_rate_limit en
  // whatsapp_escalations — geen nieuwe meting, alleen zichtbaar gemaakt).
  // Verschijnt óók als de escalatielijst leeg is: "0 klanten wachten" en
  // "geen WhatsApp-verkeer" zijn twee verschillende toestanden, en zonder
  // deze strip waren ze niet van elkaar te onderscheiden (zelfde fout als
  // een lege Kansen-lijst die als "niets te doen" leest).
  function whatsappStatsStrip(s) {
    if (!s) return '';
    const rate = s.escalations.escalation_rate_7d;
    const rateTone = rate == null ? '' : rate >= 40 ? 'text-error' : rate >= 20 ? 'text-warning' : 'text-tertiary';
    const avgSec = s.escalations.avg_response_seconds;
    const avgLabel = avgSec == null ? '—'
      : avgSec < 3600 ? `${Math.round(avgSec / 60)}m` : `${(avgSec / 3600).toFixed(1)}u`;
    const nearLimitLine = (s.near_limit || []).length
      ? `<p class="font-body-md text-[11px] text-warning mt-2">⚠ ${s.near_limit.length} klant${s.near_limit.length === 1 ? '' : 'en'} vandaag boven ${Math.round(s.daily_limit * 0.75)} van ${s.daily_limit} berichten</p>`
      : '';
    return `<div class="px-4 pb-3 pt-1 border-b border-white/5">
      <div class="grid grid-cols-5 gap-2 text-center">
        <div><p class="font-headline-sm text-[15px] text-on-surface">${s.messages_7d}</p><p class="font-label-caps text-[9px] text-on-surface-variant/70 uppercase">berichten 7d</p></div>
        <div><p class="font-headline-sm text-[15px] text-on-surface">${s.active_conversations_7d}</p><p class="font-label-caps text-[9px] text-on-surface-variant/70 uppercase">gesprekken 7d</p></div>
        <div><p class="font-headline-sm text-[15px] text-tertiary">${s.new_contacts_7d ?? 0}</p><p class="font-label-caps text-[9px] text-on-surface-variant/70 uppercase">nieuw 7d</p></div>
        <div><p class="font-headline-sm text-[15px] ${rateTone}">${rate == null ? '—' : rate + '%'}</p><p class="font-label-caps text-[9px] text-on-surface-variant/70 uppercase">escalatie 7d</p></div>
        <div><p class="font-headline-sm text-[15px] text-on-surface">${avgLabel}</p><p class="font-label-caps text-[9px] text-on-surface-variant/70 uppercase">reactietijd</p></div>
      </div>
      ${nearLimitLine}
    </div>`;
  }

  // Eén escalatiekaart — vraag + reden + antwoordveld. Was tot 22 aug 2026
  // inline in whatsappPanel; nu een eigen functie omdat zowel de Vandaag-kaart
  // (compact, hooguit 2) als het volle Communicatie-scherm (alles) hem nodig
  // hebben — twee plekken die 'm los uitschrijven lopen gegarandeerd uit elkaar.
  function escalationCard(it) {
    return `<div class="bg-surface-container-lowest/50 border border-white/5 rounded-lg p-3" data-wa-id="${it.id}">
      <p class="font-label-caps text-[10px] text-primary uppercase truncate">${esc(it.project || 'Onbekend project')} · ${esc(fmtDate(it.created_at))}</p>
      <p class="font-body-md text-body-md text-on-surface mt-1">${esc(it.question || '')}</p>
      <p class="font-body-md text-[11px] text-on-surface-variant/70 mt-1">Iris kon dit niet zelf beantwoorden: ${esc(it.reason || '')}</p>
      <textarea rows="3" class="${inputCls} mt-2" data-wa-text placeholder="Typ je antwoord..."></textarea>
      <div class="flex gap-2 mt-2">
        <button class="flex-1 bg-primary text-on-primary font-headline-sm text-[13px] py-2 rounded-lg" data-wa-send="${it.id}">Versturen</button>
        <button class="px-4 border border-white/10 text-on-surface-variant rounded-lg" data-wa-dismiss="${it.id}">Negeren</button>
      </div>
    </div>`;
  }

  // Vandaag-kaart: samenvatting, hooguit 2 escalaties inline (zelfde regel als
  // Postvak — kaart ≠ scherm, zie 14c). Alles (gesprekkenlijst, nieuwe
  // contacten, volledige escalatielijst) leeft in het Communicatie-scherm.
  function whatsappPanel(list, stats) {
    const hasTraffic = stats && (stats.messages_7d > 0 || stats.escalations.open > 0 || stats.new_contacts_7d > 0);
    if ((!list || !list.length) && !hasTraffic) return '';
    const preview = (list || []).slice(0, 2);
    const nieuw = (stats && stats.new_contacts_7d) || 0;
    const metaBits = [];
    if (list.length) metaBits.push(`${list.length} klant${list.length === 1 ? '' : 'en'} wacht${list.length === 1 ? '' : 'en'} op jou`);
    if (nieuw) metaBits.push(`${nieuw} nieuw${nieuw === 1 ? '' : 'e'} contact${nieuw === 1 ? '' : 'en'} deze week`);
    return `<div class="glass-panel rounded-xl overflow-hidden">
      <div class="mail-head">
        <button class="mail-head-main" data-open-communicatie-sheet="1">
          <div class="card-head-icon"><span class="material-symbols-outlined">chat</span></div>
          <div class="min-w-0 flex-1 text-left">
            <p class="card-head-title">Communicatie</p>
            <p class="card-head-meta">${metaBits.length ? esc(metaBits.join(' · ')) : 'Niets wacht — zie de cijfers hieronder'}</p>
          </div>
        </button>
      </div>
      ${whatsappStatsStrip(stats)}
      <div class="px-4 pb-4 space-y-3">
        ${preview.map(escalationCard).join('')}
        <button class="mail-open-all" data-open-communicatie-sheet="1">
          Communicatie openen${list.length > preview.length ? `<span class="mail-open-all-count">${list.length}</span>` : ''}
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
      </div>
    </div>`;
  }

  // ── Communicatie-scherm ──────────────────────────────────────────────────
  // Drie secties, gesorteerd naar wat het van je vraagt: eerst wat wacht
  // (escalaties), dan wie er nieuw is binnengekomen, dan alles — zelfde
  // opbouw als het Postvak-scherm (mailScreenHtml hierboven).
  function conversationRow(c) {
    const who = c.contact_name || c.wa_id;
    return `<li class="mail-row is-tapbaar" data-open-thread="${esc(c.wa_id)}" role="button" tabindex="0">
      <span class="mail-avatar" style="background:${avatarTint(who)}">${esc(initials(c.contact_name, ''))}</span>
      <div class="min-w-0 flex-1">
        <div class="mail-row-top">
          <span class="mail-from">${esc(who)}</span>
          <span class="mail-time">${esc(relTime(c.updated_at))}</span>
        </div>
        <p class="mail-subject">${esc(c.project || 'Onbekend project')}${c.is_new ? ' · <span class="text-primary">nieuw contact</span>' : ''}</p>
        <p class="mail-summary">${c.message_count} bericht${c.message_count === 1 ? '' : 'en'}${c.open_escalations ? ` · ${c.open_escalations} wacht op jou` : ''}</p>
      </div>
      <span class="mail-draft-hint"><span class="material-symbols-outlined">chevron_right</span></span>
    </li>
    <li class="mail-thread hidden"></li>`;
  }

  function threadMessagesHtml(thread) {
    const msgs = thread.messages || [];
    if (!msgs.length) return '<p class="mail-leeg">Nog geen berichten opgeslagen.</p>';
    return `<div class="space-y-2 py-2">${msgs.map((m) => `
      <div class="rounded-lg p-2.5 ${m.role === 'user' ? 'bg-white/5' : 'bg-primary/10'}">
        <p class="font-label-caps text-[9px] text-on-surface-variant/60 uppercase">${m.role === 'user' ? esc(thread.contact_name || thread.wa_id) : 'Iris'}</p>
        <p class="font-body-md text-[13px] text-on-surface mt-0.5 whitespace-pre-wrap">${esc(typeof m.content === 'string' ? m.content : JSON.stringify(m.content))}</p>
      </div>`).join('')}</div>`;
  }

  // Eén bindfunctie, gedeeld tussen kaart en scherm — zelfde reden als
  // bindMailRows: twee plekken die dit los binden lopen uit elkaar.
  // `conversationRow()` kan hetzelfde gesprek in twee secties tonen (Nieuwe
  // contacten + Alle gesprekken) — de slot is daarom de eerstvolgende <li>
  // van dezelfde rij, nooit een globale lookup op wa_id, anders opent een tik
  // op de tweede rij het transcript van de eerste.
  function bindConversationRows(root) {
    root.querySelectorAll('[data-open-thread]').forEach((row) => {
      row.onclick = async () => {
        const waId = row.dataset.openThread;
        const slot = row.nextElementSibling;
        if (!slot) return;
        if (!slot.classList.contains('hidden')) { slot.classList.add('hidden'); return; }
        slot.classList.remove('hidden');
        slot.innerHTML = '<p class="mail-leeg">Laden…</p>';
        try {
          // api() bouwt de URL als `?op=${op}` — een tweede queryparam meegeven
          // via een `&` in de op-string is hier de eenvoudigste weg zonder de
          // gedeelde api()-helper (en al haar aanroepers) een signatuur erbij
          // te geven voor dit ene geval.
          const data = await api(`whatsapp-thread&wa_id=${encodeURIComponent(waId)}`);
          slot.innerHTML = threadMessagesHtml(data.thread);
        } catch (e) {
          slot.innerHTML = `<p class="font-body-md text-[12px] text-error">${esc(e.message)}</p>`;
        }
      };
    });
  }

  function communicatieScreenHtml(list, stats, conversations) {
    const nieuwe = (conversations || []).filter((c) => c.is_new);
    return `
      ${whatsappStatsStrip(stats)}
      ${list.length ? `<section class="mail-sectie">
        <p class="mail-sectie-kop"><span class="flex-1">Wacht op jou</span><span class="mail-sectie-num">${list.length}</span></p>
        <div class="space-y-3">${list.map(escalationCard).join('')}</div>
      </section>` : ''}
      ${nieuwe.length ? `<section class="mail-sectie">
        <p class="mail-sectie-kop"><span class="flex-1">Nieuwe contacten (7d)</span><span class="mail-sectie-num">${nieuwe.length}</span></p>
        <ul class="mail-list">${nieuwe.map(conversationRow).join('')}</ul>
      </section>` : ''}
      <section class="mail-sectie">
        <p class="mail-sectie-kop"><span class="flex-1">Alle gesprekken (30d)</span><span class="mail-sectie-num">${(conversations || []).length}</span></p>
        ${(conversations || []).length
          ? `<ul class="mail-list">${conversations.map(conversationRow).join('')}</ul>`
          : '<p class="mail-leeg">Nog geen klantgesprekken via WhatsApp.</p>'}
      </section>`;
  }

  async function openCommunicatieSheet() {
    const card = openSheet('Communicatie', 'WhatsApp-verkeer', '<p class="mail-leeg">Laden…</p>');
    try {
      const [waData, waStats, waConvos] = await Promise.all([
        api('whatsapp'), api('whatsapp-stats'), api('whatsapp-conversations'),
      ]);
      const list = (waData && waData.escalations) || [];
      card.querySelector('.mt-3').innerHTML = communicatieScreenHtml(list, waStats, (waConvos && waConvos.conversations) || []);
      bindWhatsappCards(card, () => { loadToday(); openCommunicatieSheet(); });
      bindConversationRows(card);
    } catch (e) {
      card.querySelector('.mt-3').innerHTML = `<p class="font-body-md text-[13px] text-error">Kon Communicatie niet laden: ${esc(e.message)}</p>`;
    }
  }

  // `onDone` draait ná een geslaagde actie — default ververst alleen Vandaag
  // (de kaart), maar het Communicatie-scherm geeft hier zijn eigen herlaad mee
  // zodat de sheet zelf ook bijwerkt in plaats van open te blijven staan met
  // een net-verstuurde kaart nog zichtbaar.
  function bindWhatsappCards(root, onDone = loadToday) {
    root.querySelectorAll('[data-wa-send]').forEach((btn) => {
      btn.onclick = async () => {
        const card = btn.closest('[data-wa-id]');
        const ta = card.querySelector('[data-wa-text]');
        const text = (ta.value || '').trim();
        if (!text) { toast('Typ eerst een antwoord', '', 'edit'); return; }
        btn.disabled = true; btn.textContent = '…';
        try {
          await api('whatsapp-reply', 'POST', { id: btn.dataset.waSend, text });
          toast('Verstuurd', 'ok', 'check_circle');
          onDone();
        } catch (e) {
          toast(e.message, 'err', 'error');
          btn.disabled = false; btn.textContent = 'Versturen';
        }
      };
    });
    root.querySelectorAll('[data-wa-dismiss]').forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await api('whatsapp-dismiss', 'POST', { id: btn.dataset.waDismiss });
          onDone();
        } catch (e) {
          toast(e.message, 'err', 'error');
          btn.disabled = false;
        }
      };
    });
  }

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
    // De top-3 als klikbare bullets: elke tik opent de detailweergave (net
    // als in de Besluiten-lijst). We bewaren alleen de item-keys, niet kant-
    // en-klare HTML: die HTML werd één keer gebakken en bleef daarna voor
    // altijd staan, ook nadat Vincent een besluit nam — de "verdwijnt nooit"-
    // klacht van 12 aug 2026. renderChat() bouwt de bullets nu bij elke
    // render opnieuw uit greetKeys, dus een beslist item valt vanzelf weg
    // zodra refresh() de nieuwe decision_status heeft opgehaald.
    chatHistory.push({ role: 'assistant', content: line, greeting: true, greetKeys: tops.map((it) => it.key) });
    renderChat();
  }
  // Persoonlijke teruggroet op de "Hi 👋"-knop: kort, warm, met de stand van
  // de dag — alsof Iris opkijkt als je binnenkomt.
  function irisHi() {
    if (chatHistory.some((m) => m.hi)) return;
    chatHistory.push({ role: 'user', content: 'Hi 👋' });
    const open = items.filter((it) => !it.decision_status || it.decision_status === 'failed').length;
    const who = window.__tenantName || 'Vincent';
    const line = open
      ? `Hoi ${who} 👋 Fijn dat je er bent. ${open} besluit(en) wachten, ik hou ze voor je vast — zeg het als ik er eentje voor je moet uitvoeren.`
      : `Hoi ${who} 👋 Fijn dat je er bent. Niets wacht op je — rustig ritje vandaag.`;
    chatHistory.push({ role: 'assistant', content: line, hi: true });
    renderChat();
  }

  async function loadToday(token = loadToken) {
    const el = $('today-body');
    if (token === loadToken && !contextCache) el.innerHTML = skeletons(3);
    // Topbar toont de live begroeting ("Goedemiddag"); de grote body-titel
    // wordt de tenant-naam (Nicole, niet hard-coded "Vincent") zodat de
    // begroeting niet dubbel staat. De naam komt uit de context-response.
    const g = greeting();
    const tenantName = (contextCache && contextCache.tenant && contextCache.tenant.name) || window.__tenantName || 'Vincent';
    $('today-greeting').textContent = tenantName;
    const tbGreet = $('topbar-greeting');
    if (tbGreet) tbGreet.textContent = g;
    try {
      const [data, outboxData, waData, waStats] = await Promise.all([
        api('context'),
        api('outbox').catch(() => null), // niet-fataal: de queue-note is een bonus, geen kernfunctie
        api('whatsapp').catch(() => null), // idem: geen WhatsApp gekoppeld is geen fout
        api('whatsapp-stats').catch(() => null), // idem: cijfers zijn een bonus, geen kernfunctie
      ]);
      if (token !== loadToken) return;
      const ctx = data.payload;
      if (!ctx) {
        el.innerHTML = `<div class="glass-panel rounded-xl p-10 text-center">
          <span class="material-symbols-outlined text-primary text-4xl mb-2">cloud_off</span>
          <p class="font-body-lg text-body-lg text-on-surface-variant">Nog geen context gesynchroniseerd.<br>Draait ImpactOS?</p></div>`;
        return;
      }
      contextCache = ctx;
      // Zet de tenant-naam globaal zodat latere begroetingen ("Hoi <naam>")
      // de juiste klant tonen i.p.v. het hard-coded "Vincent".
      const tName = (data.tenant && data.tenant.name) || (ctx && ctx.tenant && ctx.tenant.name);
      if (tName) window.__tenantName = tName;
      const stamp = data.generated_at ? `Stand van ${fmtDate(data.generated_at)}` : '';
      $('today-stamp').textContent = stamp;
      // Iris begroet zodra er context is (ze "ziet" je dag).
      const open = items.filter((i) => !i.decision_status || i.decision_status === 'failed').length;
      $('today-sub').textContent = open ? `${open} besluit(en) wachten op je` : 'Niets wacht op je';
      // Iris begroet pas ná de count-update, zodat ze de juiste "N wachten op
      // je" in haar welkomst zet.
      irisGreet();
      // Het zwaarste signaal uit de pulse als kop, niet alleen een teller:
      // "3 besluiten wachten" zegt niets over wélk het belangrijkst is. Zelfde
      // bron als pulsePanel() verderop — dit is een teaser van het #1 item,
      // geen tweede lijst (die staat al compleet in pulsePanel).
      const pulseLine = $('today-pulse-line');
      const top = topPulseSignal(ctx.pulse);
      if (top) {
        pulseLine.textContent = `${top.icon} ${top.text}`;
        pulseLine.style.color = top.color;
        pulseLine.classList.remove('hidden');
      } else {
        pulseLine.classList.add('hidden');
      }
      // Besluiten die al ergens (chat, knop) zijn genomen maar nog niet zijn
      // opgepikt: alleen tonen als de sync ook echt oud is — anders knippert
      // dit elke keer even op tussen twee polls door.
      const queueNote = $('today-queue-note');
      const pendingOutbox = (outboxData?.decisions || []).filter((d) => d.status === 'pending').length;
      const syncAgeMin = lastPushAt ? Math.round((Date.now() - new Date(lastPushAt)) / 60000) : null;
      if (queueNote) {
        if (pendingOutbox > 0 && (syncAgeMin === null || syncAgeMin >= 60)) {
          const wanneer = lastPushAt
            ? (syncAgeMin < 180 ? `${syncAgeMin}m geleden` : syncAgeMin < 1440 ? `${Math.round(syncAgeMin / 60)}u geleden` : `${Math.round(syncAgeMin / 1440)}d geleden`)
            : 'nog nooit';
          queueNote.textContent = `⏳ ${pendingOutbox} actie${pendingOutbox === 1 ? '' : 's'} wacht${pendingOutbox === 1 ? '' : 'en'} op ImpactOS — laatst gesynct ${wanneer}.`;
          queueNote.classList.remove('hidden');
        } else {
          queueNote.classList.add('hidden');
        }
      }
      // Dit ís de dag — agenda en postvak eerst, precies zo opent een
      // secretaresse het gesprek ook. Pulse (bredere signalen) erna, met
      // mail/agenda eruit gefilterd want die staan al concreet hierboven.
      // Delegeren (snel-starten) komt pas na het overzicht, niet ervoor.
      // .stagger laat de panelen ná elkaar binnenkomen i.p.v. in één keer te
      // verschijnen — dezelfde entree-klasse die de rest van de app al
      // gebruikt (Postvak-rijen, notities), nu ook hier.
      el.classList.add('stagger');
      el.innerHTML = [
        whatsappPanel((waData && waData.escalations) || [], waStats),
        agendaPanel(ctx.agenda, data.live?.agenda),
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
      el.querySelectorAll('[data-open-communicatie-sheet]').forEach((b) => { b.onclick = () => openCommunicatieSheet(); });
      const agendaSheetBtn = el.querySelector('[data-open-agenda-sheet]');
      if (agendaSheetBtn) agendaSheetBtn.onclick = () => openAgendaSheet();
      bindMailRows(el, false);
      bindWhatsappCards(el);
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

  // Vastgelopen content (Iris Orchestrator): stukken die de goedkope 30-min
  // verbeteraar niet redde ('stuck') of die zijn afgewezen ('rejected').
  // Eén knop, geen site/count-parameter — de backend kiest zelf het eerste
  // stuk. Bewust een tik-knop, geen chat-commando (zie remote/api/iris.js).
  function orchestratorPanel(orch) {
    if (!orch || orch.status !== 'ok' || !orch.count) return '';
    const row = (j) => `<li class="flex justify-between gap-3 font-body-md text-[12px]">
      <span class="text-on-surface-variant truncate">${esc(j.title || j.id)} · ${esc(j.project || '')}</span>
      <span class="text-on-surface-variant/60 shrink-0">${esc(j.status || '')}${j.seo_score == null ? '' : ` · ${j.seo_score}`}</span>
    </li>`;
    return `<div class="glass-panel rounded-xl p-4">
      <p class="font-headline-sm text-[15px] mb-2">Vastgelopen content (${orch.count})</p>
      <ul class="space-y-1 mb-3">${(orch.jobs || []).slice(0, 5).map(row).join('')}</ul>
      <button data-cmd="orchestrator_run"
        class="w-full bg-transparent border border-primary/30 text-primary font-headline-sm text-[13px] py-2 rounded-lg active:scale-[0.98] transition-all">
        Zet er één op de Gauntlet Loop</button>
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

  // Bouwt de bullets van Iris' openingsgroet ter plekke uit de huidige
  // items-stand — nooit uit bevroren HTML — zodat een net beslist item meteen
  // wegvalt in plaats van voor de rest van de sessie te blijven staan.
  function greetBulletsHtml(m) {
    if (!m.greetKeys || !m.greetKeys.length) return '';
    const open = m.greetKeys
      .map((key) => items.find((it) => it.key === key))
      .filter((it) => it && (!it.decision_status || it.decision_status === 'failed'));
    if (!open.length) return '';
    let bullets = '<ul class="iris-greet-list">';
    open.forEach((it) => {
      const tag = it.project ? `${esc(it.project)} · ` : '';
      bullets += `<li><button type="button" class="iris-greet-item" data-greet-key="${esc(it.key)}">`
        + `<span class="material-symbols-outlined">chevron_right</span>`
        + `<span class="iris-greet-txt">${tag}${esc(it.title)}</span></button></li>`;
    });
    bullets += '</ul>';
    return bullets;
  }

  function renderChat(pending = false) {
    const el = $('chat-messages');
    chatHistory.forEach((m, i) => { m.idx = i; });
    const irisAvatar = `<div class="chat-avatar">${apertureMark(16)}</div>`;
    el.innerHTML = chatHistory.map((m) => m.role === 'user'
      ? `<div class="flex justify-end fade-up"><div class="chat-msg me">${esc(m.content)}</div></div>`
      : `<div class="flex justify-start items-end gap-1.5 fade-up">${irisAvatar}<div class="chat-msg iris${m.greeting ? ' is-greeting' : ''}">${mdLite(m.content)}${m.greeting ? greetBulletsHtml(m) : ''}${effectsHtml(m)}</div></div>`).join('')
      // Levendige typing-indicator i.p.v. een statische "IRIS DENKT NA…" —
      // drie stippen die stuiteren, zodat je ziet dat Iris écht aan het
      // schrijven is (niet een vast label dat altijd aanstaat).
      + (pending ? `<div class="flex justify-start items-end gap-1.5 fade-up">${irisAvatar}<div class="chat-msg iris"><div class="chat-typing"><span></span><span></span><span></span></div></div></div>` : '');
    el.querySelectorAll('[data-prop]').forEach((btn) => {
      btn.onclick = async () => {
        const [mi, pi] = btn.dataset.prop.split(':').map(Number);
        const p = (chatHistory[mi].proposals || [])[pi];
        if (!p) return;
        btn.disabled = true;
        try {
          const r = await api('decide', 'POST', { item_key: p.item_key, action: p.action, payload: {} });
          btn.textContent = r.queued ? '✓ Vastgelegd — ImpactOS voert het uit' : 'Stond al in de wachtrij';
          toast('Besluit vastgelegd', 'ok', 'check_circle');
          refresh();
        } catch (e) {
          if (e.message !== 'login') { toast(e.message, 'err'); btn.disabled = false; }
        }
      };
    });
    // Klikbare bullets in Iris' openingsgroet → open de detailweergave.
    el.querySelectorAll('[data-greet-key]').forEach((btn) => {
      btn.onclick = () => {
        const it = items.find((i) => i.key === btn.dataset.greetKey);
        if (it) openDetail(it);
      };
    });
    el.scrollTop = el.scrollHeight;
  }
  // ── Agenda-opdracht (spraak/tekst -> calendar_add) ────────────────────────
  // Vrije zin of ingesproken commando -> parser in de backend -> agenda-voorstel
  // (review-gate: boeken gebeurt pas als Vincent het in Impact OS Remote goedkeurt).
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
  // geen ondersteuning blijft het veld gewoon typbaar. Gedeeld door twee
  // velden: het smalle agenda-veld (alleen calendar_add) en de hoofdchat
  // (de volle Iris-tool-lus — start_werk/plan_agenda/stel_besluit_voor/
  // ritueel_vastleggen) zodat een vrije, ingesproken zin als "Beerenschot
  // leuk gesprek gehad, zet maandag een mail in mijn agenda" bij Iris'
  // agents terechtkomt in plaats van alleen bij de agenda-parser.
  function setupMic(micId, iconId, inputId) {
    const mic = $(micId);
    const icon = $(iconId);
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { mic.title = 'Spraak niet ondersteund in deze browser — typ je opdracht'; return; }
    const rec = new SR();
    rec.lang = 'nl-NL';
    rec.interimResults = false;
    rec.continuous = false;
    let listening = false;
    rec.onresult = (ev) => {
      const txt = ev.results[0][0].transcript;
      $(inputId).value = txt;
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
  }
  setupMic('agenda-mic', 'agenda-mic-icon', 'agenda-input');
  setupMic('chat-mic', 'chat-mic-icon', 'chat-input');

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
  handleOnboardingRedirect();
  startPolling();
})();
