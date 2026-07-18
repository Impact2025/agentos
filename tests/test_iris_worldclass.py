"""Tests voor de wereldklasse-upgrade van Iris:

- SEO-pijler meet op gsc_history (page-snapshots) i.p.v. het lege published_pages
- testsites tellen niet mee in de cijfers
- deterministische knelpunt-prioritering (funnel vóór "laagste cijfer")
- JSON-reparatie voor afgekapte/omfencete LLM-antwoorden
- voorspelling-dedupe (één open voorspelling per site/metric/richting)
- regelgebaseerd minimum op terugval-dagen
"""
import json
import uuid

import pytest


def _seed_site(conn, site_id, name, *, gsc="sc-domain:x.nl", is_test=0, auto=1):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, gsc_property, "
        "auto_content_enabled, content_batch_size, is_test, created_at) "
        "VALUES (?, ?, 'https://x.nl', ?, ?, 1, ?, datetime('now'))",
        (site_id, name, gsc, auto, is_test),
    )


def _seed_page_snapshot(conn, site_id, url, date, clicks, impressions, position):
    conn.execute(
        "INSERT OR REPLACE INTO gsc_history (id, site_id, scope, page_url, date, "
        "clicks, impressions, ctr, position, created_at) "
        "VALUES (?, ?, 'page', ?, ?, ?, ?, 0, ?, datetime('now'))",
        (str(uuid.uuid4()), site_id, url, date, clicks, impressions, position),
    )


@pytest.fixture()
def iris_clean(conn, clean_tables):
    yield
    conn.execute("DELETE FROM gsc_history")
    conn.execute("DELETE FROM iris_predictions")
    conn.execute("DELETE FROM sites WHERE id LIKE 'wk-%'")
    conn.commit()


# ── SEO-pijler op gsc_history ────────────────────────────────────────────────

def test_seo_pillar_meet_op_gsc_history(conn, iris_clean):
    from backend.domains.iris import metrics
    _seed_site(conn, "wk-a", "WkSiteA")
    _seed_page_snapshot(conn, "wk-a", "https://x.nl/p1", "2026-07-12", 10, 200, 5.0)
    _seed_page_snapshot(conn, "wk-a", "https://x.nl/p2", "2026-07-12", 0, 50, 25.0)
    conn.commit()
    seo = metrics._seo_pillar(conn, "wk-a", gsc_configured=True)
    assert seo["pages"] == 2
    assert seo["clicks"] == 10
    assert seo["pages_with_clicks"] == 1
    assert seo["score"] > 0, "site met echte GSC-clicks mag geen 0.0 scoren"
    assert seo["note"] == ""


def test_seo_pillar_pakt_nieuwste_snapshotdag(conn, iris_clean):
    from backend.domains.iris import metrics
    _seed_site(conn, "wk-b", "WkSiteB")
    _seed_page_snapshot(conn, "wk-b", "https://x.nl/p1", "2026-07-01", 99, 999, 1.0)
    _seed_page_snapshot(conn, "wk-b", "https://x.nl/p1", "2026-07-12", 3, 100, 12.0)
    conn.commit()
    seo = metrics._seo_pillar(conn, "wk-b", gsc_configured=True)
    assert seo["pages"] == 1
    assert seo["clicks"] == 3, "moet de nieuwste snapshot-dag nemen, niet alles optellen"


def test_seo_pillar_valt_terug_op_published_pages(conn, iris_clean):
    from backend.domains.iris import metrics
    _seed_site(conn, "wk-c", "WkSiteC")
    conn.execute(
        "INSERT OR REPLACE INTO published_pages (id, site_id, slug, title, html, "
        "gsc_clicks, gsc_impressions, gsc_position, created_at, updated_at) "
        "VALUES ('wk-pp1', 'wk-c', 's', 't', '<p>x</p>', 7, 70, 9.0, "
        "datetime('now'), datetime('now'))"
    )
    conn.commit()
    seo = metrics._seo_pillar(conn, "wk-c", gsc_configured=True)
    assert seo["pages"] == 1 and seo["clicks"] == 7
    conn.execute("DELETE FROM published_pages WHERE id = 'wk-pp1'")
    conn.commit()


# ── Testsites uit de ranking ─────────────────────────────────────────────────

def test_testsites_tellen_niet_mee(conn, iris_clean):
    from backend.domains.iris import metrics
    _seed_site(conn, "wk-real", "WkEcht")
    _seed_site(conn, "wk-test", "WkTestSite", is_test=1)
    conn.commit()
    names = {p["name"] for p in metrics._site_projects(conn)}
    assert "WkEcht" in names
    assert "WkTestSite" not in names


# ── Knelpunt-prioritering ────────────────────────────────────────────────────

def _snap(projects=None, funnel_by_status=None, inputs=None, pending=0, failures=None):
    return {
        "projects": projects or [],
        "global": {
            "pending_review_total": pending,
            "scheduler_failures": failures or [],
            "funnel": {"by_status": funnel_by_status or {}},
            "inputs_7d": inputs or {},
        },
    }


def _proj(name, grade=4.0, seo_note="", pages=0, trend=None, live=0, target=8, pending=0):
    return {
        "project": name, "site_id": f"id-{name}", "grade": grade, "score": grade * 10,
        "trend": trend,
        "pillars": {
            "content": {"live_30d": live, "target_30d": target, "pending_review": pending},
            "seo": {"note": seo_note, "pages": pages},
        },
    }


def test_bottlenecks_funnel_droog_gaat_voor_zwakste_project():
    from backend.domains.iris import metrics
    snap = _snap(
        projects=[_proj("Zwak", grade=1.9)],
        funnel_by_status={"new": 4, "enriched": 3},
        inputs={"outreach_target": 50, "outreach_sent": 1, "outreach_drafts_ready": 3},
        pending=3,
    )
    b = metrics.bottlenecks(snap)
    assert b[0]["issue"] == "funnel_droog"
    assert b[0]["suggestion"]["type"] == "outreach_run"
    assert b[0]["suggestion"]["payload"]["aantal"] == 7  # geklemd op voorraad
    issues = [x["issue"] for x in b]
    assert issues.index("funnel_droog") < issues.index("zwakste_project")


def test_bottlenecks_onmeetbaar_project_krijgt_gsc_connect():
    from backend.domains.iris import metrics
    snap = _snap(projects=[_proj("Blind", seo_note="geen GSC-koppeling — niet meetbaar")])
    b = metrics.bottlenecks(snap)
    gsc = [x for x in b if x["issue"] == "onmeetbaar"]
    assert gsc and gsc[0]["suggestion"]["type"] == "gsc_connect"


def test_bottlenecks_zwakste_project_content_run():
    from backend.domains.iris import metrics
    snap = _snap(projects=[_proj("Achterblijver", live=0, target=8, pending=0)])
    b = metrics.bottlenecks(snap)
    weakest = [x for x in b if x["issue"] == "zwakste_project"][0]
    assert weakest["suggestion"]["type"] == "content_run"
    assert weakest["suggestion"]["target"] == "id-Achterblijver"


# ── JSON-reparatie ───────────────────────────────────────────────────────────

def test_extract_json_codefence():
    from backend.domains.iris.service import _extract_json
    raw = 'Hier is je briefing:\n```json\n{"advies": [{"prio": 1}]}\n```\nSucces!'
    assert _extract_json(raw) == {"advies": [{"prio": 1}]}


def test_extract_json_afgekapt_object():
    from backend.domains.iris.service import _extract_json
    raw = '{"oordeel": {"A": "ok"}, "advies": [{"prio": 1, "actie": "doe iets"}, {"prio": 2, "actie": "half afgeka'
    parsed = _extract_json(raw)
    assert parsed is not None, "afgekapte JSON moet gerepareerd worden"
    assert parsed["oordeel"] == {"A": "ok"}
    assert parsed["advies"][0]["actie"] == "doe iets"


def test_extract_json_afgekapt_na_sleutel():
    from backend.domains.iris.service import _extract_json
    raw = '{"geleerd": ["les 1"], "voorspellingen": [{"project": "X", "metric":'
    parsed = _extract_json(raw)
    assert parsed is not None
    assert parsed["geleerd"] == ["les 1"]


def test_extract_json_geneste_structuren_correct_gesloten():
    from backend.domains.iris.service import _extract_json
    raw = '{"a": [1, 2], "b": {"c": [{"d": "e"}], "f": "afgeka'
    parsed = _extract_json(raw)
    assert parsed is not None
    assert parsed["a"] == [1, 2]
    assert parsed["b"]["c"] == [{"d": "e"}]


def test_extract_json_rommel_blijft_none():
    from backend.domains.iris.service import _extract_json
    assert _extract_json("Sorry, ik kan geen JSON geven.") is None


# ── Voorspelling-dedupe ──────────────────────────────────────────────────────

def test_prediction_dedupe_zelfde_site_metric_richting(conn, iris_clean):
    from backend.domains.iris import predictions
    first = predictions.create_prediction(
        report_date="2026-07-13", project="Wk", site_id="wk-a", metric="clicks",
        direction="up", baseline=8.0, statement="Wk haalt 12 clicks")
    assert first is not None
    dup = predictions.create_prediction(
        report_date="2026-07-13", project="Wk", site_id="wk-a", metric="clicks",
        direction="up", baseline=8.0, statement="Wk haalt 12 clicks (nog eens)")
    assert dup is None, "tweede open voorspelling op zelfde site/metric/richting moet geweigerd"
    other = predictions.create_prediction(
        report_date="2026-07-13", project="Wk", site_id="wk-a", metric="position",
        direction="up", baseline=30.0, statement="positie verbetert")
    assert other is not None


# ── Regelgebaseerd minimum op terugval-dagen ─────────────────────────────────

@pytest.mark.asyncio
async def test_rule_based_voert_acties_uit_en_biedt_rest_aan(monkeypatch):
    from backend.domains.iris import service, actions

    async def fake_outreach(count, reason):
        assert "regelgebaseerd" in reason
        return f"Outreach-batch gestart: {count} concept(en)"

    monkeypatch.setattr(actions, "outreach_run", fake_outreach)
    monkeypatch.setattr("backend.shared.outcomes.llm_budget_exceeded", lambda: False)

    snap = _snap(
        projects=[_proj("Blind", seo_note="geen GSC-koppeling")],
        funnel_by_status={"new": 5},
        inputs={"outreach_target": 50, "outreach_sent": 0, "outreach_drafts_ready": 0},
    )
    from backend.domains.iris import metrics
    snap["bottlenecks"] = metrics.bottlenecks(snap)
    applied, leftovers = await service._apply_rule_based(snap)
    assert any("Outreach-batch" in a for a in applied)
    assert any(s["type"] == "gsc_connect" for s in leftovers), \
        "menselijke stap (gsc_connect) hoort als fix-aanbieding terug te komen"


@pytest.mark.asyncio
async def test_rule_based_respecteert_quota_rem(monkeypatch):
    from backend.domains.iris import service

    monkeypatch.setattr("backend.shared.outcomes.llm_budget_exceeded", lambda: True)
    snap = _snap(
        projects=[],
        funnel_by_status={"new": 5},
        inputs={"outreach_target": 50, "outreach_sent": 0, "outreach_drafts_ready": 0},
    )
    from backend.domains.iris import metrics
    snap["bottlenecks"] = metrics.bottlenecks(snap)
    applied, leftovers = await service._apply_rule_based(snap)
    assert applied == []
    assert any(s["type"] == "outreach_run" for s in leftovers), \
        "bij quota-rem wordt de actie een aanbieding i.p.v. een run"


# ── Fix-knop: failed is herkansbaar, SPA-shell brandt de poging niet op ──────

@pytest.mark.asyncio
async def test_apply_failed_is_herkansbaar(conn, monkeypatch):
    from backend.domains.iris import fix as fix_mod
    from backend.domains.iris import actions
    conn.execute(
        "INSERT OR REPLACE INTO iris_suggestions (id, report_date, scope, type, title, "
        "target, payload, status, created_at) VALUES ('wk-retry','2026-07-13','all',"
        "'seo_refresh','test','site-x','{}','approved',datetime('now'))")
    conn.commit()

    async def broken(target, n, reason):
        return None
    monkeypatch.setattr(actions, "seo_refresh", broken)
    r1 = await fix_mod.apply("wk-retry")
    assert not r1["ok"]

    async def works(target, n, reason):
        return "gelukt"
    monkeypatch.setattr(actions, "seo_refresh", works)
    r2 = await fix_mod.apply("wk-retry")
    assert r2["ok"], f"failed hoort herkansbaar te zijn, kreeg: {r2}"
    conn.execute("DELETE FROM iris_suggestions WHERE id='wk-retry'")
    conn.commit()


@pytest.mark.asyncio
async def test_seo_refresh_slaat_shell_over_en_pakt_volgende(monkeypatch, conn, iris_clean):
    """De homepage (SPA-shell) mag de refresh-poging niet opbranden: de
    volgende suggestie moet alsnog verrijkt worden."""
    from backend.domains.iris import actions
    _seed_site(conn, "wk-ref", "WkRefSite")
    conn.commit()

    from backend.domains.seo import optimizer
    monkeypatch.setattr(optimizer, "resolve_site",
                        lambda ref: {"id": "wk-ref", "name": "WkRefSite"})
    sugs = [{"id": "s1", "page": "https://x.nl/", "title": "shell"},
            {"id": "s2", "page": "https://x.nl/artikel", "title": "echt"}]
    monkeypatch.setattr(optimizer, "list_suggestions", lambda *a, **k: sugs)
    monkeypatch.setattr(optimizer, "_update_suggestion", lambda *a, **k: None)

    async def fake_refresh(sug, site):
        if sug["id"] == "s1":
            raise RuntimeError("Kon de huidige pagina-inhoud niet ophalen — refresh niet mogelijk")
        return "job-123"
    monkeypatch.setattr(optimizer, "refresh_article", fake_refresh)

    done = await actions.seo_refresh("wk-ref", 1, "test")
    assert done and "1 wegzakkende" in done, f"volgende suggestie moest verrijkt worden: {done}"


@pytest.mark.asyncio
async def test_seo_refresh_alleen_shells_geeft_nette_uitkomst(monkeypatch, conn, iris_clean):
    from backend.domains.iris import actions
    _seed_site(conn, "wk-ref2", "WkRefSite2")
    conn.commit()
    from backend.domains.seo import optimizer
    monkeypatch.setattr(optimizer, "resolve_site",
                        lambda ref: {"id": "wk-ref2", "name": "WkRefSite2"})
    monkeypatch.setattr(optimizer, "list_suggestions",
                        lambda *a, **k: [{"id": "s1", "page": "https://x.nl/", "title": "shell"}])
    monkeypatch.setattr(optimizer, "_update_suggestion", lambda *a, **k: None)

    async def only_shell(sug, site):
        raise RuntimeError("Kon de huidige pagina-inhoud niet ophalen")
    monkeypatch.setattr(optimizer, "refresh_article", only_shell)

    done = await actions.seo_refresh("wk-ref2", 1, "test")
    assert done and "overgeslagen" in done, \
        f"alleen-shells hoort een nette uitkomst te geven, geen None/400: {done}"
