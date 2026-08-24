// ── Impact OS — Iris Welkomstour ──────────────────────────────────────
// Staat los in de gedeelde globale scope (klassieke script-laadvolgorde,
// zie index.html — tour.js laadt als laatste).
//
// Wat dit doet:
//   · Een stap-voor-stap rondleiding door het hele dashboard en de tools,
//     gepresenteerd door Iris (de AI-manager, met avatar).
//   · Elke stap highlight één element (spotlight) en toont een tooltip-kaart
//     met: wat de tool is, en wat het effect/resultaat ervan is.
//   · Navigeert zelf naar de juiste tab/project om elk onderdeel te tonen.
//   · Past zich aan aan de instance: als een tool/tab er niet is (lege
//     database, beperkte scope), slaat de tour die stap over i.p.v. vast te
//     lopen.
//   · Aan/uit via Instellingen (localStorage). Geen backend nodig.
//   · Geen emoji — clean, in de bestaande design-tokens.
//
// Hooks:
//   startTour()              — start de rondleiding vanaf stap 0
//   setTourEnabled(bool)     — zet de aan/uit-schakelaar in Instellingen
//   renderTourSettings()     — HTML voor de Instellingen-sectie

(function () {
  'use strict';

  var STORAGE_ENABLED = 'impactos.tour.enabled';
  var STORAGE_DONE = 'impactos.tour.done';

  var state = { active: false, i: 0, steps: [], target: null, demoProject: null };

  // ── Hulp: element vinden ───────────────────────────────────────────────
  function visibleSel(sel) {
    var el = document.querySelector(sel);
    return (el && el.offsetParent !== null) ? el : null;
  }
  function cardWithText(t) {
    var cards = document.querySelectorAll('.section-card, .dash-alert, .kpi-grid');
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].textContent && cards[i].textContent.indexOf(t) >= 0 && cards[i].offsetParent !== null) return cards[i];
    }
    return null;
  }
  function navBtn(label) {
    var b = document.querySelectorAll('.sidebar-nav button');
    for (var i = 0; i < b.length; i++) {
      if (b[i].textContent.trim() === label) return b[i];
    }
    return null;
  }

  // ── Demo-project bepalen (past zich aan de instance aan) ─────────────────
  function pickDemoProject() {
    if (state.demoProject) return state.demoProject;
    // Eerst projecten uit de control-room (als die al geladen is).
    var cards = document.querySelectorAll('#action-center-panel .pc-name, .project-card .pc-name');
    if (cards && cards.length) { state.demoProject = (cards[0].textContent || '').trim() || null; }
    if (!state.demoProject && typeof selectProject === 'function') {
      // Probeer via de globale PROJECTS-lijst.
      if (typeof PROJECTS !== 'undefined' && PROJECTS.length) {
        // Zoek een project dat daadwerkelijk bestaat in de DOM (Control Room).
        for (var i = 0; i < PROJECTS.length; i++) {
          var c = document.querySelector('.project-card');
          if (c) { state.demoProject = (c.querySelector('.pc-name') || {}).textContent || PROJECTS[i]; break; }
        }
      }
    }
    if (!state.demoProject) state.demoProject = 'WeAreImpact';
    return state.demoProject;
  }

  // ── Stappen ────────────────────────────────────────────────────────────
  // Elke stap: { title, tool, effect, locate, center, tab, before, ok, skip }
  //  - locate(): element om te highlighten (mag null zijn → centraal tonen)
  //  - before(): actie vóór het tonen (bv. project openen)
  //  - ok(): als deze false teruggeeft, wordt de stap overgeslagen
  function defaultSteps() {
    return [
      {
        title: 'Welkom bij Impact OS',
        center: true,
        tool: 'Ik ben Iris, je AI-manager.',
        effect: 'Ik neem je in een korte rondleiding mee door het dashboard en de belangrijkste tools. Je kunt op elk moment stoppen of later opnieuw starten via Instellingen.',
      },
      {
        title: 'Control Room',
        locate: function () { return visibleSel('.page-head h2'); },
        tool: 'Het startscherm: één overzicht van alle projecten en systemen.',
        effect: 'Hier zie je in één oogopslag de status van elke website, de actieve doelen en wat er op jouw beslissing wacht.',
      },
      {
        title: 'Actiecentrum',
        locate: function () { return visibleSel('#action-center-panel'); },
        tool: 'Alles wat nu om een menselijke keuze vraagt.',
        effect: 'Concepten ter review, doelen die wachten op akkoord, fouten die aandacht nodig hebben — één inbox. Klik = direct klaar.',
        ok: function () { return !!visibleSel('#action-center-panel'); },
      },
      {
        title: 'Iris dagbriefing',
        locate: function () { return visibleSel('#iris-panel'); },
        tool: 'De dagelijkse analyse van je AI-manager.',
        effect: 'Iris leest de cijfers per project, ziet wat er is verbeterd of vastgelopen, en geeft een prioriteitenlijst. "Analyseer nu" dwingt een verse scan.',
        ok: function () { return !!visibleSel('#iris-panel'); },
      },
      {
        title: 'Projecten',
        locate: function () { return visibleSel('.project-card'); },
        tool: 'Je portfolio aan websites en producten.',
        effect: 'Klik een kaart om het volledige dashboard van dat project te openen — met eigen cijfers, doelen en tools.',
        ok: function () { return !!visibleSel('.project-card'); },
      },
      {
        title: 'Projectdashboard openen',
        center: true,
        before: function () { selectProject(pickDemoProject()); },
        tool: 'We openen een project als voorbeeld: ' + 'het dashboard.',
        effect: 'Het laadt de status, prestatiecijfers, lopende doelen en de specifieke tools van dat project.',
        ok: function () { return typeof selectProject === 'function'; },
      },
      {
        title: 'Statusbanner',
        locate: function () { return visibleSel('#dash-banner-container'); },
        tool: 'Live status van het actieve doel of de lopende taak.',
        effect: 'Toont wat er op dit moment draait en hoe ver het is. Bij een mislukt doel klik je de banner aan om het opnieuw te proberen.',
        ok: function () { return !!visibleSel('#dash-banner-container'); },
      },
      {
        title: 'Signalen',
        locate: function () { return visibleSel('.dash-alert'); },
        tool: 'Belangrijke waarschuwingen en kansen.',
        effect: 'Rode/gele kaarten vragen actie; groene kaarten zijn kansen. De knop erin voert de oplossing vaak in één klik uit.',
        ok: function () { return !!visibleSel('.dash-alert'); },
      },
      {
        title: 'Beste volgende stap',
        locate: function () { return cardWithText('Beste volgende stap'); },
        tool: 'Het aanbevolen vervolg, door de AI bepaald.',
        effect: '"Nu uitvoeren" start die actie direct — bijvoorbeeld een artikel schrijven of een scan draaien. Geen handwerk nodig.',
        ok: function () { return !!cardWithText('Beste volgende stap'); },
      },
      {
        title: 'Prestatieoverzicht',
        locate: function () { return visibleSel('.kpi-grid'); },
        tool: 'De kerncijfers: geïndexeerde pagina’s, klikken, CTR en gemiddelde positie.',
        effect: 'Allemaal uit Google Search Console. De kleine regel daaronder toont de laatste 7 dagen — wat er nú gebeurt, niet alleen het gemiddelde.',
        ok: function () { return !!visibleSel('.kpi-grid'); },
      },
      {
        title: 'Grafieken',
        locate: function () { return visibleSel('#dash-chart-clicks'); },
        tool: 'Klikken, impressies en positie over 28 dagen.',
        effect: 'Eén meetreeks per grafiek (eigen as) — de positiegrafiek vergelijkt deze periode met de vorige. Zo zie je trend, geen misleidende dubbele schaal.',
        ok: function () { return !!visibleSel('#dash-chart-clicks'); },
      },
      {
        title: 'Doelen',
        locate: function () { return cardWithText('Doelen ('); },
        tool: 'Lopende doelen van de AI-strategist.',
        effect: 'Elk doel bestaat uit taken die autonoom worden uitgevoerd. De teller toont voortgang; "Beheer" opent het volledige overzicht.',
        ok: function () { return !!cardWithText('Doelen ('); },
      },
      {
        title: 'Activiteit',
        locate: function () { return visibleSel('#project-activity-panel'); },
        tool: 'Een live log van wat de agents hebben gedaan.',
        effect: 'Elke regel is een actie met tijdstip en resultaat — inclusief een link naar het artefact (artikel, bestand) dat is opgeleverd.',
        ok: function () { return !!visibleSel('#project-activity-panel'); },
      },
      {
        title: 'Navigatie',
        locate: function () { return visibleSel('.sidebar-nav'); },
        tool: 'Het menu naar alle onderdelen van een project.',
        effect: 'Van Dashboard naar Kansen, Optimalisatie, Leads, Doelen en meer. We gaan straks naar de tools van dit project.',
        ok: function () { return !!visibleSel('.sidebar-nav'); },
      },
      {
        title: 'Batch Prospecting',
        tab: 'Leads',
        locate: function () { return visibleSel('#batch-btn'); },
        tool: 'De lead-zoekactie van dit project.',
        effect: 'Doorzoekt het web met gerichte queries, scrapet websites, analyseert ze met AI en slaat bruikbare leads op in de database én Obsidian.',
        ok: function () { return !!visibleSel('#batch-btn') && !!navBtn('Leads'); },
      },
      {
        title: 'LinkedIn Personen zoeken',
        tab: 'Leads',
        locate: function () { return visibleSel('#linkedin-query'); },
        tool: 'Gericht zoeken naar beslissers.',
        effect: 'Vindt professionals via site:linkedin.com/in op functie en regio — handig om de juiste contactpersoon per lead te bepalen.',
        ok: function () { return !!visibleSel('#linkedin-query'); },
      },
      {
        title: 'Lead overzicht',
        tab: 'Leads',
        locate: function () { return visibleSel('#lead-list'); },
        tool: 'De volledige lead-lijst met statusfilter.',
        effect: 'Nieuw → verrijkt → geverifieerd → gecontacteerd → reactie. Filter toont precies waar elke lead staat in de pipeline.',
        ok: function () { return !!visibleSel('#lead-list'); },
      },
      {
        title: 'Klaar',
        center: true,
        tool: 'Dat was de rondleiding.',
        effect: 'Je zet de tour aan of uit in Instellingen → Welkomstour, en start hem daar ook opnieuw. Veel succes met de presentatie.',
      },
    ];
  }

  // ── DOM bouwen ──────────────────────────────────────────────────────────
  var overlay, spotlight, card, cardTitle, cardStep, cardTool, cardEffect, btnPrev, btnOutline, btnNext, btnSkip, outline;

  function buildTourDOM() {
    if (overlay) return;

    overlay = document.createElement('div');
    overlay.id = 'iris-tour-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:100000;display:none';

    spotlight = document.createElement('div');
    spotlight.id = 'iris-tour-spotlight';
    spotlight.style.cssText = 'position:fixed;border-radius:12px;box-shadow:0 0 0 9999px rgba(15,23,42,.58);border:2px solid var(--accent);pointer-events:auto;transition:left .25s ease,top .25s ease,width .25s ease,height .25s ease;display:none';

    card = document.createElement('div');
    card.id = 'iris-tour-card';
    card.innerHTML =
      '<div class="iris-tour-head">' +
        '<img class="iris-tour-avatar" src="iris-avatar.png" alt="Iris" onerror="this.style.display=\'none\'">' +
        '<div><div class="iris-tour-name">Iris</div><div class="iris-tour-step" id="iris-tour-step"></div></div>' +
      '</div>' +
      '<h3 id="iris-tour-title"></h3>' +
      '<div class="iris-tour-row"><span class="iris-tour-label">Tool</span><span id="iris-tour-tool"></span></div>' +
      '<div class="iris-tour-row iris-tour-effect"><span class="iris-tour-label">Effect</span><span id="iris-tour-effect"></span></div>' +
      '<div class="iris-tour-actions">' +
        '<button class="iris-tour-btn" id="iris-tour-prev">Vorige</button>' +
        '<button class="iris-tour-btn" id="iris-tour-outline">Overzicht</button>' +
        '<button class="iris-tour-btn iris-tour-primary" id="iris-tour-next">Volgende</button>' +
      '</div>' +
      '<button class="iris-tour-skip" id="iris-tour-skip">Niet meer tonen</button>' +
      '<div class="iris-tour-outline" id="iris-tour-outline-list" style="display:none"></div>';

    overlay.appendChild(spotlight);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    cardStep = document.getElementById('iris-tour-step');
    cardTitle = document.getElementById('iris-tour-title');
    cardTool = document.getElementById('iris-tour-tool');
    cardEffect = document.getElementById('iris-tour-effect');
    btnPrev = document.getElementById('iris-tour-prev');
    btnOutline = document.getElementById('iris-tour-outline');
    btnNext = document.getElementById('iris-tour-next');
    btnSkip = document.getElementById('iris-tour-skip');
    outline = document.getElementById('iris-tour-outline-list');

    btnPrev.onclick = prevStep;
    btnNext.onclick = nextStep;
    btnOutline.onclick = toggleOutline;
    btnSkip.onclick = finishTour;

    document.addEventListener('keydown', function (e) {
      if (!state.active) return;
      if (e.key === 'Escape') finishTour();
      else if (e.key === 'ArrowRight') nextStep();
      else if (e.key === 'ArrowLeft') prevStep();
    });
  }

  function positionCurrent() { renderStepDom(state.i); }

  // ── Render stap ─────────────────────────────────────────────────────────
  function renderStepDom(i) {
    var s = state.steps[i];
    if (!s) return;
    cardStep.textContent = 'Stap ' + (i + 1) + ' van ' + state.steps.length;
    cardTitle.textContent = s.title;
    cardTool.textContent = s.tool;
    cardEffect.textContent = s.effect;

    btnPrev.style.visibility = (i === 0) ? 'hidden' : 'visible';
    btnNext.textContent = (i === state.steps.length - 1) ? 'Sluiten' : 'Volgende';

    if (s.center) {
      spotlight.style.display = 'none';
      card.style.left = '50%';
      card.style.top = '50%';
      card.style.transform = 'translate(-50%,-50%)';
    } else if (state.target) {
      card.style.transform = 'none';
      spotlight.style.display = 'block';
      var pad = 6;
      var r = state.target.getBoundingClientRect();
      spotlight.style.left = (r.left - pad) + 'px';
      spotlight.style.top = (r.top - pad) + 'px';
      spotlight.style.width = (r.width + pad * 2) + 'px';
      spotlight.style.height = (r.height + pad * 2) + 'px';

      var cw = card.offsetWidth || 340;
      var ch = card.offsetHeight || 220;
      var vw = window.innerWidth, vh = window.innerHeight;
      var left = r.left + r.width / 2 - cw / 2;
      left = Math.max(12, Math.min(left, vw - cw - 12));
      var top = r.bottom + 12;
      if (top + ch > vh - 12) top = r.top - ch - 12;
      if (top < 12) top = 12;
      card.style.left = left + 'px';
      card.style.top = top + 'px';
    } else {
      spotlight.style.display = 'none';
      card.style.transform = 'translate(-50%,-50%)';
      card.style.left = '50%';
      card.style.top = '50%';
    }
    if (outline.style.display === 'block') renderOutline();
  }

  function waitForTarget(s) {
    return new Promise(function (resolve) {
      if (s.center) return resolve(null);
      var tries = 0;
      (function poll() {
        var el = s.locate ? s.locate() : null;
        if (el) return resolve(el);
        if (tries++ > 50) return resolve(null);
        setTimeout(poll, 100);
      })();
    });
  }

  function showStep(i) {
    if (i < 0 || i >= state.steps.length) return;
    state.i = i;
    var s = state.steps[i];
    var run = function () {
      if (s.tab && typeof currentTab !== 'undefined' && currentTab !== s.tab) {
        try { switchView(s.tab); } catch (e) {}
      }
      waitForTarget(s).then(function (el) {
        if (el) {
          try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
          setTimeout(function () { state.target = el; renderStepDom(i); }, 280);
        } else {
          state.target = null;
          renderStepDom(i);
        }
      });
    };
    if (s.before) {
      try { s.before(); } catch (e) {}
      setTimeout(run, 350);
    } else {
      run();
    }
  }

  // ── Navigatie (sla overgeslagen stappen over) ────────────────────────────
  function nextStep() {
    if (state.i >= state.steps.length - 1) { finishTour(); return; }
    var n = state.i + 1;
    while (n < state.steps.length && state.steps[n].ok && !state.steps[n].ok()) n++;
    if (n >= state.steps.length) { finishTour(); return; }
    showStep(n);
  }
  function prevStep() {
    if (state.i <= 0) return;
    var n = state.i - 1;
    while (n > 0 && state.steps[n].ok && !state.steps[n].ok()) n--;
    showStep(n);
  }
  function jumpTo(i) {
    outline.style.display = 'none';
    showStep(i);
  }
  function toggleOutline() {
    if (outline.style.display === 'block') { outline.style.display = 'none'; return; }
    renderOutline();
    outline.style.display = 'block';
  }
  function renderOutline() {
    var html = '';
    state.steps.forEach(function (s, idx) {
      html += '<button class="iris-tour-outline-item' + (idx === state.i ? ' active' : '') + '" data-i="' + idx + '">' +
        (idx + 1) + '. ' + escHtml(s.title) + '</button>';
    });
    outline.innerHTML = html;
    Array.prototype.forEach.call(outline.querySelectorAll('button'), function (b) {
      b.onclick = function () { jumpTo(parseInt(b.getAttribute('data-i'), 10)); };
    });
  }

  // ── Start / stop ─────────────────────────────────────────────────────────
  function startTour() {
    buildTourDOM();
    state.steps = defaultSteps();
    // Vooraf filteren: stappen waarvan de hele context ontbreekt (bv. geen
    // projecten op een lege instance) — maar pas definitief beslissen bij het
    // tonen, want de DOM verandert tijdens de tour.
    pickDemoProject();
    state.active = true;
    overlay.style.display = 'block';
    showStep(0);
  }
  function finishTour() {
    state.active = false;
    if (overlay) overlay.style.display = 'none';
    if (spotlight) spotlight.style.display = 'none';
    localStorage.setItem(STORAGE_DONE, '1');
  }
  function maybeAutoStartTour() {
    if (window.__tourAutoStarted) return;
    window.__tourAutoStarted = true;
    var en = localStorage.getItem(STORAGE_ENABLED);
    if (en === null) en = '1';
    var done = localStorage.getItem(STORAGE_DONE);
    if (en === '1' && done !== '1') {
      setTimeout(startTour, 1000);
    }
  }

  // ── Instellingen + sidebar-knop ──────────────────────────────────────────
  function setTourEnabled(v) {
    localStorage.setItem(STORAGE_ENABLED, v ? '1' : '0');
  }
  function renderTourSettings() {
    var on = localStorage.getItem(STORAGE_ENABLED);
    if (on === null) on = '1';
    var checked = (on === '1') ? 'checked' : '';
    var label = (on === '1') ? 'Aan' : 'Uit';
    return '<div class="section-card" style="margin-bottom:16px">' +
      '<h4 style="font-size:13px;font-weight:600;margin-bottom:8px">Welkomstour</h4>' +
      '<p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">Iris neemt je stap voor stap mee door het dashboard en de tools. Zet uit om de rondleiding niet automatisch te starten bij het openen.</p>' +
      '<div style="display:flex;align-items:center;gap:12px">' +
        '<label class="iris-toggle"><input type="checkbox" ' + checked + ' onchange="setTourEnabled(this.checked)"><span class="iris-toggle-track"></span></label>' +
        '<span style="font-size:12px;color:var(--text)">' + label + '</span>' +
        '<button onclick="startTour()" class="btn btn-ghost btn-sm" style="margin-left:auto">Start tour opnieuw</button>' +
      '</div></div>';
  }

  function ensureTourButton() {
    if (!document || !document.querySelector) return;
    var footer = document.querySelector('.sidebar-footer');
    if (!footer || document.getElementById('iris-tour-btn')) return;
    var b = document.createElement('button');
    b.id = 'iris-tour-btn';
    b.innerHTML = '<span class="icon">◈</span> Tour';
    b.onclick = startTour;
    try { footer.insertBefore(b, footer.firstChild); } catch (e) {}
  }
  function observeSidebar() {
    if (window.__tourObserver) return;
    window.__tourObserver = true;
    var obs = new MutationObserver(function () { ensureTourButton(); });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function initTour() {
    buildTourDOM();
    ensureTourButton();
    observeSidebar();
    window.addEventListener('resize', function () { if (state.active) positionCurrent(); });
    // Pas auto-starten als de Control Room geladen is (route heeft gelopen).
    if (document.getElementById('main-content') && document.querySelector('.page-head, .sidebar')) {
      maybeAutoStartTour();
    } else {
      setTimeout(maybeAutoStartTour, 1500);
    }
  }

  // Globaal beschikbaar maken voor de rest van de SPA.
  window.startTour = startTour;
  window.setTourEnabled = setTourEnabled;
  window.renderTourSettings = renderTourSettings;
  window.initTour = initTour;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTour);
  } else {
    initTour();
  }
})();
