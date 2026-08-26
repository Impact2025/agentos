// ── Impact OS — Control Room: home, Actiecentrum, digest, strategist, systeemgezondheid
// Onderdeel van de SPA: klassieke scripts, gedeelde globale scope.
// Laadvolgorde staat in index.html — core.js eerst.

// Subnav-sprong: expliciet i.p.v. op de kale browser-anchor-navigatie
// vertrouwen — die bleek niet betrouwbaar te scrollen zodra de sectie pas
// ná de klik in de DOM stond (of achter een sticky kop viel).
function crJump(e, id) {
  if (e) e.preventDefault();
  var el = document.getElementById(id);
  // 'smooth' bleek in sommige Chrome-omgevingen (automation/CDP-gestuurd)
  // stil te falen — geen scroll, geen fout. 'auto' (instant) is universeel
  // betrouwbaar en voor een menu-sprong is animatie geen vereiste.
  if (el) el.scrollIntoView({ behavior: 'auto', block: 'start' });
  if (history.pushState) history.pushState(null, '', '#' + id);
  return false;
}

function renderHome(main) {
  main.innerHTML = '<div class="loading"><div class="spinner"></div><p>Control Room laden...</p></div>';
  // Drie bestaande, pure-SQLite (dus snelle) endpoints tegelijk: de
  // control-room-status, Iris' per-project rapportcijfers (voor de
  // Analytics-grafieken) en het weekbeeld (voor de "quick wins"-hero-tegel).
  // Geen van drieën doet een externe HTTP-call, dus dit voegt geen trage
  // netwerk-aanroep toe aan de toch al zware Control Room-load.
  // Plus de per-project telling (ook puur SQLite) voor de badge op elke
  // projectkaart. NB: de volle inbox (/api/action-center) wordt bewust NIET
  // hier meegehaald — loadActionCenter() (verderop) haalt hem al op om het
  // paneel te renderen; hem hier óók ophalen haalde tot 20 aug 2026 dezelfde
  // (niet-triviale) query twee keer op zonder dat het eerste resultaat ooit
  // werd gebruikt.
  Promise.all([
    fetch('/api/strategist/control-room').then(function(r){return r.json();}),
    fetch('/api/iris/scores').then(function(r){return r.json();}).catch(function(){return null;}),
    fetch('/api/analytics/weekly-insights').then(function(r){return r.json();}).catch(function(){return null;}),
    fetch('/api/action-center/by-project').then(function(r){return r.json();}).catch(function(){return {};}),
  ]).then(function(results){
    var data = results[0], irisScores = results[1], weekly = results[2];
    var projCounts = results[3] || {};
    if (data.error) { main.innerHTML = '<div class="empty-state">Fout: ' + escHtml(data.error) + '</div>'; return; }
    // De kop is een blok, geen rij: titel, ondertitel, dan pas de status-chips.
    // Stonden ze op één flexregel (zoals tot 10 aug 2026), dan werd de zin
    // "Control Room — overzicht van alle projecten" op een telefoon een kolom
    // van vier woorden naast een badge, en las de kop als een storing.
    var html = '<div class="homescreen"><header class="page-head">' +
      '<h2>' + escHtml(window.__instanceName || 'Impact OS') + '</h2>' +
      '<p class="subtitle">Control Room &mdash; overzicht van alle projecten en systemen</p>' +
      '<div class="head-chips"><span id="agent-status-indicator">' +
      '<span class="pill pill-neutral"><span style="width:6px;height:6px;border-radius:50%;background:currentColor"></span> Laden...</span></span>' +
      '<span style="font-size:11px;color:#94a3b8" id="agent-log-count"></span>' +
      '<span id="resolve-failed-btn-container"></span>' +
      // Snelle project-focus: spring direct naar de per-project view (waar
      // "Wacht op jou" bovenaan staat) zonder door het raster te klikken.
      '<span style="margin-left:auto"><select onchange="if(this.value)selectProject(this.value)" style="font-size:12px;padding:4px 8px;border:1px solid var(--card-border);border-radius:6px;background:var(--card-bg);color:var(--text)">' +
      '<option value="">Project-focus…</option>' +
      (data.projects||[]).map(function(p){
        var c = projCounts[p.name] || 0;
        return '<option value="' + escAttr(p.name) + '">' + escHtml(p.name) + (c>0 ? ' (' + c + ' wacht)' : '') + '</option>';
      }).join('') +
      '</select></span>' +
      '</div></header>';

    // ── Pagina-eigen subnav — springt naar een sectie verderop op dezelfde
    // pagina (geen tonen/verbergen: de onboarding-rondleiding in tour.js
    // verwacht #action-center-panel/#iris-panel zonder klik zichtbaar). ──
    html += '<nav class="cr-subnav">' +
      '<a href="#cr-overzicht" onclick="return crJump(event,\'cr-overzicht\')">Overzicht</a>' +
      '<a href="#cr-actiecentrum" onclick="return crJump(event,\'cr-actiecentrum\')">Actiecentrum</a>' +
      '<a href="#cr-analytics" onclick="return crJump(event,\'cr-analytics\')">Analytics</a>' +
      '<a href="#cr-systeem" onclick="return crJump(event,\'cr-systeem\')">Systeem</a>' +
      '</nav>';

    // ── Overzicht: Iris Pulse (wat deed Iris, werkt het), hero-KPI's,
    // systeemgezondheid, projectkaarten ──
    html += '<div id="cr-overzicht" class="cr-section">';
    html += '<div id="iris-pulse-panel" style="margin-bottom:16px"></div>';
    html += heroKpiRow(data, irisScores, weekly);

    // ── Actiecentrum: alles wat op een menselijke beslissing wacht ──
    html += '</div><div id="cr-actiecentrum" class="cr-section">';
    html += '<div id="action-center-panel"><div style="color:#64748b;font-size:12px;padding:8px 0">Inbox laden...</div></div>';
    html += '</div>';

    // ── Overzicht vervolgt: systeemgezondheid + projectkaarten ──
    html += '<div class="cr-section">';
    // ── Systeemgezondheid — blijft zichtbaar: dit ís een wacht-op-jou-signaal
    // zodra hij niet 'gezond' meldt, en verdient geen extra klik. ──
    html += '<div id="system-health-panel" style="margin-bottom:16px"></div>';

    // ── Project cards — de belangrijkste navigatie op dit scherm, dus een
    // responsieve grid (.project-grid) i.p.v. de altijd-2-koloms .grid-2:
    // op een breed bureaublad staan er zo 3-5 naast elkaar. ──
    html += '<div class="project-grid" style="margin-bottom:20px">';
    (data.projects||[]).forEach(function(p){
      var goals = p.goals || [];
      var rGoals = goals.filter(function(g){return g.status==='running'||g.status==='ready';});
      var runningBadge = rGoals.length > 0 ? '<span class="pill pill-info" style="margin-left:6px">' + rGoals.length + ' bezig</span>' : '';
      var gscBadge = p.gsc_configured ? '<span class="pill pill-ok">GSC</span>' : '<span class="pill pill-neutral">GSC uit</span>';
      var oppNew = (p.opportunities||{}).new || 0;
      var oppBadge = oppNew > 0 ? '<span class="pill pill-warn" style="margin-left:6px">' + oppNew + ' nieuw</span>' : '';
      // Badge met het aantal open acties voor dit project (content ter review,
      // fouten, mail, …) — dezelfde telling als de projectview. Rode rand bij
      // >0 zodat een project dat aandacht vraagt meteen opvalt in het raster.
      var pend = projCounts[p.name] || 0;
      var pendBadge = pend > 0 ? '<span class="pill pill-danger" style="margin-left:6px" title="open acties">' + pend + ' wacht</span>' : '';

      html += '<div class="project-card" onclick="selectProject(\'' + p.name.replace(/'/g,"\\'") + '\')" style="cursor:pointer;padding:16px;border:1px solid var(--card-border);border-radius:10px;background:var(--card-bg)">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
        '<p class="pc-name" style="font-size:14px;font-weight:600;color:var(--text)">' + escHtml(p.name) + runningBadge + oppBadge + pendBadge + '</p>' +
        gscBadge +
        '</div>' +
        '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(p.description) + '</p>' +
        '<div style="display:flex;gap:16px;font-size:11px;color:#94a3b8">' +
        '<span>' + (p.content_count||0) + ' bestanden</span>' +
        '<span>' + goals.length + ' doelen</span>' +
        '<span>' + ((p.opportunities||{}).total ?? 0) + ' kansen totaal</span>' +
        '</div>' +
        (goals.length > 0 ? '<div style="margin-top:8px;font-size:10px;color:#64748b;border-top:1px solid #f1f5f9;padding-top:6px">' +
          goals.slice(0,3).map(function(g){
            var live = g.status==='running'||g.status==='ready';
            var dot = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;' +
              (live ? 'background:var(--accent)' : 'background:transparent;border:1px solid #cbd5e1') + '"></span>';
            return '<div>' + dot + escHtml(g.title) + ' <span style="color:#94a3b8">(' + g.status + ')</span></div>';
          }).join('') +
          (goals.length>3?'<div style="color:#94a3b8">+ ' + (goals.length-3) + ' meer</div>':'') +
        '</div>' : '') +
        '</div>';
    });
    html += '</div>';

    // ── Strategist analyse knop ── (maakt/prioriteert doelen — hoort bij
    // domain "goal"; zonder strategist_router gemonteerd is dit een knop naar
    // een 404, dus alleen tonen als het domein er is)
    var goalOnEarly = domainOn('goal');
    if (goalOnEarly) {
      html += '<div class="section-card" style="text-align:center;background:linear-gradient(135deg,#eef2ff,#f8fafc);border:1px solid #e0e7ff">' +
        '<h4 style="font-size:14px;font-weight:700;color:var(--accent);margin-bottom:4px">Strategist Agent</h4>' +
        '<p style="font-size:12px;color:#64748b;margin-bottom:12px">AI-manager die alle projecten, doelen en kansen analyseert en prioriteiten stelt</p>' +
        '<button onclick="runStrategistAnalysis()" id="strat-btn" class="btn btn-primary" style="padding:10px 24px;font-size:13px">Analyseer &amp; prioriteer</button>' +
        '</div>';
      html += '<div id="strategist-result"></div>';
    }
    html += '</div>';

    // ── Analytics: grafieken + Iris' dagbriefing + linkbuilding ──
    html += '<div id="cr-analytics" class="cr-section">';
    html += analyticsChartsBlock(irisScores);
    html += '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">' +
      '<h4 style="font-size:13px;font-weight:700;color:var(--text)">Iris — dagbriefing van je AI-manager</h4>' +
      '<div style="display:flex;gap:6px">' +
      '<button onclick="loadIrisBriefing()" class="btn btn-ghost btn-sm">Ververs</button>' +
      '<button id="iris-run-btn" onclick="runIrisNow()" class="btn btn-primary btn-sm">Analyseer nu</button>' +
      '</div></div>' +
      '<div id="iris-panel" style="font-size:12px"><div style="color:#64748b">Laden...</div></div>' +
      // Het trage beeld naast het snelle: 28 dagen tegen de 28 daarvóór, per
      // project. Dit is dezelfde bron die Iris in haar briefing leest — zag je
      // hier iets anders dan zij, dan is één van beide een verzinsel.
      '<details style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:8px" ontoggle="if(this.open)loadWeekbeeld()">' +
      '<summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--text-dim)">Weekbeeld — 28 dagen vs. de 28 daarvóór (per project)</summary>' +
      '<div id="weekbeeld-panel" style="margin-top:8px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>' +
      '<details style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:8px" ontoggle="if(this.open)loadIrisKnowledge()">' +
      '<summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--text-dim)">Kennisbank — voed Iris met onderzoek (GEO, SEO, ...)</summary>' +
      '<div id="iris-knowledge-panel" style="margin-top:8px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details></div>';

    // ── Linkbuilding — funnel, live links en open kansen per site ──
    // Hoort bij domain "linkbuilding": op een instance die dat niet heeft
    // (bv. Nicole) is de route niet eens gemonteerd — de sectie tonen zou
    // alleen een knop zijn die op een 404 uitkomt.
    if (domainOn('linkbuilding')) {
      html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open)loadLinkbuilding()">' +
        '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:var(--text)">Linkbuilding — kansen · outreach · links live</summary>' +
        '<div style="display:flex;gap:6px;margin-top:8px">' +
        '<button onclick="runLinkbuildingProspecting(this)" class="btn btn-ghost btn-sm">Zoek kansen</button>' +
        '<button onclick="runLinkbuildingBatch(this)" class="btn btn-ghost btn-sm">Maak concepten (review)</button>' +
        '<button onclick="loadLinkbuilding()" class="btn btn-ghost btn-sm">Ververs</button>' +
        '</div>' +
        '<div id="linkbuilding-panel" style="margin-top:10px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>';
    }
    html += '</div>';

    // ── Systeem: rituelen, postvak-preview, ochtendrapport, activiteit ──
    html += '<div id="cr-systeem" class="cr-section">';

    // ── Rituelen — ochtend/avond, week, wins, doelen (persoonlijk, niet ──
    // projectgebonden — vandaar hier op de Control Room i.p.v. een tab).
    if (domainOn('rituals')) {
      html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open)loadRituelenSection()">' +
        '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:var(--text)">Rituelen — ochtend · avond · week · wins · doelen</summary>' +
        '<div id="rituelen-panel" style="margin-top:10px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>';
    }

    // ── Postvak — gesorteerd naar wat een mail van jou nodig heeft ──
    // Ingeklapt: dit dupliceert deels de mailkaarten die al bovenaan in het
    // Actiecentrum staan (waar echt op geklikt kan worden); hier is het een
    // volledigere lijst ter oriëntatie, geen tweede besliswerk-inbox.
    html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open)loadSortedInbox()">' +
      '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:var(--text)">Postvak — wat wacht op jouw antwoord</summary>' +
      '<div id="sorted-inbox-panel" style="margin-top:10px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>';

    // ── Ochtendrapport (inklapbaar; zelfde inhoud als de 07:00-digest) ──
    html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open)loadDigest()">' +
      '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:var(--text)">Ochtendrapport — fouten · wacht-op-jou · gisteren opgeleverd · vandaag gepland</summary>' +
      '<div id="digest-panel" style="margin-top:10px;font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div></details>';

    // ── Recente activiteit + OpenModel-credits — samen onder "Systeem" ──
    // Twee logs die niemand op elke bezoek hoeft te zien; gebundeld i.p.v.
    // los, zodat de standaardweergave van de Control Room stopt bij wat
    // daadwerkelijk een besluit van Vincent vraagt (Actiecentrum + Iris).
    html += '<details class="section-card" style="margin-bottom:16px;padding:10px 16px" ontoggle="if(this.open){loadActivityLogs();loadLlmUsage();_ensureLlmUsageRefresh();}">' +
      '<summary style="cursor:pointer;font-size:13px;font-weight:700;color:var(--text)">Systeem — activiteit · credit-verbruik · cijfers</summary>' +
      '<div style="margin-top:10px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
      '<h5 style="font-size:11px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:.4px">Recente activiteit</h5>' +
      '<button onclick="loadActivityLogs()" class="btn btn-ghost btn-sm">Ververs</button></div>' +
      '<div id="activity-log-panel" style="background:#0f172a;border-radius:8px;padding:8px;font-family:monospace;font-size:11px;max-height:300px;overflow-y:auto">' +
      '<div style="color:#64748b;text-align:center;padding:16px">Klik om te laden...</div></div>' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin:14px 0 6px">' +
      '<h5 style="font-size:11px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:.4px">OpenModel-credits</h5>' +
      '<button onclick="loadLlmUsage()" class="btn btn-ghost btn-sm">Ververs</button></div>' +
      '<div id="llm-usage-panel" style="font-size:12px"><div style="color:#64748b">Klik om te laden...</div></div>';

    // "Doelen" hoort bij domain "goal" — zonder de Doelen-engine (geen
    // goal_router gemonteerd) is dat getal altijd 0/0 en dus ruis, geen info.
    var sys = data.system || {};
    var obs = sys.obsidian || {};
    var goalOn = domainOn('goal');
    html += '<div class="kpi-grid" style="grid-template-columns:repeat(' + (goalOn ? 4 : 3) + ',1fr);margin:14px 0 0">' +
      kpiBox('Hermes', sys.hermes_configured ? sys.hermes_backend : 'Uit', '', sys.hermes_model || '') +
      kpiBox('Obsidian', obs.configured ? 'Actief' : 'Uit', '', obs.total_notes + ' notities') +
      kpiBox('OMI', obs.omi_configured ? 'Actief' : 'Uit') +
      (goalOn ? kpiBox('Doelen', data.goals_summary ? data.goals_summary.total : 0, '', (data.goals_summary ? data.goals_summary.running : 0) + ' actief') : '') +
    '</div>';
    if (goalOn) {
      var gs = data.goals_summary || {};
      html += '<div class="kpi-grid" style="grid-template-columns:repeat(5,1fr);margin-top:8px">' +
        kpiBox('Totaal doelen', gs.total||0) +
        kpiBox('Actief', gs.running||0, '', '') +
        kpiBox('Gereed', gs.completed||0, '', '') +
        kpiBox('Mislukt', gs.failed||0, '', '') +
        kpiBox('Gepauzeerd', gs.paused||0, '', '') +
      '</div>';
    }
    html += '</div></details>';
    html += '</div>';

    html += '</div>';
    main.innerHTML = html;
    // Grafieken pas tekenen als de canvassen al in de DOM staan (Chart.js
    // heeft écht opgemeten formaat nodig) — de sectie is nooit display:none
    // (subnav is anchor-based, geen tonen/verbergen), dus dit hoeft niet
    // lazy achter een klik zoals de <details>-accordions elders op dit scherm.
    if (irisScores && irisScores.projects && irisScores.projects.length) {
      // renderProjectScoreBar verwacht per project een 0-10-schaal — dat is
      // `grade` (het rapportcijfer), niet `score` (0-100, de som van de vier
      // pijlers). Verwar die twee niet: elke balk stond eerder op "10" omdat
      // `score` (bv. 45.8) ver boven de as-max van 10 lag.
      var barData = irisScores.projects.map(function(p) { return { project: p.project, score: p.grade }; });
      renderProjectScoreBar('cr-chart-scores', barData);
      // De vier pijlers wegen ongelijk (content max 25, seo max 35,
      // uitvoering max 20, hygiëne max 20 — zie iris/metrics.py) — zonder
      // normalisatie zou SEO de radar altijd domineren, niet omdat een
      // project daar beter op scoort maar omdat de as een grotere noemer
      // heeft. Elke pijler wordt daarom herschaald naar 0-10 vóór het
      // gemiddelde over projecten.
      var _pillarMax = { content: 25, seo: 35, uitvoering: 20, hygiene: 20 };
      var pillarSums = { content: 0, seo: 0, uitvoering: 0, hygiene: 0 };
      irisScores.projects.forEach(function(p) {
        var pil = p.pillars || {};
        Object.keys(_pillarMax).forEach(function(k) {
          var raw = (pil[k] && pil[k].score) || 0;
          pillarSums[k] += (raw / _pillarMax[k]) * 10;
        });
      });
      var n = irisScores.projects.length;
      renderPillarRadar('cr-chart-pillars', {
        content: pillarSums.content / n, seo: pillarSums.seo / n,
        uitvoering: pillarSums.uitvoering / n, hygiene: pillarSums.hygiene / n,
      });
    }
    loadActionCenter();
    startActionCenterRefresh();
    loadIrisPulse();
    loadIrisBriefing();
    loadSystemHealth();
    startAgentStatusPoll();
    // Postvak, activiteit en credits laden pas zodra hun <details> opengaat
    // (ontoggle hierboven) — dat scheelt bij elk bezoek drie API-calls voor
    // een paneel dat de meeste keren dicht blijft.
  }).catch(function(e){ main.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>'; });
}

// ── Hero-KPI-rij — de systeemcijfers die er in één oogopslag toe doen.
// Bron: `global`-blok uit /api/iris/scores (al puur lokale SQL, geen extra
// netwerk-call) + een client-side som van de weekbeeld-quick-wins. Valt
// een van beide calls weg (bv. een verse installatie zonder Iris-historie),
// dan tonen we gewoon minder tegels — nooit een verzonnen 0. ──
// ── Iris Pulse — "wat deed Iris deze week, en werkt het" ───────────────────
// Vat vijf domeinen samen (mail/agenda/content/leads/traffic) tot één
// verhaal bovenaan de Control Room. Losstaand geladen (niet in de eerste
// Promise.all) omdat de bron externe API's aanraakt (Outlook/Google/GA4,
// wel TTL-gecached) — een trage of falende sectie mag de rest van de
// Control Room niet vertragen of blokkeren.
function loadIrisPulse() {
  var el = document.getElementById('iris-pulse-panel');
  if (!el) return;
  fetch('/api/action-center/pulse').then(function(r){return r.json();}).then(function(p){
    if (!document.getElementById('iris-pulse-panel')) return;
    document.getElementById('iris-pulse-panel').innerHTML = renderIrisPulse(p);
  }).catch(function(){
    if (document.getElementById('iris-pulse-panel')) document.getElementById('iris-pulse-panel').innerHTML = '';
  });
}

// `tab` = welke SPA-tab de tegel opent bij een klik (leeg = niet klikbaar,
// zoals bij een niet-geconfigureerde bron waar geen scherm achter zit).
// Mail/Agenda/Leads zijn projectloze gegevens (Vincents eigen mailbox/
// agenda/acquisitie) maar de tabs erachter zijn wél project-gebonden — de
// klik valt daarom terug op WeAreImpact, dezelfde regel als acAction's
// open_tab-afhandeling hierboven.
function _pulseTile(label, value, sub, tone, tab) {
  var clickable = tab ? ' clickable" onclick="' + escAttr('pulseGoTo(' + JSON.stringify(tab) + ')') + '" role="button" tabindex="0"' : '"';
  return '<div class="hero-kpi ' + (tone||'') + clickable + '><p class="label">' + escHtml(label) + '</p>' +
    '<p class="value">' + escHtml(String(value)) + '</p>' +
    (sub ? '<p style="font-size:10px;color:#94a3b8;margin-top:2px">' + escHtml(sub) + '</p>' : '') + '</div>';
}

// Springt naar een tab, met het huidige project (of WeAreImpact als er nog
// geen gekozen is — de globale Control-Room-pulse leeft buiten een project).
function pulseGoTo(tab, project) {
  var proj = project || currentProject || 'WeAreImpact';
  currentProject = proj; currentTab = tab; weSuggestions = [];
  history.pushState(null, '', '#project=' + encodeURIComponent(proj));
  route();
}

function renderIrisPulse(p) {
  if (!p) return '';
  var tiles = [];

  var mail = p.mail || {};
  if (mail.status === 'ok') {
    var rate = (mail.week || {}).reply_rate;
    tiles.push(_pulseTile('Mail — achterstand', mail.backlog || 0,
      rate != null ? rate + '% deze week beantwoord' : 'reply-rate nog niet gemeten',
      (mail.backlog||0) === 0 ? 'tone-ok' : ((mail.backlog||0) >= 15 ? 'tone-danger' : 'tone-warn'),
      'Postvak'));
  } else {
    tiles.push(_pulseTile('Mail', '—', mail.reason || 'niet geconfigureerd', ''));
  }

  var agenda = p.agenda || {};
  if (agenda.status === 'ok') {
    var todayCount = (agenda.today || []).filter(function(e){return !e.declined;}).length;
    tiles.push(_pulseTile('Agenda — vandaag', todayCount + ' afspraken',
      (agenda.pending_proposals ? agenda.pending_proposals + ' voorstel(len) wachten' : 'geen open voorstellen'),
      agenda.pending_proposals ? 'tone-warn' : '',
      'Agenda'));
  } else {
    tiles.push(_pulseTile('Agenda', '—', agenda.reason || 'niet geconfigureerd', ''));
  }

  var content = p.content || {};
  if (content.status === 'ok') {
    tiles.push(_pulseTile('Content — deze week', content.published_7d || 0,
      content.in_wachtrij + ' in Wachtrij' + (content.stuck ? ' · ' + content.stuck + ' vastgelopen' : ''),
      content.stuck ? 'tone-danger' : ((content.published_7d||0) > 0 ? 'tone-ok' : ''),
      'Wachtrij'));
  }

  var leads = p.leads || {};
  if (leads.status === 'ok') {
    var f = leads.funnel || {};
    tiles.push(_pulseTile('Leads', (f.reached||{}).contacted || 0,
      f.formula || 'nog geen outreach verstuurd', '', 'Leads'));
  }

  var ga = p.analytics || {};
  if (ga.status === 'ok') {
    var sess = (ga.compare || {}).sessions;
    if (sess && sess.pct != null) {
      tiles.push(_pulseTile('Traffic — 7 vs. 7 dagen', (sess.pct > 0 ? '+' : '') + sess.pct + '%',
        sess.prev + ' → ' + sess.now + ' sessies',
        sess.pct <= -15 ? 'tone-danger' : (sess.pct >= 15 ? 'tone-ok' : ''),
        'Dashboard'));
    }
  } else {
    tiles.push(_pulseTile('Traffic', '—', ga.reason || 'GA4 niet geconfigureerd', ''));
  }

  var act = p.activity || {};
  var doneLabel = act.status === 'ok' ? act.done_7d : '—';

// Elk gebied uit build_pulse (mail/agenda/analytics/seo) heeft één scherm
// waar het item om vraagt; seo-items dragen hun eigen projectnaam (meerdere
// sites kunnen hier langskomen), de rest valt terug op het huidige/WeAreImpact-
// project — zelfde regel als de tegels hierboven.
var _pulseAreaTab = { mail: 'Postvak', agenda: 'Agenda', analytics: 'Dashboard', seo: 'Optimalisatie' };
function _pulseListItem(item, icon, color) {
  var tab = _pulseAreaTab[item.area];
  var text = escHtml(item.what) + (item.why ? '<span style="color:#94a3b8"> — ' + escHtml(item.why) + '</span>' : '');
  if (!tab) return '<li><span style="color:' + color + '">' + icon + '</span> ' + text + '</li>';
  return '<li class="clickable" style="cursor:pointer" onclick="' + escAttr('pulseGoTo(' + JSON.stringify(tab) + ', ' + JSON.stringify(item.project || null) + ')') + '">' +
    '<span style="color:' + color + '">' + icon + '</span> ' + text + '</li>';
}
  var goodHtml = (p.good || []).slice(0, 5).map(function(g){
    return _pulseListItem(g, '&#10003;', 'var(--green)');
  }).join('');
  var badHtml = (p.bad || []).slice(0, 6).map(function(b){
    var color = b.severity === 'hoog' ? 'var(--red)' : (b.severity === 'midden' ? 'var(--amber)' : '#94a3b8');
    return _pulseListItem(b, '&#9888;', color);
  }).join('');

  return '<div class="section-card" style="margin-bottom:16px">' +
    '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:2px">' +
    '<h4 style="font-size:14px;font-weight:700;color:var(--text)">Iris deze week</h4>' +
    '<span style="font-size:11px;color:#94a3b8">' + doneLabel + ' actie(s) afgerond · ' +
    (act.errors_7d ? act.errors_7d + ' met fout' : '0 fouten') + '</span>' +
    '</div>' +
    '<div class="hero-kpi-grid" style="margin-top:8px">' + tiles.join('') + '</div>' +
    (goodHtml || badHtml ?
      '<div class="grid-2" style="margin-top:12px;gap:16px">' +
      '<div><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:4px">Gaat goed</p>' +
      '<ul style="list-style:none;padding:0;margin:0;font-size:12px;color:var(--text);display:flex;flex-direction:column;gap:4px">' +
      (goodHtml || '<li style="color:#94a3b8">Nog niets uitgesproken positiefs deze ronde.</li>') + '</ul></div>' +
      '<div><p style="font-size:11px;font-weight:700;color:var(--text-dim);margin-bottom:4px">Vraagt aandacht</p>' +
      '<ul style="list-style:none;padding:0;margin:0;font-size:12px;color:var(--text);display:flex;flex-direction:column;gap:4px">' +
      (badHtml || '<li style="color:#94a3b8">Niets dat nu om actie vraagt.</li>') + '</ul></div>' +
      '</div>' : '') +
    '</div>';
}

function heroKpiRow(data, irisScores, weekly) {
  var g = (irisScores && irisScores.global) || null;
  var tiles = [];
  if (g) {
    tiles.push({ label: 'Fouten (24u)', value: g.errors_24h || 0, tone: (g.errors_24h||0) > 0 ? 'tone-danger' : 'tone-ok' });
    tiles.push({ label: 'Wacht op review', value: g.pending_review_total || 0, tone: (g.pending_review_total||0) > 0 ? '' : 'tone-ok' });
    tiles.push({ label: 'Opgeleverd (24u)', value: g.delivered_24h || 0, tone: 'tone-ok' });
  }
  if (weekly && weekly.projects) {
    var quickWins = weekly.projects.reduce(function(sum, p) { return sum + (p.quick_wins || 0); }, 0);
    tiles.push({ label: 'Quick wins deze week', value: quickWins, tone: quickWins > 0 ? 'tone-warn' : '' });
  }
  var gs = data.goals_summary || {};
  if (domainOn('goal')) tiles.push({ label: 'Actieve doelen', value: gs.running || 0, tone: '' });
  if (!tiles.length) return '';
  return '<div class="hero-kpi-grid">' + tiles.map(function(t) {
    return '<div class="hero-kpi ' + t.tone + '"><p class="label">' + escHtml(t.label) + '</p><p class="value">' + t.value + '</p></div>';
  }).join('') + '</div>';
}

// ── Analytics-grafieken — projectscores (wie heeft aandacht nodig) en het
// gemiddelde van Iris' vier pijlers. Beide uit dezelfde /api/iris/scores-call
// die de hero-rij ook al gebruikte; de canvassen worden pas na
// main.innerHTML getekend (zie renderHome), hier staat alleen de markup. ──
function analyticsChartsBlock(irisScores) {
  if (!irisScores || !irisScores.projects || !irisScores.projects.length) return '';
  return '<div class="grid-2" style="margin-bottom:16px">' +
    '<div class="chart-card"><h4>Projectcijfer — wie heeft aandacht nodig</h4>' +
    '<div class="chart-box" style="height:' + Math.max(160, irisScores.projects.length * 28) + 'px"><canvas id="cr-chart-scores"></canvas></div></div>' +
    '<div class="chart-card"><h4>Gemiddelde pijlers — alle projecten</h4>' +
    '<div class="chart-box" style="height:260px"><canvas id="cr-chart-pillars"></canvas></div></div>' +
    '</div>';
}

// ── Actiecentrum — één inbox met alles wat op jou wacht ────────────
// Eén schaal (pill-warn/-info/-danger/-ok/-neutral) i.p.v. een losse hexkleur
// + emoji per soort item — betekenis (let op / neutraal / fout) draagt de
// kleur, niet elk kaartsoort zijn eigen plaatje.
var _acKindMeta = {
  goal_draft:    { pill: 'pill-warn', label: 'Plan wacht op akkoord', icon: '📝' },
  goal_ready:    { pill: 'pill-info', label: 'Klaar om te starten', icon: '▶' },
  goal_failed:   { pill: 'pill-danger', label: 'Vastgelopen doel', icon: '⚠' },
  content_review:{ pill: 'pill-info', label: 'Content ter review', icon: '📄' },
  content_needs_work: { pill: 'pill-warn', label: 'Onder kwaliteitsgrens', icon: '✎' },
  content_stuck: { pill: 'pill-danger', label: 'Vastgelopen content', icon: '⚠' },
  task_approval: { pill: 'pill-info', label: 'Taak wacht op goedkeuring', icon: '☑' },
  vacancies:     { pill: 'pill-ok', label: 'Opdracht-kansen', icon: '💼' },
  leads:         { pill: 'pill-ok', label: 'Nieuwe leads', icon: '👤' },
  linkbuilding_review: { pill: 'pill-info', label: 'Link-outreach ter review', icon: '🔗' },
  outreach_review: { pill: 'pill-info', label: 'Outreach ter review', icon: '✉' },
  calendar_proposal: { pill: 'pill-info', label: 'Agenda-voorstel', icon: '📅' },
  mail_reply:    { pill: 'pill-info', label: 'Mail wacht op antwoord', icon: '✉' },
  personal_mail: { pill: 'pill-info', label: 'Persoonlijke mail', icon: '✉' },
  social_msg:    { pill: 'pill-info', label: 'Social bericht', icon: '💬' },
  error:         { pill: 'pill-danger', label: 'Fout', icon: '⚠' }
};
// Kinds die al hun eigen tab hebben (Agenda, Postvak) — nogmaals als kaart in
// een Actiecentrum-lijst tonen is dezelfde beslissing op twee plekken
// aanbieden. Gedeeld door home.js (globale Control Room), shell.js (generiek
// project-dashboard) en tabs-weareimpact.js — hier gedefinieerd omdat home.js
// als eerste laadt (zie index.html). Ze blijven wél in build_inbox() zelf
// staan: bridge/digest lezen daar rechtstreeks van, buiten deze schermen om.
// 26 aug 2026: agenda-voorstellen en mail stonden dubbel, zowel bovenaan als
// in "Overige acties" / de globale lijst.
var _AC_HAS_OWN_TAB_KINDS = { calendar_proposal: 1, mail_reply: 1, personal_mail: 1 };
// Pil-klasse → volle randkleur, voor de gekleurde linkerrand op elke
// Actiecentrum-kaart (dezelfde betekenis-schaal als de pillen zelf).
function _pillBorderColor(pillClass) {
  if (pillClass === 'pill-danger') return 'var(--red)';
  if (pillClass === 'pill-warn') return 'var(--amber)';
  if (pillClass === 'pill-info') return 'var(--accent)';
  if (pillClass === 'pill-ok') return 'var(--green)';
  return 'var(--card-border)';
}

var _acLastItems = [];
var _acRefreshTimer = null;
function startActionCenterRefresh() {
  if (_acRefreshTimer) clearInterval(_acRefreshTimer);
  _acRefreshTimer = setInterval(function(){
    if (!document.getElementById('action-center-panel')) { clearInterval(_acRefreshTimer); _acRefreshTimer = null; return; }
    loadActionCenter();
  }, 30000);
}
function updateTabBadge(count) {
  document.title = count > 0 ? '(' + count + ') Impact OS' : 'Impact OS';
}

function loadActionCenter() {
  var el = document.getElementById('action-center-panel');
  if (!el) return;
  fetch('/api/action-center').then(function(r){return r.json();}).then(function(data){
    if (!el) return;
    var rawItems = data.items || [];
    _acLastItems = rawItems;
    // calendar_proposal/mail_reply/personal_mail hebben al hun eigen tab
    // (Agenda, Postvak) — hier nogmaals als kaart tonen is dezelfde beslissing
    // op twee plekken aanbieden (26 aug 2026). De GSC-bulkactie hieronder telt
    // wél nog op rawItems: dat is een cross-cutting actie, geen kaart.
    var items = rawItems.filter(function(i){ return !_AC_HAS_OWN_TAB_KINDS[i.kind]; });
    updateTabBadge(items.length);
    if (!items.length) {
      el.innerHTML = '<div class="section-card" style="margin-bottom:16px;background:var(--ok-bg);border-color:var(--ok-border)">' +
        '<span style="font-size:13px;color:var(--ok-fg);font-weight:600">Niets wacht op jou.</span> ' +
        '<span style="font-size:12px;color:var(--ok-fg)">De agents draaien op schema.</span></div>';
      return;
    }
    // ── Samenvattingsstrip: aantal per soort, uit dezelfde platte lijst die
    // hieronder ook de kaarten tekent — geen aparte call, puur groeperen. ──
    var kindCounts = {};
    items.forEach(function(i){ kindCounts[i.kind] = (kindCounts[i.kind]||0) + 1; });
    var chipsHtml = '<div class="ac-kind-chips">' + Object.keys(kindCounts).map(function(k){
      var meta = _acKindMeta[k] || { icon: '•', label: k };
      return '<span class="ac-kind-chip" title="' + escAttr(meta.label) + '">' + meta.icon + ' ' + kindCounts[k] + '</span>';
    }).join('') + '</div>';
    var draftCount = items.filter(function(i){ return i.kind === 'goal_draft'; }).length;
    var bulkBar = '';
    if (draftCount >= 3) {
      bulkBar = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;padding:8px 12px;background:var(--warn-bg);border:1px solid var(--warn-border);border-radius:var(--radius-sm)">' +
        '<span style="font-size:11px;color:var(--warn-fg);flex:1"><b>' + draftCount + ' doelen</b> wachten op je akkoord — in één keer afhandelen:</span>' +
        '<button onclick="acBulkDrafts(this, \'start\')" class="btn btn-primary btn-sm" style="background:var(--green)">Start alles</button>' +
        '<button onclick="acBulkDrafts(this, \'delete\')" class="btn btn-danger-outline btn-sm">Verwijder alles</button></div>';
    }
    // GSC-expert bulk: alle wachtende Search Console-meldingen in één keer
    // door de agent laten analyseren (en veilig verzenden/oplossen).
    var gscCount = rawItems.filter(function(i){
      return i.kind === 'mail_reply' && i.actions.some(function(a){ return a.type === 'mail_gsc_fix'; });
    }).length;
    if (gscCount >= 1) {
      bulkBar += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;padding:8px 12px;background:var(--warn-bg);border:1px solid var(--warn-border);border-radius:var(--radius-sm)">' +
        '<span style="font-size:11px;color:var(--warn-fg);flex:1"><b>' + gscCount + ' Search Console-melding(en)</b> — laat de GSC-expert ze analyseren &amp; afhandelen:</span>' +
        '<button onclick="acGscFixAll(this)" class="btn btn-primary btn-sm">Verwerk alle GSC</button></div>';
    }
    // Fout-triage bulk: alle foutkaarten (activity_log-fouten + mislukte
    // content_jobs) in één keer analyseren & herstellen — in plaats van ze
    // stuk voor stuk aan te klikken. Patroon-fouten (OpenModel-down, MS-auth,
    // catch-up) worden deterministisch gediagnosticeerd, zonder LLM per kaart.
    var errorCount = items.filter(function(i){
      return i.kind === 'error' || (i.kind === 'content_needs_work' || i.kind === 'publish_failed');
    }).length;
    if (errorCount >= 1) {
      bulkBar += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;padding:8px 12px;background:var(--info-bg);border:1px solid var(--info-border);border-radius:var(--radius-sm)">' +
        '<span style="font-size:11px;color:var(--info-fg);flex:1"><b>' + errorCount + ' foutkaart(en)</b> — laat Iris ze allemaal analyseren &amp; afhandelen:</span>' +
        '<button onclick="acTriageAll(this)" class="btn btn-primary btn-sm">Analyseer alle fouten</button></div>';
    }
    var html = '<div class="section-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">' +
      '<h3 style="font-size:14px;font-weight:700;color:var(--text)">Vandaag — wacht op jou (' + items.length + ')</h3>' +
      '<span style="font-size:11px;color:#94a3b8">' + (data.counts.errors ? data.counts.errors + ' fout(en) · ' : '') + 'klik = klaar</span></div>' +
      chipsHtml + bulkBar;
    items.forEach(function(it, idx){
      var meta = _acKindMeta[it.kind] || { pill: 'pill-neutral', label: it.kind };
      // Voor content-review tonen we een type-specifieke tag zodat in één
      // oogopslag duidelijk is wat het is — en wat de knoppen doen. Een hook is
      // géén pagina, LinkedIn is géén site-publicatie; die verschillen moeten
      // zichtbaar zijn vóórdat je op "Publiceer" klikt.
      if (it.kind === 'content_review') {
        var ct = (it.content_type || 'blog').toLowerCase();
        if (ct === 'linkedin_outreach') {
          meta = { pill: 'pill-warn', label: 'LinkedIn · géén site-pagina' };
        } else if (ct === 'hook' || ct === 'snippet' || ct === 'social_snippet') {
          meta = { pill: 'pill-warn', label: 'SEO-hook · géén artikel' };
        } else {
          meta = { pill: 'pill-info', label: 'Artikel · wordt gepubliceerd' };
        }
      }
      // Tijdstempel: volle datum + tijd (NL), zodat je per kaart ziet wanneer
      // het item écht binnenkwam of aangemaakt is — niet alleen de dag. Voor
      // mail tonen we de binnenkomsttijd van de oorspronkelijke mail (de
      // backend zet created_at al op received_at als die er is).
      var when = it.created_at ? '<span style="color:#94a3b8;font-size:10px;flex-shrink:0">' + escHtml(_fmtNlDateTime(it.created_at)) + '</span>' : '';
      // Lead-status-badge op de kaart zelf (calendar-voorstellen): meteen
      // zichtbaar of dit een bekende klant of een nieuwe lead is, vóórdat je
      // op "Plan in agenda" klikt.
      var leadBadge = '';
      if (it.kind === 'calendar_proposal' && it.detail && it.detail.lead_status) {
        var _ls = it.detail.lead_status;
        if (_ls.known) {
          leadBadge = '<span style="font-size:10px;color:#065f46;background:#d1fae5;padding:1px 6px;border-radius:4px;font-weight:600">✓ bekende klant/lead</span>';
        } else if (_ls.tier === 'warm') {
          leadBadge = '<span style="font-size:10px;color:#92400e;background:#fef3c7;padding:1px 6px;border-radius:4px;font-weight:600">● warm contact</span>';
        } else {
          leadBadge = '<span style="font-size:10px;color:#9a3412;background:#ffedd5;padding:1px 6px;border-radius:4px;font-weight:600">★ nieuwe lead</span>';
        }
      }
      html += '<div id="ac-item-' + idx + '" style="padding:10px 4px 10px 12px;border-bottom:1px solid #f1f5f9;border-left:3px solid ' + _pillBorderColor(meta.pill) + '">' +
        '<div style="flex:1;min-width:0">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">' +
        '<span class="pill ' + meta.pill + '">' + (_acKindMeta[it.kind] ? _acKindMeta[it.kind].icon + ' ' : '') + escHtml(meta.label) + '</span>' +
        '<span style="font-size:10px;color:#64748b;background:var(--neutral-bg);padding:1px 6px;border-radius:4px">' + escHtml(it.project || '') + '</span>' +
        (it.flag ? '<span style="font-size:10px;color:#065f46;background:#d1fae5;padding:1px 6px;border-radius:4px;font-weight:600">' + escHtml(it.flag) + '</span>' : '') + leadBadge + when + '</div>' +
        '<p style="font-size:13px;font-weight:600;color:var(--text);margin:2px 0">' + escHtml(it.title) + '</p>' +
        '<p style="font-size:11px;color:#64748b;margin-bottom:6px">' + escHtml(it.summary || '') + '</p>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        it.actions.map(function(a){
          var cls = a.danger ? 'btn-danger-outline'
            : (a.accent) ? 'btn-primary'   // GSC-expert
            : (a.type === 'open_tab' || a.type === 'dismiss') ? 'btn-ghost'
            : 'btn-primary';
          return '<button onclick=\'acAction(this, ' + JSON.stringify(a).replace(/'/g, '&#39;') + ', ' + JSON.stringify(it.project || '') + ')\' ' +
            'class="btn btn-sm ' + cls + '">' + escHtml(a.label) + '</button>';
        }).join('') +
        // 'Markeer als bekend': alleen bij mail van een nog-onbekende afzender.
        // Na één klik onthoudt het systeem wie dit is (known_senders-register)
        // en verdwijnt de 'Nieuwe afzender'-vlag bij volgende mails. Bij een
        // al-bekende afzender (sender_known=true) tonen we de knop niet.
        (it.kind === 'mail_reply' && !it.sender_known ? '<button onclick="acMarkSenderKnown(this, ' + String(it.id) + ')" class="btn btn-sm btn-ghost">Markeer als bekend</button>' : '') +
        // Voor een MS-auth-fout toont het backend-antwoord (na 'Analyseer & fix')
        // de vlag reconnect_ms — maar de kaart staat er al mét die fout vóórdat
        // je klikt. We herkennen hem direct aan de titel/summary en bieden de
        // echte re-auth-knop aan, zodat je niet eerst "Analyseer & fix" hoeft.
        (it.reconnect_ms || /niet geauthenticeerd bij microsoft/i.test(it.summary || '') || /microsoft/i.test(it.title || '')
          ? '<button onclick="acReconnectMicrosoft(this, ' + JSON.stringify(it.project || '') + ')" class="btn btn-sm btn-primary">Verbind Microsoft opnieuw</button>'
          : '') +
        '</div></div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div class="section-card" style="margin-bottom:16px;background:var(--danger-bg);border-color:var(--danger-border)">' +
      '<span style="font-size:12px;color:var(--danger-fg)">Actiecentrum laden mislukt: ' + escHtml(e.message) + '</span></div>';
  });
}

var _digestLoaded = false;
function loadDigest() {
  if (_digestLoaded) return;
  var el = document.getElementById('digest-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  fetch('/api/action-center/digest').then(function(r){return r.json();}).then(function(d){
    _digestLoaded = true;
    el.innerHTML = '<div class="strategist-analyse-content">' + mdToHtml(d.markdown || '') + '</div>';
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Rapport laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Postvak — gesorteerd naar wat een mail van jou nodig heeft ─────
// Hergebruikt precies de triage die al bestond (urgent/actie/wacht/info,
// zie outlook/service.py _TRIAGE_SYSTEM) via GET /api/outlook/sorted.
// Ontwerp (12 aug 2026, na een screenshot met 13× dezelfde systeemmail
// in "Reageren"): een kop telt wat om een handeling van Vincent vraagt,
// niet wat er toevallig binnenkwam — 'needs_reply' krijgt daarom de volle
// rij (afzender, tijd, onderwerp), 'wacht'/'ter info' alleen een telling,
// want die twee vragen niets van hem op déze kaart. Systeemruis die wél
// binnenkomt hoort hier al uitgefilterd te zijn (zie rules.py); wat de
// regels wegnamen staat als transparante voetnoot, niet stilzwijgend weg.
var _sortedInboxMeta = {
  waiting: { label: 'Wacht op reactie van hen' },
  fyi: { label: 'Ter informatie' }
};
var _sortedInboxSecondary = ['waiting', 'fyi'];

// Deterministische, gedempte kleur per afzendernaam — geen emoji, wel
// direct te onderscheiden in een lijst van gelijkvormige regels.
var _AVATAR_PALETTE = ['#475569', '#0f766e', '#7c3aed', '#b45309', '#1d4ed8', '#be123c', '#4d7c0f'];
function _avatarColor(name) {
  var s = name || '?';
  var h = 0;
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return _AVATAR_PALETTE[h % _AVATAR_PALETTE.length];
}
function _initial(name) {
  var s = (name || '').trim();
  return s ? s.charAt(0).toUpperCase() : '?';
}
function _relTime(iso) {
  if (!iso) return '';
  var d = new Date(iso.replace(' ', 'T') + (iso.indexOf('Z') === -1 && iso.indexOf('+') === -1 ? 'Z' : ''));
  if (isNaN(d)) return '';
  var mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'zojuist';
  if (mins < 60) return mins + 'm';
  var hrs = Math.round(mins / 60);
  if (hrs < 24) return hrs + 'u';
  var days = Math.round(hrs / 24);
  return days + 'd';
}

function loadSortedInbox() {
  var el = document.getElementById('sorted-inbox-panel');
  if (!el) return;
  fetch('/api/outlook/status').then(function(r){return r.json();}).then(function(status){
    if (!el) return;
    if (!status.configured) { el.innerHTML = '<div style="color:#94a3b8">Outlook niet geconfigureerd.</div>'; return; }
    if (!status.authenticated) { el.innerHTML = '<div style="color:#94a3b8">Outlook niet ingelogd — koppel je account via de Postvak-tab.</div>'; return; }
    return fetch('/api/outlook/sorted').then(function(r){return r.json();}).then(function(d){
      if (!el) return;
      var needsReply = d.needs_reply || [];
      var footNotes = [];
      if (d.untriaged) footNotes.push(d.untriaged + ' nog niet getrieerd');
      if (d.filtered) footNotes.push(d.filtered + ' automatisch gefilterd (nieuwsbrief/systeem/geen klant)');
      var foot = footNotes.length
        ? '<div style="margin-top:10px;padding-top:8px;border-top:1px solid #f1f5f9;font-size:10.5px;color:#94a3b8">' + escHtml(footNotes.join(' · ')) + '</div>'
        : '';

      var html = '';
      if (needsReply.length) {
        html += '<div style="font-size:12px;font-weight:600;color:#1e293b;margin-bottom:6px">' +
          needsReply.length + ' mail' + (needsReply.length === 1 ? '' : 's') + ' wachten op jouw antwoord</div>';
        needsReply.forEach(function(m){
          var name = m.from_name || m.from_email || 'Onbekend';
          html += '<div style="display:flex;align-items:flex-start;gap:8px;padding:7px 4px;border-bottom:1px solid #f1f5f9">' +
            '<div style="flex-shrink:0;width:22px;height:22px;border-radius:50%;background:' + _avatarColor(name) + ';color:#fff;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;margin-top:1px">' + escHtml(_initial(name)) + '</div>' +
            '<div style="flex:1;min-width:0">' +
            '<div style="display:flex;align-items:baseline;gap:6px">' +
            '<span style="font-size:12px;font-weight:600;color:#1e293b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(name) + '</span>' +
            '<span style="font-size:10px;color:#94a3b8;flex-shrink:0">' + escHtml(_relTime(m.received_at)) + '</span>' +
            '</div>' +
            '<div style="font-size:11.5px;color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(m.subject || '(geen onderwerp)') + '</div>' +
            '</div>' +
            (m.suggested_reply ? '<span style="flex-shrink:0;font-size:9.5px;font-weight:600;color:#4f46e5;background:#eef2ff;border:1px solid #e0e7ff;padding:2px 7px;border-radius:4px;margin-top:1px">Concept klaar</span>' : '') +
            '</div>';
        });
      } else {
        html += '<div style="font-size:12px;color:#166534;font-weight:600">Niets wacht op jouw antwoord.</div>';
      }

      var secondaryBits = _sortedInboxSecondary
        .map(function(key){ var n = (d[key] || []).length; return n ? n + ' ' + _sortedInboxMeta[key].label.toLowerCase() : null; })
        .filter(Boolean);
      if (secondaryBits.length) {
        html += '<div style="margin-top:8px;font-size:11px;color:#94a3b8">' + escHtml(secondaryBits.join(' · ')) + '</div>';
      }

      html += foot;
      el.innerHTML = html;
    });
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Postvak laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Linkbuilding — funnel, live links en open kansen ───────────────
// Gedeelde HTML-builder: gebruikt door het Control Room-paneel (alle sites)
// én de Links-tab per project (tabs-business.js, gescoped op site_id).
function buildLinkbuildingHtml(f, prospects, live) {
    var by = f.by_status || {}, r = f.reached || {};
    var html = '<div class="kpi-grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:10px">' +
      kpiBox('Kansen', f.total_prospects||0) +
      kpiBox('Gekwalificeerd', by.qualified||0) +
      kpiBox('Benaderd', r.contacted||0) +
      kpiBox('Links live', r.link_live||0, '', (f.dofollow_live||0) + ' dofollow') +
      kpiBox('Geverifieerd', r.verified||0) + '</div>';
    if (f.formula) html += '<div style="font-size:12px;font-weight:600;color:#16a34a;margin-bottom:8px">\u{1F4C8} ' + escHtml(f.formula) + '</div>';
    var review = by.outreach_review || 0;
    if (review) html += '<div style="font-size:12px;color:#b45309;margin-bottom:8px">\u{270B} ' + review + ' concept(en) wachten op je verzendklik — zie het Actiecentrum bovenaan.</div>';
    if (live.length) {
      html += '<div style="font-size:12px;font-weight:600;margin-bottom:4px">Links live (' + live.length + ')</div>';
      live.slice(0, 10).forEach(function(pl){
        html += '<div style="font-size:11px;color:#475569;margin-bottom:2px">\u{2713} ' +
          '<a href="' + escHtml(pl.source_url) + '" target="_blank" rel="noopener">' + escHtml(pl.source_url) + '</a>' +
          ' \u{2192} ' + escHtml(pl.target_url) +
          (pl.rel ? ' <span style="color:#94a3b8">(' + escHtml(pl.rel) + ')</span>'
                  : ' <span style="color:#16a34a">(dofollow)</span>') + '</div>';
      });
    }
    var open = prospects.filter(function(p){ return p.status === 'qualified' || p.status === 'new'; }).slice(0, 10);
    if (open.length) {
      html += '<div style="font-size:12px;font-weight:600;margin:8px 0 4px">Beste open kansen</div>';
      open.forEach(function(p){
        html += '<div style="font-size:11px;color:#475569;margin-bottom:2px">' +
          '<span style="font-weight:600">' + (p.relevance_score||0) + '</span> \u{00B7} ' + escHtml(p.domain) +
          ' <span style="color:#94a3b8">(' + escHtml(p.prospect_type||'overig') +
          (p.contact_email ? ' \u{00B7} ' + escHtml(p.contact_email) : ' \u{00B7} geen e-mail') + ')</span>' +
          (p.rationale ? '<div style="color:#94a3b8;margin-left:14px">' + escHtml(p.rationale) + '</div>' : '') + '</div>';
      });
    }
    if (!(f.total_prospects||0)) html += '<div style="color:#64748b">Nog geen linkkansen — klik "Zoek kansen" (of wacht op de wekelijkse run van woensdag).</div>';
    return html;
}

function loadLinkbuilding() {
  var el = document.getElementById('linkbuilding-panel');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b">Laden...</div>';
  Promise.all([
    fetch('/api/linkbuilding/funnel').then(function(r){return r.json();}),
    fetch('/api/linkbuilding/prospects').then(function(r){return r.json();}),
    fetch('/api/linkbuilding/placements?status=live').then(function(r){return r.json();})
  ]).then(function(res){
    el.innerHTML = buildLinkbuildingHtml(res[0] || {}, res[1] || [], res[2] || []);
  }).catch(function(e){ el.innerHTML = '<div style="color:#ef4444">Laden mislukt: ' + escHtml(e.message) + '</div>'; });
}

// Herlaad wat er zichtbaar is: het Control Room-paneel of de Links-tab.
function _refreshLinkbuildingView() {
  if (document.getElementById('linkbuilding-panel')) loadLinkbuilding();
  if (typeof currentTab !== 'undefined' && currentTab === 'Links') {
    var tc = document.getElementById('tab-content');
    if (tc) renderLinksTab(tc);
  }
}

function runLinkbuildingProspecting(btn, siteId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Agent zoekt... (kan een minuut duren)'; }
  post('/api/linkbuilding/prospect-run' + (siteId ? '?site_id=' + encodeURIComponent(siteId) : '')).then(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'Zoek kansen'; }
    _refreshLinkbuildingView();
  }).catch(function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Zoek kansen'; }
    alert('Prospect-run mislukt: ' + e.message);
  });
}

function runLinkbuildingBatch(btn, siteId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Agent schrijft... (kan even duren)'; }
  post('/api/linkbuilding/outreach-batch' + (siteId ? '?site_id=' + encodeURIComponent(siteId) : '')).then(function(d){
    if (btn) { btn.disabled = false; btn.textContent = 'Maak concepten (review)'; }
    _refreshLinkbuildingView();
    if (typeof loadActionCenter === 'function' && document.getElementById('action-center-panel')) loadActionCenter();
    alert((d.drafted||0) + ' concept(en) klaargezet ter review — versturen blijft jouw klik.');
  }).catch(function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Maak concepten (review)'; }
    alert('Batch mislukt: ' + e.message);
  });
}

// ── OpenModel-credits — live verbruik (llm_usage-telemetrie) ───────
// Toont waar de credits vandaag heengaan: budgetbalk, grootverbruikers
// per doel/model en de 7-daagse trend. Ververst elke 30s, net als de inbox.
var _llmRouteLabels = {
  'iris': 'Iris (manager-analyse)',
  'content': 'Content-pipeline (schrijven & review)',
  'goal': 'Doelen (synthese)',
  'goal-plan': 'Doelen (planning/decompositie)',
  'goal-alternative': 'Doelen (zelfcorrectie)',
  'mail': 'Mail-helpdesk (concepten)',
  'mail-triage': 'Postvak (triage)',
  'mail-draft': 'Postvak (conceptantwoord, live)',
  'mail-suggested-reply': 'Postvak (conceptantwoord, batch)',
  'outreach': 'Outreach-concepten',
  'linkbuilding': 'Linkbuilding (kwalificatie & concepten)',
  'seo-engine': 'SEO Demand Engine',
  'seo-optimizer': 'SEO-optimalisatie',
  'seo-loop': 'SEO-loop (verbetervoorstel)',
  'gauntlet-decompose': 'Gauntlet (opdracht opsplitsen)',
  'gauntlet-write': 'Gauntlet (schrijven)',
  'gauntlet-review': 'Gauntlet (blinde criticus)',
  'strategist-analyse': 'Strategist (analyse)',
  'strategist-execute': 'Strategist (acties bepalen)',
  'radar-angle': 'Radar (invalshoek)',
  'radar-relevantie': 'Radar (relevantie-rechter)',
  'radar-multimedia': 'Radar (contentpakket)',
  'radar-infographic': 'Radar (infographic)',
  'vacancies-fit': 'Vacature Fit-Analist',
  'pipeline-triage': 'Contentmotor (planning)',
  'analytics': 'Analytics Analist',
  'finance': 'Financieel rapport',
  'agent-openmodel': 'Chat-agent (tools)',
  'claude-openmodel': 'Denk-werk (ongelabeld)',
  'hermes-openmodel': 'Bulk-werk (ongelabeld)'
};

// Routes met een dynamisch staartje (conveyor:<profiel>, delegate:<profiel>,
// loop:<profiel>, chat:<agent>) dragen de agent-naam al leesbaar in zichzelf —
// alleen het voorvoegsel hoeft een Nederlands label.
var _llmRoutePrefixLabels = {
  'conveyor': 'Contentmotor',
  'delegate': 'Delegate-team',
  'loop': 'Verbeterlus',
  'chat': 'Chat'
};

function _llmRouteLabel(route) {
  if (_llmRouteLabels[route]) return _llmRouteLabels[route];
  var idx = (route || '').indexOf(':');
  if (idx > 0) {
    var prefix = route.slice(0, idx);
    var rest = route.slice(idx + 1);
    if (_llmRoutePrefixLabels[prefix]) return _llmRoutePrefixLabels[prefix] + ' — ' + rest;
  }
  return route;
}

function _fmtTokens(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2).replace('.', ',') + 'M';
  if (n >= 1000) return Math.round(n / 1000) + 'k';
  return String(n);
}

var _llmUsageTimer = null;
function startLlmUsageRefresh() {
  if (_llmUsageTimer) clearInterval(_llmUsageTimer);
  _llmUsageTimer = setInterval(function(){
    if (!document.getElementById('llm-usage-panel')) { clearInterval(_llmUsageTimer); _llmUsageTimer = null; return; }
    loadLlmUsage();
  }, 30000);
}
// Het Systeem-paneel is nu ingeklapt; de 30s-poller moet pas draaien nadat
// iemand het daadwerkelijk heeft opengeklikt — anders telt elk Control
// Room-bezoek mee alsof het paneel de hele tijd open stond.
function _ensureLlmUsageRefresh() {
  if (!_llmUsageTimer) startLlmUsageRefresh();
}

function loadLlmUsage() {
  var el = document.getElementById('llm-usage-panel');
  if (!el) return;
  fetch('/api/action-center/llm-usage').then(function(r){return r.json();}).then(function(d){
    if (!el) return;
    var t = d.today || {};
    var html = '';

    // Budgetbalk — vandaag t.o.v. DAILY_TOKEN_BUDGET
    var pct = t.budget_pct != null ? t.budget_pct : 0;
    var barColor = pct >= 90 ? '#dc2626' : (pct >= 70 ? '#d97706' : '#16a34a');
    html += '<div style="margin-bottom:10px">' +
      '<div style="display:flex;justify-content:space-between;gap:8px;font-size:11px;color:#475569;margin-bottom:3px;flex-wrap:wrap">' +
      '<span><b>Vandaag:</b> ' + _fmtTokens(t.total_tokens) + ' van ' + _fmtTokens(d.budget) + ' tokens (' + pct + '% van het dagbudget)' +
      (t.cost != null ? ' · ≈ $' + t.cost.toFixed(2) : '') + '</span>' +
      '<span>' + (t.calls || 0) + ' calls' + (t.errors ? ' · <span style="color:#dc2626;font-weight:600">' + t.errors + ' fout(en)</span>' : '') + '</span></div>' +
      '<div style="height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden">' +
      '<div style="height:100%;width:' + Math.min(100, pct) + '%;background:' + barColor + ';border-radius:4px"></div></div></div>';

    // Grootverbruikers vandaag — per doel + model, aflopend
    var routes = d.by_route || [];
    if (!routes.length) {
      html += '<div style="color:#94a3b8">Nog geen LLM-verbruik vandaag.</div>';
    } else {
      html += '<div style="font-size:11px;font-weight:600;color:#334155;margin-bottom:4px">Grootverbruikers vandaag</div>';
      var max = routes[0].total_tokens || 1;
      routes.slice(0, 8).forEach(function(r){
        var share = t.total_tokens ? Math.round(100 * r.total_tokens / t.total_tokens) : 0;
        var label = _llmRouteLabel(r.route);
        html += '<div style="display:flex;align-items:center;gap:8px;padding:3px 0">' +
          '<span style="flex:0 0 230px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="route: ' + escHtml(r.route) + '">' +
          escHtml(label) + ' <span style="color:#94a3b8;font-size:10px">' + escHtml(r.model || '') + '</span></span>' +
          '<div style="flex:1;height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden">' +
          '<div style="height:100%;width:' + Math.max(2, Math.round(100 * r.total_tokens / max)) + '%;background:#4f46e5;border-radius:4px"></div></div>' +
          '<span style="flex:0 0 130px;text-align:right;color:#475569">' + _fmtTokens(r.total_tokens) + ' · ' + share + '%' +
          (r.cost != null ? ' · $' + r.cost.toFixed(2) : '') + '</span>' +
          '<span style="flex:0 0 55px;text-align:right;color:#94a3b8;font-size:10px">' + r.calls + '×' +
          (r.errors ? ' <span style="color:#dc2626">' + r.errors + ' fout</span>' : '') + '</span></div>';
      });
    }

    // Trend — laatste dagen (staafjes, vandaag donker)
    var days = d.days || [];
    if (days.length > 1) {
      var dmax = 1;
      days.forEach(function(x){ if (x.total_tokens > dmax) dmax = x.total_tokens; });
      html += '<div style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:8px">' +
        '<div style="font-size:11px;font-weight:600;color:#334155;margin-bottom:4px">Laatste ' + days.length + ' dagen (tokens per dag)</div>' +
        '<div style="display:flex;align-items:flex-end;gap:6px;height:52px">' +
        days.map(function(x, i){
          var h = Math.max(2, Math.round(40 * x.total_tokens / dmax));
          var isToday = i === days.length - 1;
          return '<div title="' + x.date + ': ' + _fmtTokens(x.total_tokens) + ' tokens (' + x.calls + ' calls)' +
            (x.cost != null ? ' · $' + x.cost.toFixed(2) : '') + '" style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:2px;height:100%">' +
            '<div style="width:100%;max-width:34px;height:' + h + 'px;background:' + (isToday ? '#4f46e5' : '#c7d2fe') + ';border-radius:4px 4px 0 0"></div>' +
            '<span style="font-size:9px;color:#94a3b8">' + x.date.slice(8, 10) + '/' + x.date.slice(5, 7) + '</span></div>';
        }).join('') + '</div></div>';
    }

    if (!d.prices_configured) {
      html += '<div style="margin-top:8px;font-size:10px;color:#94a3b8">Tip: zet <code>OPENMODEL_INPUT_COST_PER_MTOK</code> en <code>OPENMODEL_OUTPUT_COST_PER_MTOK</code> in .env om het verbruik ook in dollars/credits te zien.</div>';
    }
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Verbruik laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Weekbeeld — het trage zoekbeeld uit het weekrapport ────────────
// De projectcijfers hierboven draaien op 7-vs-7 dagen; dit is 28-vs-28. Ze
// horen te verschillen: een slechte week binnen een stijgende lijn is geen
// probleem. Zolang dit alleen in de mail stond, stuurde het niets aan.
function _weekDelta(v, suffix) {
  if (v == null) return '<span style="color:#94a3b8">n/b</span>';
  var kleur = v > 0 ? '#166534' : (v < 0 ? '#991b1b' : '#64748b');
  return '<span style="color:' + kleur + ';font-weight:600">' + (v > 0 ? '+' : '') + v + (suffix || '') + '</span>';
}

function loadWeekbeeld() {
  var el = document.getElementById('weekbeeld-panel');
  if (!el) return;
  fetch('/api/analytics/weekly-insights').then(function(r){return r.json();}).then(function(d){
    var s = d.summary || {};
    if (s.state === 'geen') {
      el.innerHTML = '<div style="color:#64748b">Nog geen weekrapport vastgelegd. ' +
        'De maandagrun (08:00) legt het eerste weekbeeld vast — dit is dus geen ' +
        'uitspraak over de prestaties.</div>';
      return;
    }
    var html = '<div style="font-size:11px;color:#64748b;margin-bottom:6px">Week ' + escHtml(s.week || '') +
      (s.state === 'verouderd' ? ' <span style="color:#b45309;font-weight:600">· ' + s.weken_oud +
        ' weken oud — de weekrun heeft sindsdien niet gedraaid</span>' : '') + '</div>';
    html += '<table style="width:100%;border-collapse:collapse;font-size:11px">' +
      '<tr style="color:#64748b;text-align:right"><th style="text-align:left">Project</th>' +
      '<th>Klikken</th><th>Δ</th><th>Impressies</th><th>Δ</th><th>Positie</th><th>Δ</th><th>Kansen</th></tr>';
    (s.projects || []).forEach(function(p){
      html += '<tr style="border-top:1px solid #f1f5f9;text-align:right">' +
        '<td style="text-align:left;font-weight:600">' + escHtml(p.project) + '</td>' +
        '<td>' + p.clicks + '</td><td>' + _weekDelta(p.clicks_pct, '%') + '</td>' +
        '<td>' + p.impressions + '</td><td>' + _weekDelta(p.impressions_pct, '%') + '</td>' +
        '<td>' + p.position + '</td><td>' + _weekDelta(p.position_delta, '') + '</td>' +
        '<td>' + (p.quick_wins || 0) + (p.ctr_fix ? ' · ' + p.ctr_fix + ' CTR' : '') + '</td></tr>';
    });
    html += '</table>';
    html += '<div style="font-size:10px;color:#94a3b8;margin-top:4px">Positie: lager is beter, ' +
      'dus een positieve Δ is winst. "Kansen" = quick wins (positie 4-15 met volume) · ' +
      'CTR = veel vertoningen bij minder dan 2% doorklik (snippet-probleem, geen nieuw artikel).</div>';
    if ((s.structureel_dalend || []).length) {
      html += '<div style="margin-top:6px;padding:6px 8px;border-radius:6px;background:#fef2f2;color:#991b1b;font-size:11px">' +
        '<strong>Structureel dalend</strong> (volume én positie over 28 dagen): ' +
        s.structureel_dalend.map(escHtml).join(', ') + ' — dit is geen weekruis.</div>';
    }
    var blijvers = d.blijvers || [];
    if (blijvers.length) {
      html += '<div style="margin-top:6px;padding:6px 8px;border-radius:6px;background:#fffbeb;color:#92400e;font-size:11px">' +
        '<strong>Blijft liggen:</strong> ' + blijvers.slice(0, 5).map(function(b){
          return escHtml(b.query) + ' (' + escHtml(b.project || '') + ', ' + b.weken + ' wk)';
        }).join(' · ') + ' — een kans die zich elke week herhaalt zonder te bewegen, wordt niet opgepakt.</div>';
    }
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Weekbeeld laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Iris — dagbriefing van de manager-agent ────────────────────────
function _irisGradeColor(cijfer) {
  if (cijfer >= 8) return ['#dcfce7', '#166534'];
  if (cijfer >= 6) return ['#fef9c3', '#854d0e'];
  return ['#fee2e2', '#991b1b'];
}

// ISO- of "YYYY-MM-DD HH:MM:SS"-string → "13 aug 2026, 07:45" (NL).
function _fmtNlDateTime(s) {
  if (!s) return 'onbekend';
  var m = String(s).replace('T', ' ').match(/^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2})/);
  if (!m) return String(s);
  var maanden = ['jan','feb','mrt','apr','mei','jun','jul','aug','sep','okt','nov','dec'];
  return parseInt(m[3], 10) + ' ' + maanden[parseInt(m[2], 10) - 1] + ' ' + m[1] + ', ' + m[4] + ':' + m[5];
}

function loadIrisBriefing() {
  var el = document.getElementById('iris-panel');
  if (!el) return;
  fetch('/api/iris/briefing').then(function(r){return r.json();}).then(function(d){
    var html = '';
    var grades = d.grades || {};
    var names = Object.keys(grades);
    if (!d.report_date) {
      // Nog geen briefing — toon het live cijferbeeld als voorproefje.
      var projs = ((d.metrics || {}).projects) || [];
      if (projs.length) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">' + projs.map(function(p){
          var c = _irisGradeColor(p.grade);
          return '<span style="padding:3px 8px;border-radius:12px;background:' + c[0] + ';color:' + c[1] + ';font-size:11px;font-weight:600">' + escHtml(p.project) + ' ' + p.grade + '</span>';
        }).join('') + '</div>';
      }
      html += '<div style="color:#64748b">Iris heeft nog geen dagbriefing gedraaid — klik <strong>Analyseer nu</strong> of wacht op de 06:45-run.</div>';
      el.innerHTML = html;
      return;
    }
    // Wanneer liep de laatste briefing? (created_at bevat datum + tijd)
    html += '<div style="font-size:10px;color:#94a3b8;margin-bottom:8px">Laatste briefing: ' + _fmtNlDateTime(d.created_at) + '</div>';
    // Trefkans-badge: Iris' eigen bewezen track record (de gesloten leer-lus).
    var tr = d.track_record || {};
    if (tr.accuracy != null || tr.open) {
      var accColor = tr.accuracy == null ? ['#e2e8f0','#475569']
        : (tr.accuracy >= 60 ? ['#dcfce7','#166534'] : (tr.accuracy >= 40 ? ['#fef9c3','#854d0e'] : ['#fee2e2','#991b1b']));
      html += '<div style="margin-bottom:8px;font-size:11px;color:#475569">Mijn trefkans: ' +
        (tr.accuracy != null
          ? '<span style="padding:2px 8px;border-radius:10px;background:' + accColor[0] + ';color:' + accColor[1] + ';font-weight:700">' + tr.accuracy + '%</span> (' + (tr.correct||0) + ' raak · ' + (tr.wrong||0) + ' mis)'
          : '<span style="color:#94a3b8">nog geen afgerekende voorspellingen</span>') +
        (tr.open ? ' · <span style="color:#7c3aed;font-weight:600">' + tr.open + ' voorspelling(en) open</span>' : '') + '</div>';
    }
    // Cijfer-chips per project (laagste eerst — daar zit het werk).
    names.sort(function(a,b){ return (grades[a].cijfer||0) - (grades[b].cijfer||0); });
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">' + names.map(function(n){
      var g = grades[n]; var c = _irisGradeColor(g.cijfer || 0);
      return '<span title="' + escHtml(g.oordeel || '') + '" style="padding:3px 8px;border-radius:12px;background:' + c[0] + ';color:' + c[1] + ';font-size:11px;font-weight:600;cursor:default">' + escHtml(n) + ' ' + (g.cijfer != null ? g.cijfer : '?') + '</span>';
    }).join('') + '</div>';
    // ── "Wil je dat ik dit fix?" — Iris' kant-en-klare actie-knoppen ──
    fetch('/api/iris/suggestions?report_date=' + encodeURIComponent(d.report_date||''))
      .then(function(r){return r.ok ? r.json() : {suggestions:[]};})
      .then(function(sd){
        var sugs = sd.suggestions || [];
        var block = _irisSuggestionBlock(sugs);
        var holder = document.getElementById('iris-suggestions');
        if (holder) holder.innerHTML = block;
      })
      .catch(function(){ var h=document.getElementById('iris-suggestions'); if(h) h.innerHTML=''; });
    html += '<div id="iris-suggestions"></div>';
    // ── Zelfherstel: wat heeft Iris zélf opgelost (en wat lukte niet)? ──
    // Zonder dit blijft haar nuttigste werk onzichtbaar: fouten die nooit in
    // het Actiecentrum belandden omdat ze al opgelost waren.
    html += '<div id="iris-selfheal"></div>';
    fetch('/api/iris/selfheal?limit=50')
      .then(function(r){ return r.ok ? r.json() : {items:[]}; })
      .then(function(hd){
        var items = hd.items || [];
        var holder = document.getElementById('iris-selfheal');
        if (!holder || !items.length) return;
        var healed = items.filter(function(i){ return i.result === 'healed'; });
        var open = items.filter(function(i){ return i.result === 'escalated'; });
        var last = healed[0];
        holder.innerHTML = '<div style="margin:6px 0 8px;font-size:11px;color:#475569">' +
          'Zelf opgelost: <strong style="color:#166534">' + healed.length + '</strong>' +
          (open.length ? ' · <span style="color:#b45309">' + open.length + ' moest ik melden</span>' : '') +
          (last ? '<div style="color:#94a3b8;font-size:10px;margin-top:2px">laatst: ' +
            escHtml(last.action || '') + ' — ' + escHtml((last.note || '').slice(0, 90)) + '</div>' : '') +
          '</div>';
      })
      .catch(function(){});
    var advice = d.advice || [];
    if (advice.length) {
      html += '<div style="margin-bottom:8px">' + advice.slice(0,3).map(function(a){
        return '<div style="padding:4px 0;border-bottom:1px solid #f1f5f9"><strong style="color:#334155">' + (a.prio || '•') + '. ' + escHtml(a.actie || '') + '</strong> <span style="color:#64748b">— ' + escHtml(a.waarom || '') + '</span></div>';
      }).join('') + '</div>';
    }
    html += '<details><summary style="cursor:pointer;font-size:11px;font-weight:600;color:#7c3aed">Volledige briefing (' + escHtml(d.report_date) + ') — geleerd · verbeterd · advies</summary>' +
      '<div class="strategist-analyse-content" style="margin-top:8px">' + mdToHtml(d.markdown || '') + '</div></details>';
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div style="color:#ef4444">Iris-briefing laden mislukt: ' + escHtml(e.message) + '</div>';
  });
}

// ── Iris kennisbank — Vincent voedt Iris met onderzoek ─────────────
function loadIrisKnowledge() {
  var el = document.getElementById('iris-knowledge-panel');
  if (!el) return;
  fetch('/api/iris/knowledge').then(function(r){return r.json();}).then(function(d){
    var items = d.items || [];
    var html = '';
    if (d.folder) {
      html += '<div style="font-size:11px;color:#64748b;margin-bottom:8px">Drop markdown-onderzoek in: <code style="background:#f1f5f9;padding:1px 4px;border-radius:3px">' + escHtml(d.folder) + '</code> — of plak hieronder direct.</div>';
    } else {
      html += '<div style="font-size:11px;color:#b45309;margin-bottom:8px">Geen Obsidian-vault ingesteld — je kunt kennis wel hieronder plakken.</div>';
    }
    // Plakveld voor directe kennis
    html += '<div style="margin-bottom:10px">' +
      '<input id="iris-know-title" placeholder="Titel (bv. GEO-onderzoek jan 2026)" style="width:100%;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;margin-bottom:4px">' +
      '<textarea id="iris-know-text" placeholder="Plak hier je onderzoek/notities die Iris moet leren en toepassen..." style="width:100%;min-height:70px;padding:6px 8px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px;resize:vertical"></textarea>' +
      '<div style="display:flex;gap:6px;margin-top:4px">' +
      '<button id="iris-know-add" onclick="addIrisKnowledge()" style="padding:5px 12px;background:#7c3aed;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">Leer dit</button>' +
      '<button onclick="syncIrisKnowledge(this)" style="padding:5px 12px;background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer">Ververs uit vault</button>' +
      '</div></div>';
    if (items.length) {
      html += '<div style="font-size:11px;color:#334155;font-weight:600;margin-bottom:4px">' + items.length + ' kennisitem(s) actief</div>';
      html += items.map(function(it){
        var tags = (it.tags||[]).map(function(t){return '<span style="background:#ede9fe;color:#6d28d9;padding:1px 5px;border-radius:8px;font-size:9px;margin-left:4px">' + escHtml(t) + '</span>';}).join('');
        var scope = (it.scope && it.scope !== 'all') ? ' <span style="color:#94a3b8">[' + escHtml(it.scope) + ']</span>' : '';
        var princ = (it.principles||[]).slice(0,4).map(function(p){return '<li style="color:#475569">' + escHtml(p) + '</li>';}).join('');
        return '<div style="border:1px solid #f1f5f9;border-radius:6px;padding:6px 8px;margin-bottom:5px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center"><strong style="font-size:11px;color:#334155">' + escHtml(it.title) + scope + tags + '</strong>' +
          '<button onclick="deleteIrisKnowledge(\'' + it.id + '\')" title="Verwijderen" style="background:none;border:none;color:#cbd5e1;cursor:pointer;font-size:13px">×</button></div>' +
          (it.summary ? '<div style="font-size:10px;color:#64748b;margin:2px 0">' + escHtml(it.summary) + '</div>' : '') +
          (princ ? '<ul style="margin:2px 0;padding-left:16px;font-size:10px;line-height:1.5">' + princ + '</ul>' : '') +
          '</div>';
      }).join('');
    } else {
      html += '<div style="font-size:11px;color:#94a3b8">Nog geen kennis. Plak je GEO-onderzoek hierboven of zet een .md in de vault-map.</div>';
    }
    el.innerHTML = html;
  }).catch(function(e){ el.innerHTML = '<div style="color:#ef4444">Kennisbank laden mislukt: ' + escHtml(e.message) + '</div>'; });
}

function addIrisKnowledge() {
  var title = (document.getElementById('iris-know-title')||{}).value || '';
  var text = (document.getElementById('iris-know-text')||{}).value || '';
  if (text.trim().length < 20) { alert('Plak wat meer tekst zodat Iris er iets van kan leren.'); return; }
  var btn = document.getElementById('iris-know-add');
  if (btn) { btn.disabled = true; btn.textContent = 'Iris leest...'; }
  fetch('/api/iris/knowledge', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ title: title, text: text }) })
    .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function(){ loadIrisKnowledge(); })
    .catch(function(e){ if(btn){btn.disabled=false;btn.textContent='Leer dit';} console.error('[Iris kennis]', e); });
}

function syncIrisKnowledge(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/iris/knowledge/sync', { method: 'POST' })
    .then(function(r){ return r.json(); })
    .then(function(res){ loadIrisKnowledge(); })
    .catch(function(e){ if(btn){btn.disabled=false;btn.textContent='Ververs uit vault';} console.error('[Iris sync]', e); });
}

function deleteIrisKnowledge(id) {
  if (!confirm('Dit kennisitem verwijderen?')) return;
  fetch('/api/iris/knowledge/' + id, { method: 'DELETE' })
    .then(function(){ loadIrisKnowledge(); })
    .catch(function(e){ console.error('[Iris kennis del]', e); });
}

function runIrisNow() {
  var btn = document.getElementById('iris-run-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Iris analyseert...'; }
  fetch('/api/iris/run-now', { method: 'POST' }).then(function(r){
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'Analyseer nu'; }
    loadIrisBriefing();
  }).catch(function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Mislukt — opnieuw'; }
    console.error('[Iris]', e);
  });
}

// ── Iris actie-voorstellen: "Wil je dat ik dit fix?" ──────────────
// Elke kaart = één kant-en-klare fix met een agent erachter.
// Vincent keurt per stuk: goedkeuren (apply) of wijzen (reject).
function _irisSugStatusLabel(s) {
  return ({pending:'Wacht op jou', approved:'Goedgekeurd',
           rejected:'Afgewezen', applied:'Uitgevoerd',
           failed:'Mislukt'})[s] || s;
}
// Open (pending/approved/failed) krijgen de volle kaart — dat vraagt een klik
// van Vincent. 'applied'/'rejected' zijn geschiedenis: die kregen tot 12 aug
// 2026 exact dezelfde grote groene kaart als een openstaand item en drukten
// zo de rest van de Control Room omlaag voor niets. Geschiedenis hoort
// samengevat en ingeklapt, niet even zwaar als een besluit dat nog moet.
function _irisSuggestionBlock(sugs) {
  if (!sugs || !sugs.length) return '';
  var relevant = sugs.filter(function(s){ return !(s.type === 'goal_draft' && s.goal_id); });
  var open = [], history = [];
  relevant.forEach(function(s){
    var st = s.status || 'pending';
    (st === 'applied' || st === 'rejected' ? history : open).push(s);
  });

  var openCards = open.map(function(s){
    var st = s.status || 'pending';
    var approved = (st === 'approved');
    var btnHtml = '';
    if (st === 'pending') {
      btnHtml =
        '<button onclick="irisActie(\'approve\',\'' + s.id + '\',this)" class="btn btn-sm btn-primary" style="margin-right:5px">Ja, fix dit</button>' +
        '<button onclick="irisActie(\'reject\',\'' + s.id + '\',this)" class="btn btn-sm btn-ghost">Nee, wijs af</button>';
    } else if (approved) {
      btnHtml = '<button onclick="irisActie(\'apply\',\'' + s.id + '\',this)" class="btn btn-sm btn-primary" style="background:var(--green)">Voer uit</button>';
    } else if (st === 'failed') {
      // Goedkeuring was er al — de uitvoering strandde. Herkansen mag.
      btnHtml = '<button onclick="irisActie(\'apply\',\'' + s.id + '\',this)" class="btn btn-sm btn-primary" style="background:var(--amber)">Probeer opnieuw</button>';
    }
    var detailHtml = (s.detail ? '<div style="font-size:11px;color:#64748b;margin:4px 0 6px">' + escHtml(s.detail) + '</div>' : '');
    var resultHtml = (st === 'failed' && s.applied_detail
      ? '<div style="font-size:11px;color:var(--warn-fg);margin-top:4px">' + escHtml(s.applied_detail) + '</div>' : '');
    var border = (st === 'failed') ? 'var(--warn-border)' : (approved ? '#ddd6fe' : '#f1f5f9');
    return '<div data-sug-id="' + s.id + '" style="border:1px solid ' + border + ';border-radius:8px;padding:8px 10px;margin-bottom:6px;background:#fff">' +
      '<div style="display:flex;align-items:center;gap:6px">' +
      '<strong style="font-size:12px;color:var(--text);flex:1">' + escHtml(s.title) + '</strong>' +
      '<span class="pill pill-neutral">' + _irisSugStatusLabel(st) + '</span></div>' +
      detailHtml + btnHtml + resultHtml + '</div>';
  }).join('');

  var historyRows = history.map(function(s){
    var done = s.status === 'applied';
    return '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;color:#64748b">' +
      '<span class="pill ' + (done ? 'pill-ok' : 'pill-neutral') + '" style="flex-shrink:0">' + _irisSugStatusLabel(s.status) + '</span>' +
      '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(s.title) + '</span></div>';
  }).join('');
  var historyBlock = history.length
    ? '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11px;color:#94a3b8">' +
      history.length + ' eerder afgehandeld</summary><div style="margin-top:4px">' + historyRows + '</div></details>'
    : '';

  if (!openCards && !historyBlock) return '';
  return '<div style="margin:10px 0 4px;border-top:1px solid #f1f5f9;padding-top:10px">' +
    (openCards ? '<div style="font-size:12px;font-weight:700;color:var(--text);margin-bottom:6px">Wil je dat ik dit fix? <span style="font-weight:400;color:#94a3b8">(klik om de juiste agent aan het werk te zetten)</span></div>' + openCards : '') +
    historyBlock + '</div>';
}

function irisActie(action, sid, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  var base = '/api/iris/suggestions/' + encodeURIComponent(sid) + '/';
  var check = function(r){
    if (r.ok) return r.json();
    // Toon de échte reden (FastAPI 'detail') i.p.v. een kaal 'HTTP 400'.
    return r.json().catch(function(){ return {}; }).then(function(body){
      throw new Error((body && body.detail) ? body.detail : ('HTTP ' + r.status));
    });
  };
  var p = fetch(base + action, { method: 'POST' }).then(check);
  if (action === 'approve') {
    // Eén klik = goedkeuren + direct uitvoeren: dat is de menselijke gate,
    // een tweede knop erbovenop voegt niets toe (alles landt tóch in de
    // Wachtrij/het Actiecentrum, nooit direct live).
    p = p.then(function(){
      if (btn) btn.textContent = 'Iris werkt eraan...';
      return fetch(base + 'apply', { method: 'POST' }).then(check);
    });
  }
  p.then(function(){
    loadIrisBriefing();
  }).catch(function(e){
    console.error('[Iris actie]', e);
    if (btn) { btn.disabled = false; btn.textContent = 'Opnieuw'; }
    alert('Actie mislukt: ' + (e.message || e));
  });
}

function acBulkDrafts(btn, mode) {
  var drafts = _acLastItems.filter(function(i){ return i.kind === 'goal_draft'; });
  if (!drafts.length) return;
  var msg = mode === 'start'
    ? 'Alle ' + drafts.length + ' wachtende doelen bevestigen en starten? (Publiceren blijft achter de Wachtrij-gate.)'
    : 'Alle ' + drafts.length + ' draft-doelen definitief verwijderen?';
  if (!confirm(msg)) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig... 0/' + drafts.length; }
  var done = 0;
  var next = function(i) {
    if (i >= drafts.length) { loadActionCenter(); loadActivityLogs(); return; }
    var id = drafts[i].id;
    var p;
    if (mode === 'start') {
      p = fetch('/api/goals/confirm', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id:id}) })
        .then(function(){ return fetch('/api/goals/start', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({goal_id:id}) }); });
    } else {
      p = fetch('/api/goals/' + encodeURIComponent(id), { method:'DELETE' });
    }
    p.catch(function(e){ console.error('[Actiecentrum bulk]', id, e); }).finally(function(){
      done++;
      if (btn) btn.textContent = 'Bezig... ' + done + '/' + drafts.length;
      next(i + 1);
    });
  };
  next(0);
}

// GSC-expert: alle wachtende Search Console-meldingen in één keer door de
// agent laten analyseren én veilig afhandelen (verzenden naar echte mensen bij
// hoge confidence; notificaties worden opgelost zonder naar Google te mailen).
function acGscFixAll(btn) {
  if (!confirm('De GSC-expert analyseert ALLE wachtende Search Console-meldingen en handelt ze veilig af. Doorgaan?')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'GSC-agent bezig…'; }
  post('/api/mail/gsc-fix-all', { auto: true })
    .then(function(d){
      // Vuur-en-vergeet: de agent loopt op de achtergrond. Direct terug met
      // job_id; het Actiecentrum toont de resultaten zodra ze landen.
      var msg = (d && d.message) ? d.message
        : 'GSC-expert verwerkt de meldingen op de achtergrond.';
      alert(msg);
      loadActionCenter(); loadActivityLogs();
    })
    .catch(function(e){
      if (btn) { btn.disabled = false; btn.textContent = 'Verwerk alle GSC'; }
      alert('GSC bulk mislukt: ' + e.message);
    });
}

// Alle foutkaarten in één keer analyseren & afhandelen (ipv ze stuk voor stuk
// aan te klikken). Vuur-en-vergeet: de backend loopt op de achtergrond en geeft
// meteen een job_id terug; het Actiecentrum toont de resultaten zodra ze landen.
function acTriageAll(btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Iris analyseert alle fouten…'; }
  post('/api/iris/errors/triage-all', {})
    .then(function(d){
      var msg = (d && d.message) ? d.message
        : 'Alle foutkaarten worden op de achtergrond geanalyseerd.';
      alert(msg);
      // Ververs na een paar seconden zodat de diagnoses/resultaten zichtbaar zijn.
      setTimeout(function(){ loadActionCenter(); loadActivityLogs(); }, 4000);
    })
    .catch(function(e){
      if (btn) { btn.disabled = false; btn.textContent = 'Analyseer alle fouten'; }
      alert('Bulk-analyse mislukt: ' + e.message);
    });
}

// Start de Microsoft device-code login voor een "Niet geauthenticeerd bij
// Microsoft"-fout. Toont de code + link; na invoeren herstelt de Bridge-mail-
// sync zichzelf. Dit is de echte fix, niet alleen "check je credentials".
function acReconnectMicrosoft(btn, project) {
  if (btn) { btn.disabled = true; btn.textContent = 'Microsoft-login starten…'; }
  post('/api/iris/errors/reconnect-microsoft', {})
    .then(function(d){
      if (btn) { btn.disabled = false; btn.textContent = 'Verbind Microsoft opnieuw'; }
      if (!d || !d.ok) { alert('Kon de Microsoft-login niet starten.'); return; }
      var link = d.verification_uri || 'https://login.microsoft.com/device';
      var code = d.user_code || '(zie de site)';
      var w = window.open(link, '_blank');
      alert('Microsoft-login gestart.\n\n1. Open deze pagina: ' + link + (w ? ' (nieuw tabblad geopend)' : '') +
            '\n2. Voer deze code in: ' + code +
            '\n\nNa het inloggen herstelt de Bridge-mail-sync zichzelf.');
      loadActionCenter(); loadActivityLogs();
    })
    .catch(function(e){
      if (btn) { btn.disabled = false; btn.textContent = 'Verbind Microsoft opnieuw'; }
      alert('Microsoft-login mislukt: ' + e.message);
    });
}

// omdat conflict_checked != 'ok') IN de Actiecentrum-kaart, zodat de gebruiker
// leest wát er aan de hand is — niet alleen een 502 in de console.
function showCalendarInstruction(btn, msg) {
  if (!btn) return;
  var card = btn.closest('[id^="ac-item-"]');
  if (!card) return;
  var existing = card.querySelector('.ac-instruction');
  if (existing) existing.remove();
  var box = document.createElement('div');
  box.className = 'ac-instruction';
  box.style.cssText = 'margin-top:8px;padding:8px 10px;background:var(--warn-bg);border:1px solid var(--warn-border);' +
    'border-radius:8px;font-size:12px;color:var(--warn-fg);line-height:1.45';
  box.textContent = msg || '';
  // zet het blokje ónder de knoppenrij (de eerste .actions-div, of direct in de card-body)
  var body = card.querySelector('div[style*="flex:1"]') || card;
  body.appendChild(box);
}

function acAction(btn, action, project) {
  var type = action.type;
  if (type === 'open_tab') {
    // Vertrouw de projectnaam die met de kaart meekomt (komt rechtstreeks uit
    // de site-rij in de backend) — PROJECTS is een hardcoded lijst die
    // meerdere keren niet meer overeenkwam met de echte sitenaam ('Ictusgo'
    // vs 'IctusGo', 'Steentjebij Steentje' vs 'Steentjeapp', 'Bewaard voor
    // Jou' vs 'BewaardVoorJou'), waardoor "Bekijk in Wachtrij" stil naar de
    // Wachtrij van een ánder project sprong.
    var proj = project || currentProject || 'WeAreImpact';
    currentProject = proj; currentTab = action.tab; weSuggestions = [];
    history.pushState(null, '', '#project=' + encodeURIComponent(proj));
    route();
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  var done = function(){ loadActionCenter(); loadActivityLogs(); };
  var fail = function(e){
    if (btn) { btn.disabled = false; btn.textContent = 'Mislukt — opnieuw'; }
    // Toon de foutmelding ook inline (niet alleen console): post() stopt de
    // FastAPI-`detail` in e.message, dus de gebruiker leest wát er stuk is in
    // plaats van blind "Mislukt — opnieuw" te zien en te blijven klikken.
    if (btn && e && e.message) {
      var card = btn.closest('[id^="ac-item-"]');
      if (card) {
        var body = card.querySelector('div[style*="flex:1"]') || card;
        var box = body.querySelector('.ac-inline-error');
        if (!box) { box = document.createElement('div'); box.className = 'ac-inline-error';
          box.style.cssText = 'margin-top:8px;padding:6px 10px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;font-size:12px;color:#991b1b;line-height:1.4';
          body.appendChild(box); }
        box.textContent = e.message;
      }
    }
    console.error('[Actiecentrum]', e);
  };

  if (type === 'goal_confirm_start') {
    post('/api/goals/confirm', { goal_id: action.id })
      .then(function(){ return post('/api/goals/start', { goal_id: action.id }); })
      .then(done).catch(fail);
  } else if (type === 'goal_start') {
    post('/api/goals/start', { goal_id: action.id }).then(done).catch(fail);
  } else if (type === 'goal_retry') {
    post('/api/goals/retry-failed', { goal_id: action.id }).then(done).catch(fail);
  } else if (type === 'goal_delete') {
    if (!confirm('Doel definitief verwijderen?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    fetch('/api/goals/' + encodeURIComponent(action.id), { method:'DELETE' }).then(done).catch(fail);
  } else if (type === 'content_approve') {
    // Social is opt-in: alleen-website is de standaardkeuze. Social gebeurt
    // nooit vanzelf — voor géén enkel project.
    showChoiceModal({
      title: 'Publiceren',
      body: 'Standaard gaat alleen de website live. Social alleen als je dat hier kiest.',
      buttons: [
        { label: 'Alleen website publiceren', value: 'website_only', primary: true },
        { label: 'Ook naar social posten', value: 'with_social' },
        { label: 'Annuleren', value: 'cancel' },
      ],
    }).then(function (choice) {
      if (!choice || choice === 'cancel') {
        if (btn) { btn.disabled = false; btn.textContent = action.label; }
        return;
      }
      if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
      // Zonder expliciete keuze post het backend niets naar social.
      var body = choice === 'with_social' ? { channels: ALL_SOCIAL_CHANNELS } : { social: false };
      post('/api/content-queue/' + encodeURIComponent(action.id) + '/approve', body)
        .then(done).catch(fail);
    });
  } else if (type === 'content_reject') {
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'content_ready_linkedin') {
    // LinkedIn-outreach: NOOIT naar de site publiceren. Markeer als klaar voor
    // LinkedIn (mens plakt de berichten per doelgroep zelf op LinkedIn).
    if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/ready-linkedin', {})
      .then(done).catch(fail);
  } else if (type === 'content_regenerate') {
    if (btn) btn.textContent = 'Agent herschrijft... (kan even duren)';
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/regenerate').then(done).catch(fail);
  } else if (type === 'content_manual_edit') {
    acManualEdit(btn, action); return;
  } else if (type === 'billing_reminder_send') {
    if (!confirm('Deze herinnering wordt ECHT verstuurd naar de klant. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/billing/reminders/' + encodeURIComponent(action.id) + '/send').then(done).catch(fail);
  } else if (type === 'billing_reminder_skip') {
    post('/api/billing/reminders/' + encodeURIComponent(action.id) + '/skip').then(done).catch(fail);
  } else if (type === 'followup_send') {
    if (!confirm('Deze opvolgmail wordt ECHT verstuurd naar de lead. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/leads/' + encodeURIComponent(action.id) + '/followup-approve').then(done).catch(fail);
  } else if (type === 'followup_skip') {
    post('/api/leads/' + encodeURIComponent(action.id) + '/followup-dismiss').then(done).catch(fail);
  } else if (type === 'outreach_send') {
    if (!confirm('Deze outreach-mail wordt ECHT verstuurd naar de lead. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/leads/' + encodeURIComponent(action.id) + '/outreach-approve').then(done).catch(fail);
  } else if (type === 'outreach_dismiss') {
    post('/api/leads/' + encodeURIComponent(action.id) + '/outreach-dismiss').then(done).catch(fail);
  } else if (type === 'linkbuilding_send') {
    if (!confirm('Deze link-outreach-mail wordt ECHT verstuurd. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/linkbuilding/' + encodeURIComponent(action.id) + '/outreach-approve').then(done).catch(fail);
  } else if (type === 'linkbuilding_dismiss') {
    post('/api/linkbuilding/' + encodeURIComponent(action.id) + '/outreach-dismiss').then(done).catch(fail);
  } else if (type === 'task_approve') {
    fetch('/api/tasks/' + encodeURIComponent(action.id) + '/status', { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status:'done'}) })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(done).catch(fail);
  } else if (type === 'dismiss') {
    post('/api/action-center/dismiss', { kind: action.dismiss_kind, ref_id: String(action.id) }).then(done).catch(fail);
  } else if (type === 'mail_send') {
    if (!confirm('Deze mail wordt ECHT verstuurd naar de klant. Doorgaan?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/send').then(done).catch(fail);
  } else if (type === 'mail_reject') {
    if (!confirm('Concept afwijzen? Wordt niet verstuurd.')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'mail_ignore_sender') {
    if (!confirm('Niet meer reageren op deze afzender? Alle openstaande concepten van deze afzender worden afgewezen en toekomstige mails krijgen nooit meer een concept-antwoord.')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/ignore-sender').then(done).catch(fail);
  } else if (type === 'mail_gsc_fix') {
    if (btn) { btn.disabled = true; btn.textContent = 'GSC-agent analyseert…'; }
    post('/api/mail/reply/' + encodeURIComponent(action.id) + '/gsc-fix', { auto: true })
      .then(function(d){
        if (d && d.analysis) {
          var disp = d.disposition;
          var dispLabel = disp === 'sent' ? 'Verzonden naar klant'
                        : disp === 'resolved' ? 'Geanalyseerd (geen antwoord mogelijk — Google no-reply)'
                        : 'Klaar ter review — check & verstuur zelf';
          if (btn) {
            var card = btn.closest('[id^="ac-item-"]');
            if (card) {
              var body = card.querySelector('div[style*="flex:1"]') || card;
              var box = document.createElement('div');
              box.className = 'ac-gsc-analysis';
              box.style.cssText = 'margin-top:8px;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:8px;font-size:12px;color:#e2e8f0;line-height:1.5;white-space:pre-wrap';
              box.textContent =
                (d.used_live_gsc ? 'Live GSC-data gebruikt' : 'Geen live GSC-data beschikbaar') +
                ' · Vertrouwen: ' + (Math.round((d.confidence||0)*100)) + '%\n' +
                dispLabel + '\n\n' + (d.analysis || '');
              body.appendChild(box);
              btn.textContent = 'Gereed';
            }
            setTimeout(function(){ loadActionCenter(); loadActivityLogs(); }, 6000);
          }
        } else {
          done();
        }
      }).catch(fail);
  } else if (type === 'error_triage') {
    if (btn) { btn.disabled = true; btn.textContent = 'Iris analyseert...'; }
    var qs = action.error_kind ? ('?kind=' + encodeURIComponent(action.error_kind)) : '';
    post('/api/iris/errors/' + encodeURIComponent(action.id) + '/triage' + qs, {})
      .then(function(d){
        if (btn) {
          var card = btn.closest('[id^="ac-item-"]');
          if (card) {
            var body = card.querySelector('div[style*="flex:1"]') || card;
            var box = document.createElement('div');
            box.className = 'ac-triage-result';
            var okFix = d && d.ok && d.remedy_type && d.remedy_type !== 'human_step';
            box.style.cssText = 'margin-top:8px;padding:10px;border-radius:8px;font-size:12px;line-height:1.5;' +
              (okFix ? 'background:#f0fdf4;border:1px solid #86efac;color:#166534'
                     : (d && d.remedy_type === 'human_step' ? 'background:#fff7ed;border:1px solid #fdba74;color:#9a3412'
                                                            : 'background:#fef2f2;border:1px solid #fecaca;color:#991b1b'));
            var lines = [];
            if (d && d.diagnosis) lines.push('<strong>Diagnose:</strong> ' + escHtml(d.diagnosis));
            if (okFix) lines.push(escHtml(d.result || 'Fix uitgevoerd.'));
            else if (d && d.remedy_type === 'human_step') lines.push('Vereist een menselijke stap: ' + escHtml(d.human_step || ''));
            else lines.push(escHtml((d && (d.error || d.result)) || 'Fix mislukt.'));
            box.innerHTML = lines.join('<br>');
            body.appendChild(box);
          }
          btn.textContent = 'Geanalyseerd';
        }
        // Niet meteen herladen: dat vervangt de kaart (met het net getoonde
        // resultaat) vrijwel synchroon weer door de ongewijzigde lijst — de
        // analyse lijkt dan niets te doen. Geef Vincent de tijd om te lezen.
        setTimeout(function(){ loadActionCenter(); loadActivityLogs(); }, 6000);
      }).catch(fail);
  } else if (type === 'calendar_approve') {
    if (!confirm('Afspraak in Google Agenda planen? (incl. reistijd/conflict-check)')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/calendar/proposals/approve', JSON.stringify({ proposal_id: action.id }), 'application/json')
      .then(function(d){
        if (d && d.ok) { done(d); return; }
        // Bewust geblokkeerd (conflict_checked != 'ok') of geweigerd: toon de
        // deel-instructie IN de kaart, gooi hem niet naar de console. De knop
        // wordt een neutraal "Begrijp ik" i.p.v. "Mislukt — opnieuw" — het is
        // geen systeemfout, dus opnieuw klikken lost niets op en verwart.
        if (btn) { btn.textContent = 'Begrijp ik'; btn.disabled = false; btn.onclick = function(){ var c = btn.closest('[id^="ac-item-"]'); if (c) { var b = c.querySelector('.ac-instruction'); if (b) b.remove(); } }; }
        showCalendarInstruction(btn, (d && d.error) || 'Goedkeuren geweigerd door de agenda-agent.');
      })
      .catch(fail);
  } else if (type === 'calendar_reject') {
    if (!confirm('Afspraak-voorstel weigeren?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/calendar/proposals/reject', JSON.stringify({ proposal_id: action.id }), 'application/json')
      .then(done).catch(fail);
  } else if (type === 'calendar_detail') {
    // Zoek het volledige item (met detail-velden) terug in de laatst geladen
    // lijst en open een modal. Zo hoeft de backend geen aparte endpoint voor
    // detail te krijgen — de data zit al in de action-center payload.
    var item = (_acLastItems || []).find(function(i){ return String(i.id) === String(action.id) && i.kind === 'calendar_proposal'; });
    acShowCalendarDetail(item);
    return;
  } else if (type === 'run_job') {
    // Een gemiste geplande taak alsnog draaien. Draait op de achtergrond: een
    // contentronde of outreach-batch duurt minuten. De kaart verdwijnt vanzelf
    // zodra de run slaagt — dat is precies wanneer het gat gedicht is.
    post('/api/scheduler/jobs/' + encodeURIComponent(action.id) + '/run', {})
      .then(function(d){
        if (btn) btn.textContent = 'Gestart — draait op de achtergrond';
        setTimeout(function(){ loadActionCenter(); loadActivityLogs(); }, 8000);
      }).catch(fail);
  } else if (type === 'confirm_depublished') {
    // Haalt een afgekeurd-maar-live artikel écht offline (unpublish bij de
    // site zelf) en sluit de kaart pas bij bevestigd succes.
    if (btn) { btn.disabled = true; btn.textContent = 'Haalt offline...'; }
    post('/api/content-queue/' + encodeURIComponent(action.id) + '/confirm-depublished', {})
      .then(done).catch(fail);
  } else if (type === 'mail_edit') {
    acMailEdit(btn, action); return;
  } else if (type === 'personal_mail_send') {
    post('/api/mail/personal/' + encodeURIComponent(action.id) + '/send').then(done).catch(fail);
  } else if (type === 'personal_mail_reject') {
    if (!confirm('Concept voor persoonlijke mail afwijzen?')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/mail/personal/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'social_send') {
    // Plaatst het goedgekeurde concept-antwoord op het social-kanaal (achter
    // de gate via de social-inbox router). Zelfde handler als de bridge.
    post('/api/social-inbox/msg/' + encodeURIComponent(action.id) + '/approve').then(done).catch(fail);
  } else if (type === 'campagne_publish') {
    // ECHTE plaatsing op de kanalen die Impact OS kan bedienen (de groene
    // "Plaats op socials"-knop). Per kanaal de bestaande publish_pack-route —
    // die doet de daadwerkelijke API-call. Kanalen die niet automatisch kunnen
    // (geen token / geen publieke image / LinkedIn) staan niet in
    // action.channels en krijgen dus geen valse "geplaatst"-melding.
    var chans = (action.channels && action.channels.length) ? action.channels
                : ['facebook'];
    if (btn) { btn.disabled = true; btn.textContent = 'Plaatst op ' + chans.join(', ') + '...'; }
    var results = [];
    var seq = Promise.resolve();
    chans.forEach(function(plat){
      seq = seq.then(function(){
        return post('/api/social-content/packs/' + encodeURIComponent(action.id) + '/publish',
                    JSON.stringify({ platform: plat }), 'application/json')
          .then(function(d){
            if (d && d.success) {
              results.push('✓ ' + plat + (d.url ? ' (' + d.url + ')' : ' geplaatst'));
            } else if (d && d.manual) {
              results.push('• ' + plat + ' handmatig: ' + (d.detail || 'kopieer en plaats zelf'));
            } else {
              results.push('✗ ' + plat + ': ' + ((d && d.error) || 'mislukt'));
            }
          })
          .catch(function(e){ results.push('✗ ' + plat + ': ' + (e && e.message || e)); });
      });
    });
    seq.then(function(){
      if (btn) {
        var card = btn.closest('[id^="ac-item-"]');
        if (card) {
          var body = card.querySelector('div[style]-x') || card; // fallback
          var b = card.querySelector('.ac-inline-result');
          if (!b) { b = document.createElement('div'); b.className = 'ac-inline-result';
            b.style.cssText = 'margin-top:8px;padding:8px 10px;border-radius:8px;font-size:12px;line-height:1.5;white-space:pre-wrap;background:#f0fdf4;border:1px solid #86efac;color:#166534';
            var target = card.querySelector('div[style*="flex:1"]') || card;
            target.appendChild(b);
          }
          b.textContent = results.join('\n');
        }
        btn.textContent = 'Geplaatst op ' + chans.length + ' kanaal(en)';
      }
      setTimeout(function(){ loadActionCenter(); loadActivityLogs(); }, 1500);
    });
  } else if (type === 'campagne_posted') {
    // JIJ hebt de post zelf op de kanalen gezet (de grijze zelf-melding).
    // Vraag wélke: een post die alleen op LinkedIn verscheen is geen post op
    // vier kanalen, en dat verschil is later het enige waarop je kunt
    // terugkijken. Leeg laten = alle kanalen waarvoor tekst klaarstond.
    // Deze knop plaatst NIETS — hij noteert alleen dat jij het deed.
    var kanalen = prompt('Op welke kanalen heb je de post ZELF geplaatst? (komma-gescheiden; leeg = alle)',
                         'linkedin, facebook, instagram, twitter');
    if (kanalen === null) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/social-content/packs/' + encodeURIComponent(action.id) + '/posted',
         JSON.stringify({ platforms: kanalen }), 'application/json').then(done).catch(fail);
  } else if (type === 'campagne_skip') {
    if (!confirm('Deze campagnepost overslaan? Hij wordt afgewezen en komt niet terug.')) {
      if (btn) { btn.disabled = false; btn.textContent = action.label; } return;
    }
    post('/api/social-content/packs/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else if (type === 'social_reject') {
    if (!confirm('Concept social-antwoord afwijzen? Wordt niet geplaatst.')) { if (btn) { btn.disabled = false; btn.textContent = action.label; } return; }
    post('/api/social-inbox/msg/' + encodeURIComponent(action.id) + '/reject').then(done).catch(fail);
  } else {
    fail(new Error('Onbekende actie: ' + type));
  }
}

// ── Detail-paneel voor een afspraak-voorstel (calendar_proposal) ──────
// Toont alle geparseerde velden én de lead-status: is dit een bekende klant
// of een nieuwe lead? Gebouwd op de showChoiceModal-stijl overlay.
function acShowCalendarDetail(item) {
  if (!item) { alert('Detail niet beschikbaar — ververs de pagina.'); return; }
  var d = item.detail || {};
  var lead = d.lead_status || { known: false, label: 'Onbekend', where: [], tier: 'new' };
  var badge = lead.known
    ? '<span style="background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700">✓ Bekende klant/lead</span>'
    : (lead.tier === 'warm'
        ? '<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700">● Warm contact — nog geen lead</span>'
        : '<span style="background:#ffedd5;color:#9a3412;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700">★ Nieuwe lead</span>');
  var whereHtml = (lead.where && lead.where.length)
    ? '<ul style="margin:4px 0 0 16px;padding:0;color:#475569">' +
        lead.where.map(function(w){ return '<li style="margin:2px 0">' + escHtml(w) + '</li>'; }).join('') + '</ul>'
    : '<span style="color:#94a3b8">Nog nergens anders in ons systeem gevonden.</span>';

  var rows = [
    ['Afzender', d.from_addr || '—'],
    ['Onderwerp', d.subject || '—'],
    ['Voorgesteld', (d.proposed_start ? d.proposed_start.replace('T', ' ') : '—') + '  →  ' + (d.proposed_end ? d.proposed_end.slice(11,16) : '')],
    ['Locatie', (d.is_remote ? '🌐 ' : '') + (d.location || '—')],
    ['Duur', (d.duration_min ? d.duration_min + ' min' : '—') + (d.travel_buffer_min ? '  (+ ' + d.travel_buffer_min + ' min reistijd)' : '')],
    ['Prioriteit', d.priority || '—'],
  ];
  var rowsHtml = rows.map(function(r){
    return '<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #f1f5f9">' +
      '<div style="width:110px;flex-shrink:0;color:#94a3b8;font-size:12px">' + escHtml(r[0]) + '</div>' +
      '<div style="flex:1;font-size:13px;color:#1e293b;word-break:break-word">' + escHtml(r[1]) + '</div></div>';
  }).join('');

  var rationaleHtml = d.rationale
    ? '<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;border-left:3px solid #cbd5e1;border-radius:6px;font-size:12px;color:#475569;line-height:1.5">' + escHtml(d.rationale) + '</div>'
    : '';

  // ── Lead-CRM-blok (alleen als er een lead is) ──
  var crm = d.lead || null;
  var crmHtml = '';
  if (crm) {
    var tags = (crm.tags && crm.tags.length) ? crm.tags.map(function(t){ return '<span style="background:#eef2ff;color:#4338ca;padding:1px 7px;border-radius:999px;font-size:10px;font-weight:600">#' + escHtml(t) + '</span>'; }).join(' ') : '';
    var scoreColor = (crm.score >= 75) ? '#065f46' : (crm.score >= 50 ? '#92400e' : '#9a3412');
    crmHtml =
      '<div style="margin-top:14px;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px">' +
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
          '<strong style="font-size:13px;color:#166534">Lead in CRM</strong>' +
          '<span style="background:#dcfce7;color:#166534;padding:1px 8px;border-radius:999px;font-size:10px;font-weight:700">' + escHtml(crm.status || '—') + '</span>' +
          '<span style="color:' + scoreColor + ';font-weight:700;font-size:12px">score ' + (crm.score != null ? crm.score : '—') + '</span>' +
        '</div>' +
        (crm.phone ? '<div style="font-size:12px;color:#475569;margin-bottom:4px">TEL ' + escHtml(crm.phone) + '</div>' : '') +
        (crm.summary ? '<div style="font-size:12px;color:#334155;line-height:1.5;margin-bottom:6px">' + escHtml(crm.summary) + '</div>' : '') +
        (tags ? '<div style="margin-top:4px">' + tags + '</div>' : '') +
        (crm.obsidian_path ? '<div style="margin-top:6px;font-size:11px;color:#64748b">VAULT ' + escHtml(crm.obsidian_path) + '</div>' : '') +
      '</div>';
  }

  // ── Recente mails (de thread) ──
  var mails = d.recent_mails || [];
  var mailsHtml = '';
  if (mails.length) {
    mailsHtml = '<div style="margin-top:14px">' +
      '<div style="font-size:12px;font-weight:700;color:#475569;margin-bottom:6px">Recente mails van deze afzender</div>' +
      mails.map(function(m){
        return '<div style="padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;margin-bottom:6px">' +
          '<div style="display:flex;justify-content:space-between;gap:8px"><span style="font-size:12px;font-weight:600;color:#1e293b">' + escHtml(m.subject || '(geen onderwerp)') + '</span>' +
          '<span style="font-size:10px;color:#94a3b8;flex-shrink:0">' + escHtml(m.date || '') + '</span></div>' +
          '<div style="font-size:11px;color:#64748b;margin-top:2px;line-height:1.4">' + escHtml(m.snippet || '') + '</div>' +
        '</div>';
      }).join('') + '</div>';
  }

  var body =
    '<div style="font-size:13px;font-weight:700;color:#1e293b;margin-bottom:10px">' + escHtml(item.title) + '</div>' +
    rowsHtml +
    '<div style="margin-top:14px;padding:12px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px">' +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
        '<strong style="font-size:13px;color:#92400e">Wie is dit?</strong>' + badge + '</div>' +
      '<div style="font-size:12px;color:#475569">Lead-status in ons systeem:</div>' + whereHtml +
    '</div>' +
    crmHtml + mailsHtml + rationaleHtml;

  // Toon als modal via de bestaande overlay-builder van core.js.
  if (typeof showChoiceModal === 'function') {
    showChoiceModal({
      title: 'Afspraak-voorstel — detail',
      bodyHtml: body,
      buttons: [
        { label: 'Plan in agenda', value: 'approve', primary: true },
        { label: 'Sluiten', value: 'close' },
      ],
    }).then(function(choice){
      if (choice === 'approve') {
        // Trigger dezelfde flow als de "Plan in agenda"-knop op de kaart.
        var act = (item.actions || []).find(function(a){ return a.type === 'calendar_approve'; });
        if (act) acAction(null, act, item.project || 'Agenda');
      }
    });
  } else {
    // Fallback: simpele alert-achtige overlay.
    var ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;z-index:9999;padding:16px';
    ov.innerHTML = '<div style="background:#fff;border-radius:12px;max-width:480px;width:92%;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.25);max-height:85vh;overflow:auto">' +
      '<h3 style="margin:0 0 12px;font-size:15px;color:#1e293b">Afspraak-voorstel — detail</h3>' + body +
      '<div style="margin-top:16px;text-align:right"><button onclick="this.closest(\'[style*=fixed]\').remove()" class="btn btn-primary">Sluiten</button></div></div>';
    ov.onclick = function(e){ if (e.target === ov) ov.remove(); };
    document.body.appendChild(ov);
  }
}


// 'Markeer als bekend': zet de afzender van dit mail-concept in het
// bekende-afzenders-register (backend: known_senders). Daarna toont het
// systeem deze afzender nooit meer als 'Nieuwe afzender'. Na succes fris
// we het Actiecentrum op zodat de vlag én de knop direct verdwijnen.
function acMarkSenderKnown(btn, replyId) {
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/mail/reply/' + encodeURIComponent(replyId) + '/mark-known', { method: 'POST' })
    .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function(){
      // Direct opfrissen: de kaart verliest zijn 'Nieuwe afzender'-vlag en
      // de 'Markeer als bekend'-knop verdwijnt bij de volgende render.
      loadActionCenter();
    })
    .catch(function(e){
      if (btn) { btn.disabled = false; btn.textContent = 'Mislukt — opnieuw'; }
      console.error('[Actiecentrum] markeer bekend faalde', e);
    });
}


// Haal het volledige concept op en vervang de actieknoppen door een
// tekstvak + opslaan/annuleren. Zelfde backend-gate als de helpdesk-tab.
async function acMailEdit(btn, action) {
  var actionsDiv = btn ? btn.parentNode : null;
  var card = actionsDiv ? actionsDiv.closest('[id^="ac-item-"]') : null;
  if (!card || !actionsDiv) { alert('Editor kon niet worden geopend — ververs de pagina.'); return; }
  var inner = actionsDiv.parentNode;
  try {
    var replies = (await (await fetch('/api/mail/pending')).json()).replies || [];
    var r = replies.find(function(x){ return String(x.id) === String(action.id); });
    if (!r) { alert('Concept niet meer gevonden (al verstuurd of afgewezen?).'); return; }
    var ta = document.createElement('textarea');
    ta.value = r.draft_body || '';
    ta.style.cssText = 'width:100%;min-height:120px;font-size:12px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:inherit;background:#fffbeb;margin-top:8px';
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Opslaan';
    saveBtn.style.cssText = 'padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Annuleren';
    cancelBtn.style.cssText = 'padding:4px 12px;background:#f8fafc;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer;margin-top:8px';
    actionsDiv.innerHTML = '';
    inner.appendChild(ta);
    actionsDiv.appendChild(saveBtn);
    actionsDiv.appendChild(cancelBtn);
    cancelBtn.onclick = function(){ loadActionCenter(); };
    saveBtn.onclick = async function(){
      saveBtn.disabled = true; saveBtn.textContent = 'Opslaan...';
      try {
        var resp = await fetch('/api/mail/reply/' + encodeURIComponent(action.id) + '/edit', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ text: ta.value })
        });
        var d = await resp.json();
        if (d.ok) { loadActionCenter(); }
        else { alert('❌ ' + (d.error || 'onbekend')); saveBtn.disabled = false; saveBtn.textContent = 'Opslaan'; }
      } catch(e) { alert('❌ ' + e.message); saveBtn.disabled = false; saveBtn.textContent = 'Opslaan'; }
    };
  } catch(e) { alert('❌ ' + e.message); }
}

// ── Inline editor voor het Actiecentrum (content → Handmatig aanpassen) ──
// Haal de huidige artikel-body op, toon een tekstvak met de ruwe HTML en
// knoppen om de tekst naar Claude/Gemini te kopiëren (zodat je hem daar zelf
// kunt verbeteren) en de bewerkte versie terug te plakken + op te slaan. Bij
// opslaan scort Impact OS opnieuw en zet de job op 'pending_review' als de grens
// gehaald is.
async function acManualEdit(btn, action) {
  var actionsDiv = btn ? btn.parentNode : null;
  var card = actionsDiv ? actionsDiv.closest('[id^="ac-item-"]') : null;
  if (!card || !actionsDiv) { alert('Editor kon niet worden geopend — ververs de pagina.'); return; }
  var inner = actionsDiv.parentNode;
  actionsDiv.innerHTML = '<span style="font-size:11px;color:#64748b">Artikel laden...</span>';
  try {
    var job = await (await fetch('/api/content-queue/' + encodeURIComponent(action.id))).json();
    var html = job.blog_html || '';
    if (!html) { alert('Geen body gevonden voor dit artikel.'); loadActionCenter(); return; }

    var ta = document.createElement('textarea');
    ta.value = html;
    ta.style.cssText = 'width:100%;min-height:200px;font-size:11px;line-height:1.5;padding:8px;border:1px solid #e2e8f0;border-radius:6px;resize:vertical;font-family:monospace;background:#fffbeb;margin-top:8px';

    var copyClaude = document.createElement('button');
    copyClaude.textContent = 'Kopieer naar Claude';
    copyClaude.style.cssText = 'padding:4px 10px;background:#fff;color:#d97757;border:1px solid #fcd9c9;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var copyGemini = document.createElement('button');
    copyGemini.textContent = 'Kopieer naar Gemini';
    copyGemini.style.cssText = 'padding:4px 10px;background:#fff;color:#1a73e8;border:1px solid #c5d9fb;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Opslaan & opnieuw scoren';
    saveBtn.style.cssText = 'padding:4px 12px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var forceBtn = document.createElement('button');
    forceBtn.textContent = 'Toch naar Wachtrij (score overslaan)';
    forceBtn.style.cssText = 'padding:4px 12px;background:#fff;color:#0f766e;border:1px solid #99f6e4;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;margin:8px 6px 0 0';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Annuleren';
    cancelBtn.style.cssText = 'padding:4px 12px;background:#f8fafc;color:#475569;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;cursor:pointer;margin-top:8px';

    var status = document.createElement('div');
    status.style.cssText = 'font-size:11px;color:#64748b;margin-top:6px';

    actionsDiv.innerHTML = '';
    inner.appendChild(ta);
    inner.appendChild(copyClaude);
    inner.appendChild(copyGemini);
    inner.appendChild(saveBtn);
    inner.appendChild(forceBtn);
    inner.appendChild(cancelBtn);
    inner.appendChild(status);

    var promptFor = function(model){
      return 'Verbeter onderstaand artikel zodat het een SEO-score van minimaal 85/100 haalt ' +
        '(wereldklasse E-E-A-T, AEO/FAQ, leesbaar, geen AI-taal). Lever ALLEEN de volledige HTML-body ' +
        'terug zonder <html>/<head>/<body>.\n\n=== ARTIKEL ===\n' + html;
    };
    var copyTo = async function(model, url){
      try {
        await navigator.clipboard.writeText(promptFor(model));
        status.textContent = 'Tekst gekopieerd — plak in ' + model + ' (Ctrl+V / Cmd+V) en verbeter daar.';
        if (url) window.open(url, '_blank');
      } catch(e) { status.textContent = 'Kon niet kopiëren: ' + e.message + ' — selecteer de tekst handmatig.'; }
    };
    copyClaude.onclick = function(){ copyTo('Claude', 'https://claude.ai/new'); };
    copyGemini.onclick = function(){ copyTo('Gemini', 'https://gemini.google.com/app'); };

    forceBtn.onclick = async function(){
      if (!confirm('Zeker? De LLM-score wordt overgeslagen en het artikel gaat naar de Wachtrij om te publiceren. Je kunt dit niet ongedaan maken via deze knop.')) { return; }
      forceBtn.disabled = true; forceBtn.textContent = 'Vrijgeven...';
      status.textContent = 'Bezig met vrijgeven naar de Wachtrij...';
      try {
        var resp = await fetch('/api/content-queue/' + encodeURIComponent(action.id) + '/save-manual-edit', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ html_body: ta.value, force: true })
        });
        var d = await resp.json();
        if (d.success) {
          status.style.color = '#166534';
          status.textContent = 'Vrijgegeven naar de Wachtrij — klaar om te publiceren.';
          setTimeout(loadActionCenter, 1400);
        } else { alert('❌ ' + (d.detail || 'onbekend')); forceBtn.disabled = false; forceBtn.textContent = 'Toch naar Wachtrij (score overslaan)'; }
      } catch(e) { alert('❌ ' + e.message); forceBtn.disabled = false; forceBtn.textContent = 'Toch naar Wachtrij (score overslaan)'; }
    };

    cancelBtn.onclick = function(){ loadActionCenter(); };
    saveBtn.onclick = async function(){
      saveBtn.disabled = true; saveBtn.textContent = 'Opslaan...';
      status.textContent = 'Bezig met opslaan en opnieuw scoren...';
      try {
        var ctrl = new AbortController();
        var to = setTimeout(function(){ ctrl.abort(); }, 50000);
        var resp = await fetch('/api/content-queue/' + encodeURIComponent(action.id) + '/save-manual-edit', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ html_body: ta.value }), signal: ctrl.signal
        });
        clearTimeout(to);
        var d = await resp.json();
        if (d.success) {
          if (!d.scored) {
            status.style.color = '#b45309';
            status.textContent = d.feedback || 'Scoren mislukt — body wel opgeslagen.';
          } else if (d.passed) {
            status.style.color = '#166534';
            status.textContent = 'Score ' + d.score + ' — boven grens, klaar om te publiceren.';
          } else {
            status.style.color = '#b45309';
            status.textContent = 'Score ' + d.score + ' — nog onder grens. ' + (d.feedback || '').slice(0,160);
          }
          setTimeout(loadActionCenter, d.passed ? 1400 : 2600);
        } else { alert('❌ ' + (d.detail || 'onbekend')); saveBtn.disabled = false; saveBtn.textContent = 'Opslaan & opnieuw scoren'; }
      } catch(e) {
        if (e.name === 'AbortError') { alert('⏱ Opslaan duurde te lang (LLM-score hangt waarschijnlijk in quota-backoff). De body is wellicht wel opgeslagen — ververs en controleer.'); }
        else { alert('❌ ' + e.message); }
        saveBtn.disabled = false; saveBtn.textContent = 'Opslaan & opnieuw scoren';
      }
    };
  } catch(e) { alert('❌ ' + e.message); loadActionCenter(); }
}

// ── Systeemgezondheid (herbruikbaar: Control Room + per-project Dashboard) ──
function loadSystemHealth(elId, btnId) {
  elId = elId || 'system-health-panel';
  btnId = btnId || 'autoheal-btn';
  var el = document.getElementById(elId);
  if (!el) return;
  // Op het project-dashboard filteren we op dat project, zodat een fout in een
  // ander project (bv. Bijeen) niet op het Bewaardvoorjou-scherm verschijnt.
  var projectParam = (elId === 'system-health-panel-proj' && currentProject)
    ? '?project=' + encodeURIComponent(currentProject) : '';
  fetch('/api/strategist/health' + projectParam).then(function(r){return r.json();}).then(function(h){
    if (!el || h.error) return;
    if (h.ok) {
      el.innerHTML = '<div style="background:var(--ok-bg);border:1px solid var(--ok-border);border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:var(--ok-fg);display:flex;align-items:center;gap:8px">' +
        'Systeem gezond' +
        (h.last_autoheal && h.last_autoheal.time ? '<span style="color:#15803d;font-size:10px;margin-left:auto">laatste check: ' + h.last_autoheal.time.slice(11,16) + '</span>' : '') +
        '</div>';
      return;
    }
    var html = '<div style="background:var(--danger-bg);border:1px solid var(--danger-border);border-radius:8px;padding:12px 14px;margin-bottom:16px">' +
      '<p style="font-weight:600;font-size:13px;color:var(--danger-fg);margin-bottom:8px">Aandachtspunten</p>';

    (h.stalled_goals||[]).forEach(function(g){
      html += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;margin-bottom:4px;background:#fff;border:1px solid #fecaca;border-radius:6px">' +
        '<span style="font-size:11px;color:#7f1d1d"><b>' + escHtml(g.project||'') + '</b> — ‘' + escHtml(g.title) + '’ draait niet (verweesd na herstart)</span>' +
        '<button onclick="runAutoheal(\'' + elId + '\',\'' + btnId + '\')" id="' + btnId + '" style="padding:4px 10px;background:#dc2626;color:#fff;border:none;border-radius:4px;font-size:10px;font-weight:600;cursor:pointer;flex-shrink:0">Direct oplossen door agent</button>' +
        '</div>';
    });

    (h.failed_goals||[]).forEach(function(g){
      html += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 8px;margin-bottom:4px;background:#fff;border:1px solid #fecaca;border-radius:6px">' +
        '<span style="font-size:11px;color:#7f1d1d"><b>' + escHtml(g.project||'') + '</b> — ‘' + escHtml(g.title) + '’ is mislukt</span>' +
        '<button onclick="retryFailedGoal(\'' + g.goal_id + '\')" style="padding:4px 10px;background:#dc2626;color:#fff;border:none;border-radius:4px;font-size:10px;font-weight:600;cursor:pointer;flex-shrink:0">Direct oplossen door agent</button>' +
        '</div>';
    });

    (h.failed_jobs||[]).forEach(function(j){
      var lr = j.last_run || {};
      var missed = lr.status === 'missed';
      html += '<p style="font-size:11px;color:' + (missed ? '#92400e' : '#991b1b') + ';margin-top:4px">- scheduler-taak <b>' + escHtml(j.label) + '</b> ' +
        (missed ? 'is gemist' : 'is mislukt') + ': ' + escHtml(lr.error || 'geen details — zie impactos.err') + '</p>';
    });

    if (!h.stalled_goals.length && !h.failed_goals.length && !h.failed_jobs.length) {
      (h.issues||[]).forEach(function(issue){
        html += '<p style="font-size:11px;color:#991b1b;margin-top:2px">- ' + escHtml(issue) + '</p>';
      });
    }

    html += '</div>';
    el.innerHTML = html;
  }).catch(function(){});
}

function runAutoheal(elId, btnId) {
  var btn = document.getElementById(btnId || 'autoheal-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Bezig...'; }
  fetch('/api/strategist/autoheal', { method:'POST' }).then(function(r){return r.json();}).then(function(){
    loadSystemHealth(elId, btnId);
  }).catch(function(){}).finally(function(){ if (btn) btn.disabled = false; });
}

// ── Strategist Agent (AI-analyse) ──
var strategistAnalysis = null;
function runStrategistAnalysis() {
  var btn = document.getElementById('strat-btn');
  var resultEl = document.getElementById('strategist-result');
  if (!btn || !resultEl) return;
  btn.disabled = true; btn.textContent = 'Bezig met analyseren...';
  resultEl.innerHTML = '<div class="loading" style="padding:20px"><div class="spinner"></div><p>Hermes analyseert alle projecten, doelen en kansen...</p></div>';
  fetch('/api/strategist/analyse', { method:'POST' }).then(function(r){return r.json();}).then(function(data){
    if (data.error) { resultEl.innerHTML = '<div class="empty-state">Fout: ' + escHtml(data.error) + '</div>'; return; }
    var analysis = data.analysis || '(geen analyse)';
    globalStrategistAnalysis = analysis;
    var html = '<div class="section-card" style="margin-top:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">' +
      '<h4 style="font-size:14px;font-weight:700">\u{1F9E0} Strategist — prioriteiten</h4>' +
      '<span style="font-size:10px;color:#94a3b8">' + (data.timestamp||'').slice(0,16) + '</span>' +
      '</div>' +
      '<div class="strategist-analyse-content">' +
      mdToHtml(analysis) +
      '</div>' +
      '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #e2e8f0;text-align:center">' +
      '<button onclick="runStrategistExecute()" id="strat-exec-btn" style="padding:10px 24px;background:#059669;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer">\u{2699}\u{FE0F} Zal ik de agents vragen dit uit te voeren?</button>' +
      '<div id="strategist-exec-result" style="margin-top:12px"></div>' +
      '</div></div>';
    resultEl.innerHTML = html;
    btn.textContent = '\u{1F9E0} Opnieuw analyseren';
  }).catch(function(e){
    resultEl.innerHTML = '<div class="empty-state">Fout: ' + escHtml(e.message) + '</div>';
  }).finally(function(){ btn.disabled = false; });
}

var globalStrategistAnalysis = null;
function runStrategistExecute() {
  if (!globalStrategistAnalysis) { alert('Geen analyse om uit te voeren'); return; }
  var execBtn = document.getElementById('strat-exec-btn');
  var execResult = document.getElementById('strategist-exec-result');
  if (!execBtn || !execResult) return;
  execBtn.disabled = true; execBtn.textContent = 'Bezig met uitvoeren...';
  execResult.innerHTML = '<div class="loading"><div class="spinner"></div><p>Hermes vertaalt prioriteiten naar concrete acties...</p></div>';
  fetch('/api/strategist/execute', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({analysis:globalStrategistAnalysis}) })
    .then(function(r){return r.json();}).then(function(data){
      if (data.error) { execResult.innerHTML = '<div class="empty-state" style="color:#ef4444">Fout: ' + escHtml(data.error) + '</div>'; return; }
      var actions = data.actions || [];
      var created = data.created_goals || [];
      var heal = data.autoheal || {};
      var healed = (heal.deleted||[]).length > 0 || (heal.resumed||[]).length > 0;
      var html = '';
      if (healed) {
        html += '<div style="background:#e0f2fe;border:1px solid #7dd3fc;border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:#075985;margin-bottom:4px">\u{1F527} Zelf-reparatie uitgevoerd</p>';
        (heal.deleted||[]).forEach(function(d){
          html += '<p style="font-size:11px;color:#075985;margin-top:2px">- verwijderd: ' + escHtml(d.title) + ' <span style="color:#0369a1">(' + escHtml(d.project) + ' — ' + escHtml(d.reason) + ')</span></p>';
        });
        (heal.resumed||[]).forEach(function(r){
          html += '<p style="font-size:11px;color:#075985;margin-top:2px">- hervat: ' + escHtml(r.title) + ' <span style="color:#0369a1">(' + escHtml(r.project) + ')</span></p>';
        });
        html += '</div>';
      }
      if (created.length > 0) {
        html += '<div style="background:var(--ok-bg);border:1px solid var(--ok-border);border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:var(--ok-fg);margin-bottom:4px">Uitgevoerd</p>' +
          '<p style="font-size:11px;color:var(--ok-fg)">' + created.length + ' doelen aangemaakt (' + (data.created_tasks||0) + ' taken)</p>';
        for (var g = 0; g < created.length; g++) {
          var startedBadge = created[g].auto_started
            ? ' <span class="pill pill-ok">direct gestart</span>'
            : ' <span class="pill pill-warn">wacht op jouw akkoord — zie inbox</span>';
          html += '<p style="font-size:11px;color:#166534;margin-top:4px">- ' + escHtml(created[g].title) + ' <span style="color:#15803d">(' + created[g].project + ')</span>' + startedBadge + '</p>';
        }
        html += '</div>';
      } else if (actions.length > 0) {
        html += '<div style="background:#fef3c7;border:1px solid #fde68a;border-radius:8px;padding:12px;margin-bottom:12px;text-align:left">' +
          '<p style="font-weight:600;font-size:13px;color:#92400e;margin-bottom:4px">\u{1F4CB} Geanalyseerde acties</p>';
        for (var a = 0; a < actions.length; a++) {
          var item = actions[a];
          var prioColor = item.priority === 'kritiek' ? '#ef4444' : item.priority === 'belangrijk' ? '#d97706' : '#64748b';
          html += '<div style="font-size:11px;padding:6px 8px;margin:4px 0;background:#fff;border-radius:4px;border:1px solid #f1f5f9">' +
            '<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:' + prioColor + ';color:#fff;font-size:10px;margin-right:6px;text-transform:uppercase">' + escHtml(item.priority||'') + '</span>' +
            escHtml(item.action||'') +
            (item.target_project ? ' <span style="color:#64748b">(' + escHtml(item.target_project) + ')</span>' : '') +
            '</div>';
        }
        html += '</div>';
      }
      html += '<button onclick="window.location.reload()" style="padding:8px 20px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">\u{1F504} Vernieuw dashboard</button>';
      execResult.innerHTML = html;
      execBtn.textContent = '\u{2699}\u{FE0F} Opnieuw uitvoeren';
    }).catch(function(e){
      execResult.innerHTML = '<div class="empty-state" style="color:#ef4444">Fout: ' + escHtml(e.message) + '</div>';
    }).finally(function(){ execBtn.disabled = false; });
}

