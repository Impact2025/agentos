"""De waarheidsaudit — de toets die zoekt wat stíl kapot is.

Deze suite bewaakt drie dingen, in volgorde van belang:

1. **Dat de audit vindt wat er is.** Een invariant die niets vindt op data waar
   aantoonbaar iets mis is, is erger dan geen invariant: hij geeft dekking.
2. **Dat hij niet vindt wat er niet is.** Een audit die ruis produceert wordt
   binnen een week genegeerd, en dan beschermt hij nergens meer tegen.
3. **Dat hij zelf niet stil kan falen.** Een kapotte toets die zwijgt, is
   precies de faalmodus waartegen dit hele bestand bestaat.
"""
import json
import uuid

import pytest

from backend.domains.iris import integrity as ig
from backend.shared.database import get_conn


# ── Hulpjes ────────────────────────────────────────────────────────────────

# De testsessie deelt één database met alle andere modules. Deze suite maakt
# echte sites en jobs aan, en die moeten na afloop weg: een achtergebleven
# 'rejected'-job met een URL zou een ándere testmodule laten struikelen — en
# omgekeerd zou vreemde data hier valse bevindingen opleveren.
_GEMAAKT: dict = {"sites": [], "jobs": []}


def _site(naam="AuditSite"):
    sid = f"s-{uuid.uuid4().hex[:8]}"
    with get_conn() as c:
        c.execute("INSERT INTO sites (id, name, base_url, created_at) "
                  "VALUES (?, ?, ?, datetime('now'))",
                  (sid, naam, "https://audit.test"))
    _GEMAAKT["sites"].append(sid)
    return sid


def _job(site_id, *, titel="Een normaal artikel", status="published",
         slug="een-normaal-artikel", keyword="normaal artikel", url="https://audit.test/blog/x"):
    jid = f"j-{uuid.uuid4().hex[:8]}"
    resultaat = json.dumps({"success": True, "url": url}) if url else ""
    with get_conn() as c:
        c.execute(
            "INSERT INTO content_jobs (id, site_id, title, keyword, status, slug, "
            "publish_result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (jid, site_id, titel, keyword, status, slug, resultaat))
    _GEMAAKT["jobs"].append(jid)
    return jid


def _over(bevindingen, *subjects):
    """Filter op de gevallen die déze test heeft aangemaakt.

    Nooit `== []` op het geheel: andere testmodules laten hun eigen jobs achter,
    en die horen hier niet meegewogen te worden.
    """
    return [b for b in bevindingen if any(s in b.subject for s in subjects)]


def _leeg_de_bevindingen():
    with get_conn() as c:
        c.execute("DELETE FROM integrity_findings")
        c.execute("DELETE FROM activity_log WHERE action = 'waarheidsaudit'")
        c.execute("DELETE FROM inbox_dismissals")


def _kaarten(status=None):
    sql = "SELECT * FROM activity_log WHERE action = 'waarheidsaudit'"
    p = []
    if status:
        sql += " AND status = ?"
        p.append(status)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, p)]


@pytest.fixture(autouse=True)
def web(monkeypatch):
    """De buitenwereld, gestuurd vanuit de test.

    Autouse: geen enkele toets mag in de suite het echte net op. Onbekende
    URL's gelden als onbereikbaar (`ONBEKEND`) — dat is ook wat er gebeurt als
    audit.test niet resolvet, dus een test die vergeet een stand te zetten
    krijgt hetzelfde antwoord als in productie bij een timeout.
    """
    standen: dict = {}
    monkeypatch.setattr(ig, "_pagina_status",
                        lambda url: standen.get(url, ig.ONBEKEND))
    return standen


@pytest.fixture
def gsc():
    """Een GSC-waarneming: deze pagina vertoonde op dit zoekwoord.

    De tweede wereld waar `cluster_kannibalisatie` op leunt — geen
    administratie van het systeem, maar wat de zoekmachine terugmeldt.
    """
    gemaakt = []

    def _zet(site_id, page_url, top_query, position=15.0, impressions=40):
        with get_conn() as c:
            c.execute(
                "INSERT INTO gsc_history (id, site_id, scope, page_url, date, "
                "clicks, impressions, ctr, position, top_query, created_at) "
                "VALUES (?, ?, 'page', ?, '2026-08-03', 0, ?, 0.0, ?, ?, "
                "datetime('now'))",
                (f"g-{uuid.uuid4().hex[:8]}", site_id, page_url, impressions,
                 position, top_query))
        gemaakt.append(site_id)

    yield _zet
    with get_conn() as c:
        for sid in gemaakt:
            c.execute("DELETE FROM gsc_history WHERE site_id = ?", (sid,))


@pytest.fixture(autouse=True)
def _schoon():
    _GEMAAKT["sites"].clear()
    _GEMAAKT["jobs"].clear()
    _leeg_de_bevindingen()
    yield
    _leeg_de_bevindingen()
    with get_conn() as c:
        for jid in _GEMAAKT["jobs"]:
            c.execute("DELETE FROM content_jobs WHERE id = ?", (jid,))
        for sid in _GEMAAKT["sites"]:
            c.execute("DELETE FROM sites WHERE id = ?", (sid,))


# ── 1. Vindt de audit wat er is? ───────────────────────────────────────────

class TestVindtWatErIs:

    def test_afgewezen_maar_live_wordt_gevonden(self, web):
        """De scherpste toets van de set: database zegt 'rejected', web zegt live.

        Zo stond 'Impact OS end-to-end publicatietest' op 2 aug 2026 op de site
        van een klant — afgewezen in de wachtrij, nooit offline gehaald.
        """
        sid = _site()
        url = "https://audit.test/blog/agent-os-e2e"
        web[url] = ig.LEEFT
        _job(sid, titel="Impact OS end-to-end publicatietest", status="rejected", url=url)
        b = _over(ig._check_afgewezen_maar_live(), url)
        assert len(b) == 1
        assert "rejected" in b[0].detail

    def test_afgewezen_pagina_die_al_offline_is_geeft_geen_bevinding(self, web):
        """De toets vergelijkt twee wérelden, niet twee velden.

        Op 2 aug 2026 meldde hij negen pagina's op grond van een URL in
        `publish_result` — een bewering van het systeem over zijn eigen verleden.
        Vier gaven een harde 404 en één alleen de SPA-schil: ze waren allang
        offline gehaald. De kaart vroeg om werk dat gedaan was en kon nooit meer
        dichtgaan, want `publish_result` verandert nooit meer.
        """
        sid = _site()
        url = "https://audit.test/blog/allang-offline"
        web[url] = ig.WEG
        _job(sid, status="rejected", url=url)
        assert _over(ig._check_afgewezen_maar_live(), url) == []

    def test_onbereikbare_pagina_blijft_staan_met_die_twijfel(self, web):
        """Onbereikbaar is geen vrijspraak: één netwerkhik mag geen pagina
        vrijpleiten die wél degelijk live staat."""
        sid = _site()
        url = "https://audit.test/blog/niet-te-bereiken"
        web[url] = ig.ONBEKEND
        _job(sid, status="rejected", url=url)
        b = _over(ig._check_afgewezen_maar_live(), url)
        assert len(b) == 1
        assert "niet te bereiken" in b[0].detail

    def test_afgewezen_zonder_publicatie_is_geen_bevinding(self, web):
        """Het normale geval: afgewezen vóórdat er iets live ging. Geen alarm."""
        sid = _site()
        jid = _job(sid, status="rejected", url="")
        assert _over(ig._check_afgewezen_maar_live(), jid, "audit.test") == []

    @pytest.mark.parametrize("pad", [
        "levensverhaal-vastleggen-complete-gids-+-casestudy-anton-(12",
        "schrijf-meta-titel-&-description-voor-pagina-2",
        "✅-checklist-10-essentiele-stappen",
        "Hoofdletters-Zijn-Ook-Fout",
    ])
    def test_onveilig_gepubliceerd_pad_wordt_gevonden(self, pad):
        sid = _site()
        jid = _job(sid, status="published", url=f"https://audit.test/blog/{pad}")
        assert len(_over(ig._check_slug_onveilig(), jid)) == 1

    def test_net_gepubliceerd_pad_geeft_geen_bevinding(self):
        sid = _site()
        jid = _job(sid, status="published",
                   url="https://audit.test/blog/zeven-manieren-om-te-beginnen")
        assert _over(ig._check_slug_onveilig(), jid) == []

    def test_slechte_kolom_bij_een_nette_url_is_geen_404(self):
        """Wat live staat is het pad, niet de boekhouding.

        De eerste versie las `content_jobs.slug` en meldde acht gezonde pagina's
        als "vrijwel zeker 404": de publisher slugificeert bij het publiceren,
        dus de URL was netjes terwijl de kolom de ruwe titel had bewaard. De
        voorgeschreven stap (opnieuw publiceren + 301) zou acht dubbelingen
        hebben opgeleverd.
        """
        sid = _site()
        jid = _job(sid, status="published",
                   slug="levensverhaal-vastleggen-complete-gids-+-casestudy-anton-(12",
                   url="https://audit.test/blog/levensverhaal-vastleggen-casestudy-anton")
        assert _over(ig._check_slug_onveilig(), jid) == []
        # Wél zichtbaar als hygiëne: wie de kolom leest, leest niet de wereld.
        assert len(_over(ig._check_slug_kolom_wijkt_af(), jid)) == 1

    def test_dubbel_koppelteken_is_lelijk_maar_geen_bevinding(self):
        """Een audit die op cosmetica alarm slaat, leert de lezer alarm negeren."""
        sid = _site()
        jid = _job(sid, status="published",
                   url="https://audit.test/blog/wat-is-bijeen-het-platform-in--4")
        assert _over(ig._check_slug_onveilig(), jid) == []

    def test_kannibalisatie_op_genormaliseerd_zoekwoord(self):
        """Twee artikelen, verschillende titels, hetzelfde zoekwoord.

        De vergelijking loopt via `_keyword_key`, dus hoofdletters, een
        vraagteken en dubbele spaties maken er niet twee zoekwoorden van.
        """
        sid = _site()
        _job(sid, titel="9 beste partners voor AI", slug="negen-beste",
             keyword="beste partners voor AI-oplossingen?")
        _job(sid, titel="Zeven AI-partners die werken", slug="zeven-ai",
             keyword="beste  partners voor ai-oplossingen")
        b = _over(ig._check_zoekwoord_kannibalisatie(), sid)
        assert len(b) == 1
        assert "2 artikelen live" in b[0].detail

    def test_verschillende_zoekwoorden_geen_kannibalisatie(self):
        sid = _site()
        _job(sid, slug="a", keyword="teambuilding hoofddorp")
        _job(sid, slug="b", keyword="vrijwilligers werven")
        assert _over(ig._check_zoekwoord_kannibalisatie(), sid) == []

    def test_zelfde_zoekwoord_op_verschillende_sites_mag(self):
        """Twee sites die hetzelfde zoekwoord bedienen concurreren niet met
        zichzelf — kannibalisatie is per definitie binnen één domein."""
        a, b = _site("SiteA"), _site("SiteB")
        _job(a, slug="x", keyword="teambuilding")
        _job(b, slug="y", keyword="teambuilding")
        assert _over(ig._check_zoekwoord_kannibalisatie(), a, b) == []

    def test_published_zonder_url_is_onbewezen(self):
        sid = _site()
        jid = _job(sid, status="published", url="")
        b = _over(ig._check_publicatie_onbewezen(), jid)
        assert len(b) == 1
        assert "geen bewijs" in b[0].detail

    def test_publicatiefout_zonder_kaart_wordt_gevonden(self):
        """Goedgekeurd, niet live, en niemand die het ziet.

        Tot 2 aug 2026 werd `publicatie_mislukt` met status 'ok' gelogd; een
        'ok'-kaart is een logregel en geen inbox-item. Ictusgo's 404 kwam zo drie
        ochtenden terug als 'les' in de briefing zonder één keer als beslissing
        op het scherm te staan.
        """
        sid = _site()
        jid = _job(sid, titel="Teambuilding groep 8: zeven GPS-avonturen",
                   status="publish_failed", url="")
        b = _over(ig._check_publicatiefout_zonder_kaart(), jid)
        assert len(b) == 1
        assert "niemand krijgt dit te zien" in b[0].detail

    def test_publicatiefout_met_openstaande_kaart_is_geen_bevinding(self):
        """Er staat al een fout-kaart voor: dan werkt de melding en is er niets
        stils aan de hand."""
        sid = _site()
        titel = "Artikel met een nette foutkaart"
        jid = _job(sid, titel=titel, status="publish_failed", url="")
        with get_conn() as c:
            c.execute(
                "INSERT INTO activity_log (id, project, action, detail, status, created_at) "
                "VALUES (?, 'AuditSite', 'publicatie_mislukt', ?, 'error', datetime('now'))",
                (f"a-{uuid.uuid4().hex[:8]}", f"'{titel}' goedgekeurd maar NIET gepubliceerd"))
        try:
            assert _over(ig._check_publicatiefout_zonder_kaart(), jid) == []
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM activity_log WHERE detail LIKE ?", (f"%{titel}%",))

    def test_kaart_telt_bij_tot_wat_er_nu_nog_open_staat(self, web, monkeypatch):
        """Een kaart die om negen pagina's vraagt terwijl er vijf al weg zijn,
        vraagt om werk dat gedaan is — en leert de lezer kaarten te wantrouwen.

        2 aug 2026: `afgewezen_maar_live` bleef "9 geval(len)" melden nadat de
        toets er nog maar vier vond. De kaart hoort te blijven staan (de klasse
        is niet weg) maar zijn tekst is een momentopname en moet meebewegen.
        """
        sid = _site()
        urls = [f"https://audit.test/blog/telkaart-{i}" for i in range(3)]
        for u in urls:
            web[u] = ig.LEEFT
            _job(sid, status="rejected", url=u)
        inv = ig.invariant("afgewezen_maar_live")
        monkeypatch.setattr(ig, "INVARIANTEN", [inv])

        ig.run_audit(source="test")
        kaart = _kaarten("error")[0]
        assert "3 geval(len)" in kaart["detail"]

        # Twee pagina's zijn offline gehaald; de derde staat er nog.
        web[urls[0]] = ig.WEG
        web[urls[1]] = ig.WEG
        ig.run_audit(source="test")
        with get_conn() as c:
            ververst = dict(c.execute("SELECT * FROM activity_log WHERE id = ?",
                                      (kaart["id"],)).fetchone())
        assert "1 geval(len)" in ververst["detail"]
        assert ververst["status"] == "error", "de klasse is niet weg, dus de kaart blijft"

    def test_stilstand_die_dubbel_in_de_inbox_staat(self):
        """Eén gemiste taak hoort één kaart te zijn.

        2 aug 2026: 'biweekly_content' stond dubbel — als foutkaart uit
        `activity_log` én als gat uit `scheduler_gaps`, woordelijk dezelfde zin,
        maar alleen op de tweede zat de knop "Nu alsnog draaien". De toets
        filtert bewust niet op de actienaam 'gemiste_runs': dát was de vórige
        tweede meldweg, en een toets die alleen die naam kent is blind voor de
        volgende.
        """
        from backend.shared import downtime
        from datetime import datetime, timedelta

        moment = datetime.now().astimezone() - timedelta(days=2)
        downtime.record_gap("test_dubbel", "Testtaak", moment,
                            cost="geen concepten klaargezet", recoverable=True)
        aid = f"a-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO activity_log (id, project, action, detail, status, created_at) "
                "VALUES (?, 'Scheduler', 'stilstand_v2', ?, 'error', datetime('now'))",
                (aid, "test_dubbel| Testtaak draaide 2x niet"))
        try:
            b = _over(ig._check_stilstand_dubbel_gemeld(), "dubbel:test_dubbel")
            assert len(b) == 1
            assert "stilstand_v2" in b[0].detail

            # Zodra het gat dicht is, is er ook niets meer om dubbel te melden.
            downtime.mark_recovered("test_dubbel")
            assert _over(ig._check_stilstand_dubbel_gemeld(), "dubbel:test_dubbel") == []
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM activity_log WHERE id = ?", (aid,))
                c.execute("DELETE FROM scheduler_gaps WHERE job_id = 'test_dubbel'")

    def test_stilstand_langs_een_weg_is_geen_bevinding(self):
        """Het normale geval na de fix: alleen het gat, geen tweede kaart."""
        from backend.shared import downtime
        from datetime import datetime, timedelta

        downtime.record_gap("test_enkel", "Testtaak", datetime.now().astimezone() -
                            timedelta(days=2), cost="geen concepten", recoverable=True)
        try:
            assert _over(ig._check_stilstand_dubbel_gemeld(), "dubbel:test_enkel") == []
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM scheduler_gaps WHERE job_id = 'test_enkel'")

    def test_trefkans_gevleid_bij_te_veel_onbeslist(self):
        """Een leerlus die zijn missers wegstreept als 'onbeslist' meldt tegelijk
        dat het goed gaat — de gevaarlijkste combinatie die er is."""
        with get_conn() as c:
            c.execute("DELETE FROM iris_predictions")
            for i in range(12):
                status = "unclear" if i < 6 else ("correct" if i < 9 else "wrong")
                c.execute(
                    "INSERT INTO iris_predictions (id, report_date, project, site_id, "
                    "metric, direction, baseline, horizon_days, due_date, status, created_at) "
                    "VALUES (?, '2026-07-01', 'P', 's', 'clicks', 'up', 0, 7, "
                    "'2026-07-08', ?, datetime('now'))",
                    (f"p-{uuid.uuid4().hex[:8]}", status))
        try:
            b = ig._check_trefkans_gevleid()
            assert len(b) == 1 and "6 van 12" in b[0].detail
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM iris_predictions")

    def test_gezonde_verhouding_onbeslist_geeft_geen_bevinding(self):
        with get_conn() as c:
            c.execute("DELETE FROM iris_predictions")
            for i in range(12):
                status = "unclear" if i < 2 else "correct"
                c.execute(
                    "INSERT INTO iris_predictions (id, report_date, project, site_id, "
                    "metric, direction, baseline, horizon_days, due_date, status, created_at) "
                    "VALUES (?, '2026-07-01', 'P', 's', 'clicks', 'up', 0, 7, "
                    "'2026-07-08', ?, datetime('now'))",
                    (f"p-{uuid.uuid4().hex[:8]}", status))
        try:
            assert ig._check_trefkans_gevleid() == []
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM iris_predictions")

    def test_geneste_publish_result_telt_ook_als_bewijs(self):
        """De pipeline schrijft {"site": {"url": ...}}, de directe publisher
        {"url": ...}. Allebei zijn bewijs; alleen de eerste vorm herkennen zou
        de halve voorraad vals beschuldigen."""
        sid = _site()
        jid = _job(sid, status="published")
        with get_conn() as c:
            c.execute("UPDATE content_jobs SET publish_result = ? WHERE id = ?",
                      (json.dumps({"site": {"url": "https://audit.test/blog/y"}}), jid))
        assert _over(ig._check_publicatie_onbewezen(), jid) == []

    def test_kanaal_dood_meldt_de_site_en_niet_elk_artikel(self, web):
        """Twaalf 404's van één site zijn één storing, geen twaalf.

        3 aug 2026: elk artikel dat Impact OS naar ictusgo.nl publiceerde gaf een
        404, en het Actiecentrum toonde daar twaalf losse kaarten voor met de
        knop 'Opnieuw publiceren' — een remedie voor iets dat niet kapot was,
        want de publicatie-API antwoordde elke keer 201.
        """
        sid = _site()
        for n in range(4):
            url = f"https://audit.test/blog/dood-{n}"
            web[url] = ig.WEG
            _job(sid, titel=f"Artikel {n}", status="published", url=url)
        b = _over(ig._check_publicatiekanaal_dood(), f"site:{sid}")
        assert len(b) == 1, "één bevinding per site, niet per artikel"
        assert "opnieuw publiceren lost niets op" in b[0].detail.lower()

    def test_kanaal_leeft_zodra_er_iets_van_de_site_live_staat(self, web):
        """Staat er wél iets live, dan is het kanaal niet de diagnose.

        Dan is elk 404'end artikel zijn eigen probleem, en daar zijn de gewone
        fout-kaarten voor. Een kanaal-kaart eroverheen zou de aandacht juist
        wegtrekken van het artikel dat echt hulp nodig heeft.
        """
        sid = _site()
        for n in range(4):
            url = f"https://audit.test/blog/dood-{n}"
            web[url] = ig.WEG
            _job(sid, titel=f"Artikel {n}", status="published", url=url)
        levend = "https://audit.test/blog/leeft"
        web[levend] = ig.LEEFT
        _job(sid, titel="Een artikel dat wel rendert", status="published", url=levend)
        assert _over(ig._check_publicatiekanaal_dood(), f"site:{sid}") == []

    def test_onbereikbare_site_is_geen_kanaalstoring(self, web):
        """Onbereikbaar is geen bewijs — in geen van beide richtingen.

        Ligt het net tijdens de audit plat, dan mag dat geen kanaal-storing
        verzinnen; dezelfde regel als in `_pagina_status`.
        """
        sid = _site()
        for n in range(4):
            # geen stand gezet → ONBEKEND
            _job(sid, titel=f"Artikel {n}", status="published",
                 url=f"https://audit.test/blog/stil-{n}")
        assert _over(ig._check_publicatiekanaal_dood(), f"site:{sid}") == []

    def _job_met_body(self, site_id, *, blog_html, titel="Artikel", status="pending_review"):
        jid = f"j-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO content_jobs (id, site_id, title, keyword, status, slug, "
                "blog_html, publish_result, created_at) VALUES "
                "(?, ?, ?, '', ?, 'x', ?, '', datetime('now'))",
                (jid, site_id, titel, status, blog_html))
        _GEMAAKT["jobs"].append(jid)
        return jid

    def test_verzonnen_persoonlijke_autoriteit_op_ander_project_wordt_gevonden(self):
        """19 aug 2026, Bijeen: 'in mijn jaren als directeur van Stichting de
        Baan draaide ik meer dan veertig van die dagen...' — een verzonnen naam,
        functie en trackrecord, veroorzaakt door de ongescopeerde merk-brief."""
        sid = _site(naam="Bijeen")
        html = ("<h1>Vrijwilligersdag organiseren</h1><p>In mijn jaren als "
                "directeur van Stichting de Baan draaide ik meer dan veertig "
                "van die dagen.</p>")
        jid = self._job_met_body(sid, blog_html=html)
        b = _over(ig._check_merkbrief_verkeerd_project(), jid)
        assert len(b) == 1
        assert "verzonnen" in b[0].detail

    def test_weareimpact_mag_wel_in_eerste_persoon_met_functietitel(self):
        """Op WeAreImpact IS 'als Vincent van Munster' de bedoelde stem."""
        sid = _site(naam="WeAreImpact")
        html = ("<h1>Titel</h1><p>Als directeur van WeAreImpact zie ik dit "
                "elke dag.</p>")
        jid = self._job_met_body(sid, blog_html=html)
        assert _over(ig._check_merkbrief_verkeerd_project(), jid) == []


# ── 2. Produceert hij geen ruis? ───────────────────────────────────────────

class TestGeenRuis:

    def test_normaal_derde_persoons_artikel_op_ander_project_is_geen_bevinding(self):
        """Het gewone geval: geen enkele biografische claim, geen alarm."""
        sid = _site(naam="Bijeen")
        html = "<h1>Vrijwilligersdag organiseren</h1><p>Een goede vrijwilligersdag begint bij duidelijke rollen.</p>"
        jid = f"j-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO content_jobs (id, site_id, title, keyword, status, slug, "
                "blog_html, publish_result, created_at) VALUES "
                "(?, ?, 'Artikel', '', 'pending_review', 'x', ?, '', datetime('now'))",
                (jid, sid, html))
        _GEMAAKT["jobs"].append(jid)
        assert _over(ig._check_merkbrief_verkeerd_project(), jid) == []

    def test_tweede_ronde_vindt_niets_nieuws(self):
        """Draaien mag niets veranderen aan de wereld.

        Deze suite deelt de database met alle andere testmodules, dus "nul
        bevindingen" is hier niet af te dwingen — wél dat een tweede ronde
        direct na de eerste níéts nieuws oplevert. Zou dat wel zo zijn, dan is
        een subject niet stabiel (een timestamp, een teller) en wordt elke ronde
        een nieuwe bevinding: dan liegt de hele levensloop.
        """
        ig.run_audit(source="test")
        tweede = ig.run_audit(source="test")
        assert tweede["nieuw"] == 0
        assert tweede["opgelost"] == 0

    def test_ontbrekende_tabel_is_geen_storing(self, monkeypatch):
        """Een domein dat nog nooit is gebruikt heeft geen tabel. Dat is 'niet
        van toepassing', geen fout — anders opent elke verse installatie met
        een kaart over iets wat niemand kapot heeft gemaakt."""
        import sqlite3

        def kapot():
            raise sqlite3.OperationalError("no such table: radar_signals")

        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("t", "Test", "geen", ig.STIL, "stap", kapot)])
        r = ig.run_audit(source="test")
        assert r["mislukt"] == []
        assert _kaarten() == []

    def test_hygiene_escaleert_nooit(self, monkeypatch):
        """Voorraadvervuiling telt, maar alarmeert niet: een rode kaart over
        903 oude radarsignalen is ruis, geen alarm."""
        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("t", "Test", "geen", ig.HYGIENE, "stap",
                         lambda: [ig.Bevinding("x", "rommel")])])
        r = ig.run_audit(source="test")
        assert r["nieuw"] == 1
        assert r["geescaleerd"] == 0
        assert _kaarten() == []

    def test_stille_bevinding_wacht_met_escaleren(self, monkeypatch):
        """Een mechanisme dat morgen vanzelf weer aanslaat (de weekscan draait
        maandag) is geen storing maar een moment in de cyclus."""
        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("t", "Test", "geen", ig.STIL, "stap",
                         lambda: [ig.Bevinding("x", "stil kapot")])])
        r = ig.run_audit(source="test")
        assert r["nieuw"] == 1 and r["geescaleerd"] == 0

        # Nu net zo lang terugdateren dat de drempel wél wordt gehaald.
        with get_conn() as c:
            c.execute("UPDATE integrity_findings SET first_seen = "
                      "datetime('now', ?)", (f"-{ig._STIL_ESCALATIE_DAGEN + 1} day",))
        r = ig.run_audit(source="test")
        assert r["geescaleerd"] == 1

    def test_blokkerend_escaleert_meteen(self, monkeypatch):
        """Bij een dode pagina helpt wachten niet: elke dag telt mee in de
        zoekresultaten."""
        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("t", "Test", "geen", ig.BLOKKEREND, "stap",
                         lambda: [ig.Bevinding("x", "kapot")])])
        assert ig.run_audit(source="test")["geescaleerd"] == 1
        assert len(_kaarten("error")) == 1


# ── 3. De levensloop ───────────────────────────────────────────────────────

class TestLevensloop:

    def _inv(self, gevallen):
        return [ig.Invariant("t", "Testklasse", "geen", ig.BLOKKEREND, "ruim op",
                             lambda: [ig.Bevinding(g, f"geval {g}") for g in gevallen()])]

    def test_een_kaart_per_klasse_niet_per_geval(self, monkeypatch):
        """Negen dode pagina's zijn negen keer hetzelfde besluit, geen negen
        besluiten. Het Actiecentrum is een inbox, geen bugtracker."""
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: list("abcdefghi")))
        r = ig.run_audit(source="test")
        assert r["nieuw"] == 9
        assert len(_kaarten("error")) == 1
        assert "9 geval(len)" in _kaarten("error")[0]["detail"]

    def test_tweede_ronde_maakt_geen_tweede_kaart(self, monkeypatch):
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: ["a", "b"]))
        ig.run_audit(source="test")
        ig.run_audit(source="test")
        assert len(_kaarten("error")) == 1

    def test_nieuw_geval_hangt_onder_de_bestaande_kaart(self, monkeypatch):
        """Een elfde geval van dezelfde klasse verdient geen tweede kaart."""
        gevallen = ["a"]
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: list(gevallen)))
        ig.run_audit(source="test")
        gevallen.append("b")
        ig.run_audit(source="test")
        assert len(_kaarten("error")) == 1
        with get_conn() as c:
            ids = {r["escalated_id"] for r in c.execute(
                "SELECT escalated_id FROM integrity_findings WHERE resolved_at IS NULL")}
        assert len(ids) == 1 and None not in ids

    def test_kaart_blijft_staan_zolang_er_iets_openstaat(self, monkeypatch):
        gevallen = ["a", "b"]
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: list(gevallen)))
        ig.run_audit(source="test")
        gevallen.remove("a")
        r = ig.run_audit(source="test")
        assert r["opgelost"] == 1
        assert r["kaarten_gesloten"] == 0
        assert _kaarten("ok") == []

    def test_kaart_sluit_zichzelf_met_bewijs(self, monkeypatch):
        """Een bevinding sluit niet omdat iemand zegt dat het gefikst is, maar
        omdat de toets hem niet meer vindt."""
        gevallen = ["a", "b"]
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: list(gevallen)))
        ig.run_audit(source="test")
        gevallen.clear()
        r = ig.run_audit(source="test")
        assert r["open"] == 0
        assert r["kaarten_gesloten"] == 1
        assert len(_kaarten("ok")) == 1
        # En de rode kaart is weggeklikt uit het Actiecentrum.
        with get_conn() as c:
            assert c.execute("SELECT COUNT(*) FROM inbox_dismissals").fetchone()[0] == 1

    def test_terugval_is_zichtbaar_als_nieuw(self, monkeypatch):
        """Een probleem dat terugkomt na herstel krijgt een nieuwe rij. Dezelfde
        rij heropenen zou 'dit was drie weken weg' uitwissen."""
        gevallen = ["a"]
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: list(gevallen)))
        ig.run_audit(source="test")
        gevallen.clear()
        ig.run_audit(source="test")
        gevallen.append("a")
        r = ig.run_audit(source="test")
        assert r["nieuw"] == 1

    def test_vergrijsde_bevinding_krijgt_scherpere_stap(self, monkeypatch):
        monkeypatch.setattr(ig, "INVARIANTEN", self._inv(lambda: ["a"]))
        ig.run_audit(source="test")
        with get_conn() as c:
            c.execute("UPDATE integrity_findings SET first_seen = "
                      "datetime('now', ?)", (f"-{ig._VERGRIJSD_DAGEN + 1} day",))
            c.execute("UPDATE integrity_findings SET escalated_id = NULL")
            c.execute("DELETE FROM activity_log WHERE action = 'waarheidsaudit'")
        ig.run_audit(source="test")
        assert "dagen open" in _kaarten("error")[0]["next_step"]


# ── 4. De audit mag zelf niet stil falen ───────────────────────────────────

class TestAuditFaaltNietStil:

    def test_kapotte_invariant_wordt_gemeld(self, monkeypatch):
        """Een toets die zelf stuk is, meet niets — en dat moet je zien.

        Dit is de belangrijkste test van het bestand: zonder deze meldplicht is
        de audit zelf het volgende systeem dat succes rapporteert terwijl het
        niets doet.
        """
        def kapot():
            raise ValueError("kapotte check")

        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("t", "Test", "geen", ig.STIL, "stap", kapot)])
        r = ig.run_audit(source="test")
        assert len(r["mislukt"]) == 1
        kaart = _kaarten("error")
        assert len(kaart) == 1
        assert "konden niet draaien" in kaart[0]["detail"]

    def test_een_kapotte_invariant_stopt_de_rest_niet(self, monkeypatch):
        """Zou één kapotte check de audit platleggen, dan maakt hij alle andere
        onzichtbaar — exact het probleem dat dit bestand bestrijdt."""
        def kapot():
            raise ValueError("stuk")

        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("kapot", "Kapot", "geen", ig.STIL, "stap", kapot),
            ig.Invariant("goed", "Goed", "geen", ig.BLOKKEREND, "stap",
                         lambda: [ig.Bevinding("x", "gevonden")]),
        ])
        r = ig.run_audit(source="test")
        assert r["nieuw"] == 1
        assert len(r["mislukt"]) == 1

    def test_alle_echte_invarianten_draaien_zonder_fout(self):
        """Rooktest over het échte register: elke check moet op een geldige
        database kunnen draaien. Een invariant die op een hernoemde kolom
        struikelt is blind, en een blinde toets die zwijgt is het ergste geval."""
        r = ig.run_audit(source="test")
        assert r["mislukt"] == [], f"invarianten met een fout: {r['mislukt']}"

    def test_prompt_blok_meldt_uitval_expliciet(self, monkeypatch):
        """Iris moet kunnen zien dat ze blind is. Een leeg blok zou ze lezen als
        'alles in orde' — precies de conclusie die de audit moet voorkomen."""
        from backend.domains.iris import service as iris_service
        monkeypatch.setattr(ig, "audit_summary",
                            lambda: (_ for _ in ()).throw(RuntimeError("stuk")))
        blok = iris_service._audit_blok()
        assert "NIET beschikbaar" in blok
        assert "géén conclusie" in blok


# ── 5. Het register zelf ───────────────────────────────────────────────────

class TestRegister:

    def test_elke_invariant_documenteert_zijn_incident(self):
        """Een audit waarvan niemand de herkomst kent, wordt genegeerd zodra hij
        een keer ongelegen komt. Daarom draagt elke regel het incident dat hem
        heeft veroorzaakt, en een stap die een mens echt kan zetten."""
        for inv in ig.INVARIANTEN:
            assert len(inv.incident) > 40, f"{inv.key} mist een incident"
            assert len(inv.stap) > 20, f"{inv.key} mist een concrete stap"
            assert inv.severity in (ig.BLOKKEREND, ig.STIL, ig.HYGIENE)

    def test_keys_zijn_uniek(self):
        keys = [i.key for i in ig.INVARIANTEN]
        assert len(keys) == len(set(keys))

    def test_samenvatting_sorteert_op_ernst(self, monkeypatch):
        monkeypatch.setattr(ig, "INVARIANTEN", [
            ig.Invariant("h", "Hygiene", "x" * 50, ig.HYGIENE, "y" * 30,
                         lambda: [ig.Bevinding("a", "d")]),
            ig.Invariant("b", "Blokkerend", "x" * 50, ig.BLOKKEREND, "y" * 30,
                         lambda: [ig.Bevinding("c", "d")]),
        ])
        ig.run_audit(source="test")
        per = ig.audit_summary()["per_invariant"]
        assert per[0]["severity"] == ig.BLOKKEREND


# ── Klontering op de gate: het cijfer codeert het stopmoment ────────────────

class TestKwaliteitsscoreIsStopregel:
    """2 aug 2026: 39 van 76 Wachtrij-artikelen op exact 82, 4 op exact 80.

    Dat is geen kwaliteitsverdeling maar de vingerafdruk van een verbeter-lus
    die stopt bij de eerste meting boven de gate — bij een reviewer die 65-92
    varieert op identieke invoer. `approve_and_publish` besluit vervolgens op
    dat cijfer. Niets hieraan gooit ooit een fout.
    """

    def _scored(self, site_id, score, n, status="pending_review"):
        with get_conn() as c:
            for _ in range(n):
                jid = f"j-{uuid.uuid4().hex[:8]}"
                c.execute(
                    "INSERT INTO content_jobs (id, site_id, title, keyword, status, "
                    "seo_score, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                    (jid, site_id, "Artikel", "kw", status, score))
                _GEMAAKT["jobs"].append(jid)

    def test_klontering_vlak_boven_de_gate_wordt_gevonden(self):
        """De scores komen uit CONTENT_MIN_SCORE, niet uit een vast getal.

        Stond hier 82 hardgecodeerd (gate+2 toen de gate 80 was), dan zwijgt
        deze toets zodra iemand de gate verzet — en dat is precies gebeurd:
        `.env` zet CONTENT_MIN_SCORE=85, waarmee 82 buiten het venster viel en
        de test de klontering niet meer zag. Een toets die aan de oude waarde
        van zijn eigen onderwerp hangt, faalt stil op de dag dat het onderwerp
        verandert.
        """
        from backend.shared.config import CONTENT_MIN_SCORE
        sid = _site()
        self._scored(sid, CONTENT_MIN_SCORE + 2, 39)
        self._scored(sid, CONTENT_MIN_SCORE + 10, 5)
        bevindingen = ig._check_kwaliteitsscore_is_stopregel()
        assert _over(bevindingen, "score_is_stopregel"), \
            "klontering op gate+2 hoort gevonden te worden"

    def test_gezonde_spreiding_geeft_geen_bevinding(self):
        sid = _site()
        for score in (80, 83, 86, 88, 90, 92, 84, 87):
            self._scored(sid, score, 5)
        assert not _over(ig._check_kwaliteitsscore_is_stopregel(), "score_is_stopregel")

    def test_klontering_hoog_boven_de_gate_is_geen_storing(self):
        """Veertig artikelen op 95 betekent dat ze goed zijn, niet dat de lus stopte."""
        sid = _site()
        self._scored(sid, 95, 40)
        assert not _over(ig._check_kwaliteitsscore_is_stopregel(), "score_is_stopregel")

    def test_kleine_voorraad_is_ruis(self):
        """Drie stukken op 82 kan toeval zijn; daar geen kaart over."""
        sid = _site()
        self._scored(sid, 82, 6)
        assert not _over(ig._check_kwaliteitsscore_is_stopregel(), "score_is_stopregel")

    def test_gepubliceerde_artikelen_tellen_niet_mee(self):
        """De uitspraak gaat over wat nog beslist moet worden, niet over historie."""
        sid = _site()
        self._scored(sid, 82, 40, status="published")
        assert not _over(ig._check_kwaliteitsscore_is_stopregel(), "score_is_stopregel")


# ── De pogingenteller vergeleken met de échte Gauntlet-historie ────────────

class TestOrchestratorTellerTeruggezet:
    """15 aug 2026: `scripts/bijeen_worldclass_engine.py` escaleerde rechtstreeks
    naar `/api/gauntlet` — dus buiten de cross-run cap om — en schreef daarna
    `orchestrator_attempts=1, status='stuck'` terug op het bronrecord. Precies de
    twee velden waarop de rem besluit. Eén artikel is zo 17x herschreven, met
    129 duplicaten in de Wachtrij en 6,2M tokens op één dag tot gevolg.

    De duplicaten wérden gemeld; dat de teller zélf loog, zag niemand. Deze
    toets vergelijkt daarom twee administraties: wat `content_jobs` beweert over
    het aantal pogingen, tegen wat er in `gauntlet_runs` werkelijk staat.
    """

    # Per test een eigen titel: `gauntlet_runs` wordt niet opgeruimd tussen
    # tests, dus zou een vaste titel de runs van de vorige test meetellen —
    # precies de vervuiling die deze toets bij echte data moet weerstaan.
    @pytest.fixture(autouse=True)
    def _eigen_titel(self):
        self.TITEL = ("Vrijwilligers roosterplanner: plan diensten zonder appjes "
                      f"en spreadsheets {uuid.uuid4().hex[:8]}")

    def _bron(self, site_id, *, titel=None, status="stuck", pogingen=1):
        jid = f"j-{uuid.uuid4().hex[:8]}"
        with get_conn() as c:
            c.execute(
                "INSERT INTO content_jobs (id, site_id, title, keyword, status, "
                "seo_score, orchestrator_attempts, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (jid, site_id, titel or self.TITEL, "kw", status, 82, pogingen))
        _GEMAAKT["jobs"].append(jid)
        return jid

    def _runs(self, n, titel=None):
        """n Gauntlet-runs die dit artikel als objective hadden."""
        with get_conn() as c:
            for i in range(n):
                c.execute(
                    "INSERT INTO gauntlet_runs (id, objective, benchmark, status, "
                    "threshold, max_iterations, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'passed', 85, 3, datetime('now'), datetime('now'))",
                    (f"gaunt-test-{uuid.uuid4().hex[:10]}",
                     f"Herschrijf het artikel '{titel or self.TITEL}' tot "
                     f"wereldklasse SEO-content (ronde {i}).", "bench"))

    def test_teller_die_achterloopt_op_de_runs_wordt_gevonden(self):
        sid = _site()
        self._bron(sid, pogingen=1)
        self._runs(17)
        bevindingen = _over(ig._check_orchestrator_teller_teruggezet(),
                            "orchestrator_teller:")
        assert bevindingen, "17 runs tegen 1 getelde poging hoort gevonden te worden"
        assert "17x" in bevindingen[0].detail
        assert "terug" in bevindingen[0].detail

    def test_teller_op_nul_meldt_een_pad_zonder_cap_en_niet_een_reset(self):
        """Twee storingen met dezelfde meting. Nooit geteld = er is een pad dat de
        cap niet kent; teruggezet = er is een schrijver die moet stoppen. Dezelfde
        zin voor beide stuurt het zoeken de verkeerde kant op."""
        sid = _site()
        self._bron(sid, pogingen=0)
        self._runs(9)
        bevindingen = _over(ig._check_orchestrator_teller_teruggezet(),
                            "orchestrator_teller:")
        assert bevindingen
        assert "nooit één poging geteld" in bevindingen[0].detail

    def test_kloppende_teller_geeft_geen_bevinding(self):
        """Het normale geval: de cap heeft geteld wat er gedraaid heeft."""
        sid = _site()
        self._bron(sid, pogingen=3)
        self._runs(3)
        assert not _over(ig._check_orchestrator_teller_teruggezet(),
                         "orchestrator_teller:")

    def test_een_enkele_ronde_is_geen_storing(self):
        """Onder de drempel van 3 runs zegt het verschil niets: een ronde die nu
        loopt heeft de teller al opgehoogd vóór de run bestaat, en andersom."""
        sid = _site()
        self._bron(sid, pogingen=0)
        self._runs(2)
        assert not _over(ig._check_orchestrator_teller_teruggezet(),
                         "orchestrator_teller:")

    def test_afgesloten_bronrecord_telt_niet_mee(self):
        """`mark_superseded` is precies de remedie; een gesloten bron hoort de
        kaart niet in leven te houden."""
        sid = _site()
        self._bron(sid, status="superseded", pogingen=1)
        self._runs(17)
        assert not _over(ig._check_orchestrator_teller_teruggezet(),
                         "orchestrator_teller:")

    def test_geneste_herschrijftitels_tellen_als_een_artikel(self):
        """De Gauntlet staget zijn uitvoer als "Herschrijf het artikel 'X'", en
        die kan zelf weer bron worden. Een kale substring-telling ziet dan drie
        artikelen waar er één staat en meldt 3x, 22x én 25x voor hetzelfde stuk."""
        sid = _site()
        self._bron(sid, pogingen=1)
        self._bron(sid, titel=f"Herschrijf het artikel '{self.TITEL}' "
                              f"(project X) naar een wereldklasse versie", pogingen=1)
        self._runs(17)
        bevindingen = _over(ig._check_orchestrator_teller_teruggezet(),
                            "orchestrator_teller:")
        assert len(bevindingen) == 1, \
            f"één artikel hoort één bevinding te geven, kreeg {len(bevindingen)}"


class TestKernTitel:
    """De afpel-functie waarop de groepering rust. Knipt hij te vroeg af, dan
    belanden twee ongelijke artikelen in één groep — en bij het opruimen van
    duplicaten is dat het verschil tussen een overbodige versie sluiten en een
    uniek artikel weggooien.
    """

    def test_pelt_het_orchestrator_omhulsel_af(self):
        assert ig._kern_titel(
            "Herschrijf het artikel 'Vrijwilligers roosterplanner: plan diensten' "
            "(project Bijeen) naar een wereldklasse versie die de grens haalt."
        ) == "Vrijwilligers roosterplanner: plan diensten"

    def test_pelt_het_script_omhulsel_af(self):
        assert ig._kern_titel(
            "Herschrijf het artikel 'Impactrapportage maken' tot wereldklasse "
            "SEO-content (1200-1500 woorden). Zoekterm: impactrapportage."
        ) == "Impactrapportage maken"

    def test_knipt_niet_op_een_woord_binnen_de_titel(self):
        """'Van plan tot nazorg' werd 'Van plan' zolang de match op 'tot' stopte —
        waarmee dat artikel op één hoop belandde met elk ander stuk dat toevallig
        met dezelfde twee woorden begon."""
        assert ig._kern_titel(
            "Herschrijf het artikel 'Van plan tot nazorg: een geslaagd evenement' "
            "(project Bijeen) naar een wereldklasse versie."
        ) == "Van plan tot nazorg: een geslaagd evenement"

    def test_titel_met_apostrof_blijft_heel(self):
        """Nederlandse titels bevatten apostrofs ("de 3 zwakst scorende pagina's");
        een niet-gulzige match breekt precies daarop."""
        assert ig._kern_titel(
            "Herschrijf het artikel 'Optimaliseer de 3 zwakst scorende pagina's "
            "van Bijeen' (project Bijeen) naar een wereldklasse versie."
        ) == "Optimaliseer de 3 zwakst scorende pagina's van Bijeen"

    def test_laat_een_gewone_titel_met_rust(self):
        kaal = "Sociale cohesie versterken met een evenement: 6 aanpakken"
        assert ig._kern_titel(kaal) == kaal

    def test_laat_een_niet_herschrijf_opdracht_met_rust(self):
        opdracht = "[SEO Copywriter] Schrijf 1 nieuw SEO-artikel voor Virginia."
        assert ig._kern_titel(opdracht) == opdracht


# ── Afgekapte meta-titel: wat er live gaat, niet wat in de body staat ───────

class TestMetatitelAfgekapt:
    """2 aug 2026: 47 van 103 artikelen droegen een op 60 tekens afgesneden
    meta-titel, 15 daarvan al gepubliceerd. Google toont die zoals hij is.

    4 aug 2026: de toets las die titel niet op maar réconstrueerde hem als
    `volledig[:60]`. Bij nameting van zes WeAreImpact-bevindingen waren er drie
    kerngezond — hun titel was korter dan 60 tekens of viel toevallig op een
    woordgrens. Dat is dezelfde fout die `afgewezen_maar_live` twee dagen eerder
    maakte: over de buitenwereld oordelen zonder hem te raadplegen. Deze tests
    voeden daarom de live <title> in, want dát is nu de invoer van de toets.
    """

    @pytest.fixture
    def live(self, monkeypatch):
        """Stel in wat de site als <title> teruggeeft."""
        titels: dict = {}

        def _nep(url: str):
            return titels.get(url)

        monkeypatch.setattr(ig, "_live_metatitel", _nep)
        return titels

    def _met_body(self, site_id, kop, status="published",
                  url="https://audit.test/blog/x"):
        jid = f"j-{uuid.uuid4().hex[:8]}"
        body = f"<h1>{kop}</h1><p>" + ("tekst " * 40) + "</p>"
        with get_conn() as c:
            c.execute(
                "INSERT INTO content_jobs (id, site_id, title, keyword, status, "
                "blog_html, publish_result, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (jid, site_id, kop, "kw", status, body,
                 json.dumps({"success": True, "url": url})))
        _GEMAAKT["jobs"].append(jid)
        return jid

    def test_middenin_een_woord_afgekapt_wordt_gevonden(self, live):
        sid = _site()
        kop = "Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in de luchthavenregio"
        jid = self._met_body(sid, kop)
        live["https://audit.test/blog/x"] = kop[:60]  # de harde afkap, zoals hij live stond
        assert _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_nette_korte_titel_geeft_geen_bevinding(self, live):
        sid = _site()
        kop = "Een nette korte titel"
        jid = self._met_body(sid, kop)
        live["https://audit.test/blog/x"] = kop
        assert not _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_instructie_echo_wordt_gevonden(self, live):
        sid = _site()
        kop = "Zo val je op als interimmer (54 tekens)"
        jid = self._met_body(sid, kop)
        live["https://audit.test/blog/x"] = kop  # de echo staat écht in de <title>
        assert _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_merkstaart_telt_niet_als_afwijking(self, live):
        """De site plakt zijn naam achter elke titel; dat is opmaak, geen storing."""
        sid = _site()
        kop = "Een nette korte titel"
        jid = self._met_body(sid, kop)
        live["https://audit.test/blog/x"] = f"{kop} | AuditSite"
        assert not _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_herschreven_titel_is_geen_afkapping(self, live):
        """Live staat iets ánders — niet iets kapots.

        Dit is het vals-positief dat de reconstruerende versie opleverde:
        '✅ Checklist: 10 essentiële stappen…' werd gemeld omdat het afweek van
        de body, terwijl die titel volledig en gezond is. Alleen een titel die
        het begín van de bedoelde titel is én te vroeg ophoudt, is afgekapt.
        """
        sid = _site()
        jid = self._met_body(sid, "Checklist: 10 onmisbare stappen voor programmamanagers")
        live["https://audit.test/blog/x"] = "✅ Checklist: 10 essentiële stappen voor een programmamanager"
        assert not _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_onbereikbare_pagina_pleit_niet_vrij_en_beschuldigt_niet(self, live):
        """Geen antwoord is geen bewijs — in geen van beide richtingen."""
        sid = _site()
        jid = self._met_body(
            sid, "Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in de luchthavenregio")
        # niets in `live` gezet → _live_metatitel geeft None
        assert not _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")

    def test_concepten_tellen_niet_mee(self, live):
        """De uitspraak gaat over wat live staat; een concept is nog te repareren."""
        sid = _site()
        kop = "Bedrijfsuitje Hoofddorp Schiphol - Jouw teambeleving in de luchthavenregio"
        jid = self._met_body(sid, kop, status="pending_review")
        live["https://audit.test/blog/x"] = kop[:60]
        assert not _over(ig._check_metatitel_afgekapt(), f"metatitel:{jid}")


class TestClusterKannibalisatie:
    """Meerdere eigen pagina's die bij Google op hetzelfde zoekwoord vertonen.

    De wereld-versie van `zoekwoord_kannibalisatie`. Die toets leest
    `content_jobs.keyword` — de administratie van het systeem over zijn eigen
    werk — en was daardoor blind voor alles wat buiten Impact OS om is
    gepubliceerd. Bij Bewaard voor Jou stonden 102 pagina's live, vertoonden er
    zeven op 'levensverhaal vastleggen', en kende `content_jobs` er twee.
    """

    def test_vindt_twee_paginas_op_een_zoekwoord(self, gsc):
        sid = _site("ClusterSite")
        gsc(sid, "https://audit.test/blog/levensverhaal-vastleggen-gids",
            "levensverhaal vastleggen", 9.3)
        gsc(sid, "https://audit.test/kennisbank/levensverhaal-vastleggen-tijd",
            "levensverhaal vastleggen", 25.7)

        bev = _over(ig._check_cluster_kannibalisatie(), sid)
        assert len(bev) == 1
        assert "2 eigen pagina's" in bev[0].detail
        # De positie van de beste pagina hoort erbij: die bepaalt of samenvoegen
        # loont of dat er een groter probleem onder ligt.
        assert "9,3" in bev[0].detail

    def test_www_en_niet_www_is_een_pagina(self, gsc):
        """Google indexeert ze los, maar het is één document. Zonder deze stap
        meldt de audit een site aan dat hij onder twee hostnamen bekend staat —
        het verkeerde probleem met de verkeerde stap eronder."""
        sid = _site("HostSite")
        gsc(sid, "https://audit.test/blog/vrijwilligers-werven", "vrijwilligers werven", 12.0)
        gsc(sid, "https://www.audit.test/blog/vrijwilligers-werven/", "vrijwilligers werven", 14.0)

        assert _over(ig._check_cluster_kannibalisatie(), sid) == []

    def test_merkzoekopdracht_is_geen_kannibalisatie(self, gsc):
        """Op je eigen naam hóórt de hele site te verschijnen."""
        sid = _site("MerkSite")
        with get_conn() as c:
            c.execute("UPDATE sites SET base_url = ? WHERE id = ?",
                      ("https://bijeen.app", sid))
        gsc(sid, "https://bijeen.app/blog/bijeen-komen-tips", "bijeen komen", 9.9)
        gsc(sid, "https://bijeen.app/kennisbank/bijeen-komen-uitleg", "bijeen komen", 32.3)

        assert _over(ig._check_cluster_kannibalisatie(), sid) == []

    def test_overzichtspagina_telt_niet_mee(self, gsc):
        """/blog vertoont op de onderwerpen van zijn eigen artikelen; dat hoort
        zo en is geen tweede artikel."""
        sid = _site("IndexSite")
        gsc(sid, "https://audit.test/blog", "organisatiebijdrage meten", 11.5)
        gsc(sid, "https://audit.test/blog/organisatiebijdrage-meten-zo", "organisatiebijdrage meten", 1.8)

        assert _over(ig._check_cluster_kannibalisatie(), sid) == []

    def test_een_pagina_per_zoekwoord_is_gezond(self, gsc):
        sid = _site("GezondSite")
        gsc(sid, "https://audit.test/blog/hond-adopteren", "hond adopteren asiel", 8.0)
        gsc(sid, "https://audit.test/blog/kat-herplaatsen", "kat herplaatsen", 11.0)

        assert _over(ig._check_cluster_kannibalisatie(), sid) == []


class TestSitemapDubbelePagina:
    """Twee live pagina's over hetzelfde onderwerp, buiten GSC om gevonden.

    7 aug 2026: zeven zulke paren op steentjebijsteentje.nl en
    bewaardvoorjou.nl — `cluster_kannibalisatie` (leest gsc_history) zag ze
    niet, want er waren nog geen vertoningen. Deze toets leest de sitemap
    zelf, dus geen GSC/LLM/profiel nodig.
    """

    def _sitemap(self, monkeypatch, slugs):
        import backend.domains.seo.external_content as ext
        monkeypatch.setattr(
            ext, "fetch_live_sitemap_slugs",
            lambda site: [{"title": "", "slug": s} for s in slugs])

    def test_vindt_letterlijke_kopie(self, monkeypatch):
        sid = _site()
        self._sitemap(monkeypatch, [
            "4-microgewoontes-om-je-relatie-te-verdiepen",
            "4-microgewoontes-om-je-relatie-te-verdiepen-2",
            "recept-voor-appeltaart-met-kaneel",
        ])
        bev = _over(ig._check_sitemap_dubbele_pagina(), sid)
        assert len(bev) == 1
        assert "microgewoontes" in bev[0].detail

    def test_verschillende_onderwerpen_geven_geen_bevinding(self, monkeypatch):
        sid = _site()
        self._sitemap(monkeypatch, [
            "financieel-jaarverslag-2026-download",
            "vacature-programmamanager-sociaal-domein",
        ])
        assert _over(ig._check_sitemap_dubbele_pagina(), sid) == []

    def test_lege_sitemap_geeft_geen_bevinding(self, monkeypatch):
        sid = _site()
        self._sitemap(monkeypatch, [])
        assert _over(ig._check_sitemap_dubbele_pagina(), sid) == []

    def test_dode_sitemap_blokkeert_andere_sites_niet(self, monkeypatch):
        """Eén trage/onbereikbare sitemap mag de rest van de audit niet
        meeslepen — zelfde les als elders in dit bestand."""
        import backend.domains.seo.external_content as ext

        def _stuk(site):
            raise RuntimeError("timeout")
        monkeypatch.setattr(ext, "fetch_live_sitemap_slugs", _stuk)
        sid = _site()
        assert _over(ig._check_sitemap_dubbele_pagina(), sid) == []


class TestIndexNowKeyfile:
    """4 aug 2026: 28 publicaties droegen `indexnow: {status: fout, status_code:
    403}` in hun publish_result en verder nergens. Bij nameting was het
    keybestand op 7 van de 10 sites onbereikbaar — 5× een harde 404 en 2× de
    HTML-schil van de site mét HTTP 200 erboven. Bing, Yandex, Seznam en Naver
    kregen maandenlang geen enkele URL door, terwijl de Wachtrij bij elke
    goedkeuring 'aangemeld' meldde.
    """

    @pytest.fixture
    def haal(self, monkeypatch):
        """Wat de site op het keybestand teruggeeft: {url: (status, tekst)}."""
        antwoorden: dict = {}

        class _Resp:
            def __init__(self, status, tekst):
                self.status_code, self.text = status, tekst

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                if url not in antwoorden:
                    raise OSError("niet bereikbaar")
                return _Resp(*antwoorden[url])

        import httpx
        monkeypatch.setattr(httpx, "Client", _Client)
        return antwoorden

    def _site_met_key(self, key="abc123"):
        sid = _site(f"KeySite-{uuid.uuid4().hex[:4]}")
        with get_conn() as c:
            c.execute("UPDATE sites SET indexnow_key = ? WHERE id = ?", (key, sid))
        return sid, f"https://audit.test/{key}.txt"

    def test_correct_keybestand_geeft_geen_bevinding(self, haal):
        sid, url = self._site_met_key()
        haal[url] = (200, "abc123")
        assert _over(ig._check_indexnow_keyfile(), f"indexnow:{sid}") == []

    def test_404_wordt_gevonden(self, haal):
        sid, url = self._site_met_key()
        haal[url] = (404, "Not Found")
        assert _over(ig._check_indexnow_keyfile(), f"indexnow:{sid}")

    def test_spa_schil_met_status_200_wordt_gevonden(self, haal):
        """HTTP 200 bewijst niets bij een SPA — dezelfde les als `_verify_live`.

        Twee sites gaven op hun keybestand netjes 200 terug met hun eigen HTML
        erin. Wie alleen op de statuscode toetst, noemt die twee gezond en
        blijft zich afvragen waarom Bing de submits weigert.
        """
        sid, url = self._site_met_key()
        haal[url] = (200, '<!doctype html><html lang="nl"><head><title>Home</title>')
        bev = _over(ig._check_indexnow_keyfile(), f"indexnow:{sid}")
        assert bev and "HTML-schil" in bev[0].detail

    def test_onbereikbaar_beschuldigt_niet(self, haal):
        """Een netwerkhik is geen ontbrekend keybestand."""
        sid, _ = self._site_met_key()
        assert _over(ig._check_indexnow_keyfile(), f"indexnow:{sid}") == []


class TestBevindingBlijftLiggen:
    """De audit over zichzelf. 4 aug 2026: 82 openstaande bevindingen, 54
    blokkerend of stil, en géén enkel reparatiepad in de codebase voor de drie
    grootste. Het systeem meldde trouw wat er stuk was en dat melden veranderde
    niets — de faalmodus van dit hele bestand, één verdieping hoger.
    """

    def _bevinding(self, invariant, severity, dagen_oud):
        with get_conn() as c:
            c.execute(
                "INSERT INTO integrity_findings (id, invariant, subject, project, detail, "
                "severity, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, "
                "datetime('now', ?), datetime('now'))",
                (f"f-{uuid.uuid4().hex[:8]}", invariant, f"x-{uuid.uuid4().hex[:6]}",
                 "TestProject", "detail", severity, f"-{dagen_oud} day"))

    def test_verse_bevinding_telt_niet(self):
        naam = f"vers_{uuid.uuid4().hex[:6]}"
        self._bevinding(naam, ig.BLOKKEREND, 2)
        assert _over(ig._check_bevinding_blijft_liggen(), f"blijft-liggen:{naam}") == []

    def test_oude_blokkerende_bevinding_wordt_gemeld(self):
        naam = f"oud_{uuid.uuid4().hex[:6]}"
        self._bevinding(naam, ig.BLOKKEREND, 21)
        assert _over(ig._check_bevinding_blijft_liggen(), f"blijft-liggen:{naam}")

    def test_hygiene_blijft_buiten_schot(self):
        """Voorraadvervuiling alarmeert nooit — ook niet na weken."""
        naam = f"hyg_{uuid.uuid4().hex[:6]}"
        self._bevinding(naam, ig.HYGIENE, 40)
        assert _over(ig._check_bevinding_blijft_liggen(), f"blijft-liggen:{naam}") == []

    def test_opgeloste_bevinding_telt_niet_mee(self):
        naam = f"opgelost_{uuid.uuid4().hex[:6]}"
        self._bevinding(naam, ig.BLOKKEREND, 30)
        with get_conn() as c:
            c.execute("UPDATE integrity_findings SET resolved_at = datetime('now') "
                      "WHERE invariant = ?", (naam,))
        assert _over(ig._check_bevinding_blijft_liggen(), f"blijft-liggen:{naam}") == []

    def test_een_kaart_per_invariant_niet_per_geval(self):
        """Negen keer hetzelfde besluit is één besluit."""
        naam = f"bulk_{uuid.uuid4().hex[:6]}"
        for _ in range(5):
            self._bevinding(naam, ig.BLOKKEREND, 20)
        bev = _over(ig._check_bevinding_blijft_liggen(), f"blijft-liggen:{naam}")
        assert len(bev) == 1
        assert "5 blokkerende bevinding" in bev[0].detail


# ── De herschrijf-cap moet de generatiegrens overleven ─────────────────────

class TestHerschrijftellerGereset:
    """15 aug 2026: de enige rem op de herschrijflus telde op een rij die het
    mechanisme zelf verving, dus begon elke generatie op nul.

    Gemeten stonden alle 244 WeAreImpact-jobs op `orchestrator_attempts = 0`
    terwijl één artikel zestien herschrijvingen had. Er faalde nooit iets: de
    code die de teller ophoogt was correct en de code die het bronrecord
    afsluit ook. De fout zat uitsluitend in de ruimte ertussen.
    """

    def _keten(self, site_id, bron_n, opv_n):
        """Bron met `bron_n` pogingen, superseded door een opvolger met `opv_n`."""
        bron, opv = _job(site_id, status="superseded"), _job(site_id, status="pending_review")
        with get_conn() as c:
            c.execute("UPDATE content_jobs SET orchestrator_attempts = ?, "
                      "superseded_by = ? WHERE id = ?", (bron_n, opv, bron))
            c.execute("UPDATE content_jobs SET orchestrator_attempts = ? WHERE id = ?",
                      (opv_n, opv))
        return bron, opv

    def test_teller_die_terugloopt_wordt_gevonden(self):
        sid = _site()
        _, opv = self._keten(sid, bron_n=2, opv_n=0)
        bev = _over(ig._check_herschrijfteller_gereset(), f"job:{opv}")
        assert bev, "een opvolger die op nul begint hoort gevonden te worden"
        assert "cross-run cap" in bev[0].detail

    def test_geerfde_teller_geeft_geen_bevinding(self):
        """Wat `mark_superseded` nu doet: de opvolger erft de telling."""
        sid = _site()
        _, opv = self._keten(sid, bron_n=2, opv_n=2)
        assert not _over(ig._check_herschrijfteller_gereset(), f"job:{opv}")

    def test_hoger_tellende_opvolger_is_geen_bevinding(self):
        """De opvolger mag vóórlopen — hij is zelf al een ronde verder."""
        sid = _site()
        _, opv = self._keten(sid, bron_n=1, opv_n=2)
        assert not _over(ig._check_herschrijfteller_gereset(), f"job:{opv}")

    def test_mark_superseded_geeft_de_telling_door(self):
        """De code-fix zelf, niet alleen de toets erop."""
        from backend.domains.publish import content_pipeline

        sid = _site()
        bron, opv = _job(sid, status="rejected"), _job(sid, status="pending_review")
        with get_conn() as c:
            c.execute("UPDATE content_jobs SET orchestrator_attempts = 2 WHERE id = ?", (bron,))

        content_pipeline.mark_superseded(bron, opv)

        assert content_pipeline.get_job(opv)["orchestrator_attempts"] == 2
        assert content_pipeline.get_job(bron)["status"] == "superseded"
        assert not _over(ig._check_herschrijfteller_gereset(), f"job:{opv}")


# ── Content op de verkeerde site ───────────────────────────────────────────

class TestContentHoortBijAndereSite:
    """15 aug 2026: 25 stukken over Bijeen, Pootgelukkig, Liefde voor Iedereen
    en TeambuildingMetImpact stonden in de Wachtrij van WeAreImpact, doordat
    `publish_to_weareimpact` bij een onherleidbaar project stil terugviel op de
    eerste site die er ooit was. Twee ervan gingen écht live.
    """

    def _site_met_profiel(self, naam, profiel, koppen=()):
        sid = _site(naam)
        with get_conn() as c:
            c.execute("UPDATE sites SET profile = ? WHERE id = ?", (profiel, sid))
        for k in koppen:
            _job(sid, titel=k, status="published")
        return sid

    def _in_review(self, site_id, titel):
        jid = _job(site_id, titel=titel, status="pending_review")
        return jid

    def test_stuk_van_een_ander_project_wordt_gevonden(self):
        wai = self._site_met_profiel(
            "AuditImpact", "AI-consultant voor gemeenten en welzijnsorganisaties",
            ["Kunstmatige intelligentie in het sociaal domein"])
        self._site_met_profiel(
            "AuditHonden", "Alles over honden adopteren, puppy opvoeden en dierenwelzijn",
            ["Puppy opvoeden: de eerste weken", "Hond adopteren uit het asiel"])

        jid = self._in_review(wai, "Advies hond adopteren: puppy opvoeden stap voor stap")
        bev = _over(ig._check_content_hoort_bij_andere_site(), f"job:{jid}")
        assert bev, "een hondenartikel in de AI-Wachtrij hoort gevonden te worden"
        assert "AuditHonden" in bev[0].detail

    def test_eigen_onderwerp_blijft_met_rust(self):
        """Een nieuw onderwerp op de eigen site moet gewoon mogen."""
        wai = self._site_met_profiel(
            "AuditImpact2", "AI-consultant voor gemeenten en welzijnsorganisaties",
            ["Kunstmatige intelligentie in het sociaal domein"])
        self._site_met_profiel("AuditHonden2", "Honden adopteren en puppy opvoeden")

        jid = self._in_review(wai, "Kunstmatige intelligentie bij gemeenten: waar begin je")
        assert not _over(ig._check_content_hoort_bij_andere_site(), f"job:{jid}")

    def test_nipt_verschil_slaat_niet_aan(self):
        """Alleen een duidelijke winnaar telt — anders wordt elk raakvlak een fout."""
        a = self._site_met_profiel("AuditAlfa", "vrijwilligers werven welzijn gemeente",
                                   ["Vrijwilligers werven in je gemeente"])
        self._site_met_profiel("AuditBeta", "vrijwilligers behouden welzijn organisatie",
                               ["Vrijwilligers behouden in je organisatie"])
        jid = self._in_review(a, "Vrijwilligers werven en behouden bij welzijn")
        assert not _over(ig._check_content_hoort_bij_andere_site(), f"job:{jid}")

    def test_te_korte_titel_levert_geen_uitspraak(self):
        a = self._site_met_profiel("AuditGamma", "vrijwilligers werven welzijn")
        self._site_met_profiel("AuditDelta", "honden adopteren puppy opvoeden asiel")
        jid = self._in_review(a, "Puppy asiel")
        assert not _over(ig._check_content_hoort_bij_andere_site(), f"job:{jid}")
