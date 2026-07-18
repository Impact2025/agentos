"""Linkbuilding: funnel-boekhouding, e-mailguard, review-gate en link-monitor."""
import uuid

import pytest


def _seed_site(conn, site_id="lbsite", name="LB Testsite", base_url="https://lbtest.nl"):
    conn.execute(
        "INSERT OR REPLACE INTO sites (id, name, base_url, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (site_id, name, base_url),
    )
    conn.commit()


@pytest.fixture()
def lb_clean(conn, clean_tables):
    yield
    from backend.shared.database import get_conn
    with get_conn() as c:
        for t in ("link_placements", "link_prospects", "sites"):
            c.execute(f"DELETE FROM {t}")


# ── E-mailguard: bewust anders dan de acquisitie ──────────────────────────────

def test_email_ok_accepteert_redactie_adressen(conn, lb_clean):
    """info@/redactie@ is bij linkbuilding juist de doelgroep — moet door de guard."""
    from backend.domains.linkbuilding.outreach import email_ok
    assert email_ok("info@blogsite.nl")[0]
    assert email_ok("redactie@vakblad.nl")[0]


def test_email_ok_weigert_systeem_en_eigen_adressen(conn, lb_clean):
    from backend.domains.linkbuilding.outreach import email_ok
    _seed_site(conn)
    ok, why = email_ok("noreply@blogsite.nl")
    assert not ok and "systeem" in why
    ok, why = email_ok("redactie@lbtest.nl")  # eigen site uit de sites-tabel
    assert not ok and "eigen domein" in why
    assert not email_ok("redactie@weareimpact.nl")[0]
    assert not email_ok("geen-adres")[0]
    assert not email_ok("x@voorbeeld.nl")[0]


# ── Funnel: eenmalige tijdstempels ────────────────────────────────────────────

def test_advance_prospect_stempelt_eenmalig(conn, lb_clean):
    from backend.domains.linkbuilding import service
    _seed_site(conn)
    p = service.create_prospect("lbsite", {"url": "https://www.Partner.nl/blog",
                                           "status": "qualified"})
    assert p and p["domain"] == "partner.nl"  # genormaliseerd, zonder www

    p = service.advance_prospect(p["id"], "contacted")
    first_ts = p["contacted_at"]
    assert first_ts != ""
    # Terug- en weer vooruitzetten mag de tijdstempel niet overschrijven.
    service.advance_prospect(p["id"], "qualified")
    p = service.advance_prospect(p["id"], "contacted")
    assert p["contacted_at"] == first_ts

    with pytest.raises(ValueError):
        service.advance_prospect(p["id"], "gewonnen")


def test_create_prospect_dedupet_op_domein(conn, lb_clean):
    from backend.domains.linkbuilding import service
    _seed_site(conn)
    assert service.create_prospect("lbsite", {"url": "https://partner.nl/a"})
    assert service.create_prospect("lbsite", {"url": "https://www.partner.nl/b"}) is None


# ── Batch-selectie: alleen bruikbare kandidaten in review ─────────────────────

def test_select_batch_guards(conn, lb_clean):
    from backend.domains.linkbuilding import service
    from backend.domains.linkbuilding.outreach import select_batch
    _seed_site(conn)
    service.create_prospect("lbsite", {
        "url": "https://goed.nl", "status": "qualified",
        "contact_email": "redactie@goed.nl", "relevance_score": 80,
    })
    service.create_prospect("lbsite", {  # geen e-mail → niet in de batch
        "url": "https://zonder-mail.nl", "status": "qualified", "relevance_score": 95,
    })
    service.create_prospect("lbsite", {  # systeem-adres → niet in de batch
        "url": "https://noreply.nl", "status": "qualified",
        "contact_email": "noreply@noreply.nl", "relevance_score": 90,
    })
    batch = select_batch(10)
    assert [p["domain"] for p in batch] == ["goed.nl"]


@pytest.mark.asyncio
async def test_prepare_batch_zet_review_en_placement_klaar(conn, lb_clean, monkeypatch):
    """Concept → outreach_review + pending placement; er vertrekt niets."""
    from backend.domains.linkbuilding import outreach, service
    from backend.shared.database import get_conn
    _seed_site(conn)
    p = service.create_prospect("lbsite", {
        "url": "https://partner.nl/bronnen", "status": "qualified",
        "contact_email": "redactie@partner.nl", "relevance_score": 85,
        "target_url": "https://lbtest.nl/artikel", "anchor_text": "handige gids",
    })

    async def fake_draft(prospect, site):
        return {"subject": "Suggestie voor jullie bronnenpagina",
                "body": "x" * 100}
    monkeypatch.setattr(outreach, "draft_outreach", fake_draft)

    report = await outreach.prepare_linkbuilding_batch()
    assert report["drafted"] == 1

    updated = service.get_prospect(p["id"])
    assert updated["status"] == "outreach_review"
    assert updated["outreach_subject"]
    with get_conn() as c:
        pl = c.execute("SELECT * FROM link_placements WHERE prospect_id = ?",
                       (p["id"],)).fetchone()
    assert pl and pl["status"] == "pending"
    assert pl["target_url"] == "https://lbtest.nl/artikel"


# ── Monitor: linkdetectie en verlies-alarm ────────────────────────────────────

def test_find_link_in_html_detecteert_anker_en_rel():
    from backend.domains.linkbuilding.monitor import find_link_in_html
    html = (
        '<html><body>'
        '<a href="https://elders.nl/">elders</a>'
        '<a href="https://www.lbtest.nl/artikel" rel="nofollow">handige gids</a>'
        '</body></html>'
    )
    hit = find_link_in_html(html, "https://lbtest.nl/artikel")
    assert hit and hit["anchor"] == "handige gids" and hit["rel"] == "nofollow"
    # Domein-match telt ook als de exacte pagina er niet tussen staat.
    assert find_link_in_html(html, "https://lbtest.nl/anders") is not None
    assert find_link_in_html(html, "https://nergens.nl") is None


def test_monitor_zet_placement_live_en_prospect_link_live(conn, lb_clean, monkeypatch):
    from backend.domains.linkbuilding import monitor, service
    from backend.shared.database import get_conn
    _seed_site(conn)
    p = service.create_prospect("lbsite", {
        "url": "https://partner.nl/bronnen", "status": "qualified",
        "contact_email": "redactie@partner.nl",
        "target_url": "https://lbtest.nl/artikel", "anchor_text": "gids",
    })
    service.advance_prospect(p["id"], "contacted")
    with get_conn() as c:
        c.execute(
            "INSERT INTO link_placements (id, prospect_id, site_id, source_url, "
            "target_url, status, created_at, updated_at) "
            "VALUES (?, ?, 'lbsite', 'https://partner.nl/bronnen', "
            "'https://lbtest.nl/artikel', 'pending', datetime('now'), datetime('now'))",
            (str(uuid.uuid4()), p["id"]),
        )

    monkeypatch.setattr(
        monitor, "_fetch",
        lambda url: '<a href="https://lbtest.nl/artikel">gids</a>',
    )
    report = monitor.check_placements()
    assert report["pending_checked"] == 1

    with get_conn() as c:
        pl = c.execute("SELECT * FROM link_placements WHERE prospect_id = ?",
                       (p["id"],)).fetchone()
    assert pl["status"] == "live" and pl["first_seen"] != ""
    assert service.get_prospect(p["id"])["status"] == "link_live"


def test_monitor_meldt_verdwenen_link_pas_na_twee_missers(conn, lb_clean, monkeypatch):
    from backend.domains.linkbuilding import monitor, service
    from backend.shared.database import get_conn
    _seed_site(conn)
    p = service.create_prospect("lbsite", {
        "url": "https://partner.nl", "status": "link_live",
        "contact_email": "redactie@partner.nl",
        "target_url": "https://lbtest.nl",
    })
    with get_conn() as c:
        c.execute(
            "INSERT INTO link_placements (id, prospect_id, site_id, source_url, "
            "target_url, status, first_seen, created_at, updated_at) "
            "VALUES (?, ?, 'lbsite', 'https://partner.nl', 'https://lbtest.nl', "
            "'live', datetime('now'), datetime('now'), datetime('now'))",
            (str(uuid.uuid4()), p["id"]),
        )
    monkeypatch.setattr(monitor, "_fetch", lambda url: "<html>geen links</html>")

    monitor.check_placements()  # eerste misser: nog geen alarm
    with get_conn() as c:
        pl = c.execute("SELECT * FROM link_placements WHERE prospect_id = ?",
                       (p["id"],)).fetchone()
    assert pl["status"] == "live" and pl["check_fails"] == 1

    monitor.check_placements()  # tweede misser: lost + error-kaart
    with get_conn() as c:
        pl = c.execute("SELECT * FROM link_placements WHERE prospect_id = ?",
                       (p["id"],)).fetchone()
        err = c.execute(
            "SELECT 1 FROM activity_log WHERE action='link_lost' AND status='error'"
        ).fetchone()
    assert pl["status"] == "lost"
    assert err is not None


# ── Fase 3: Iris-actie en knelpunt-detectie ───────────────────────────────────

@pytest.mark.asyncio
async def test_iris_linkbuilding_run_klemt_en_dedupet(conn, lb_clean, monkeypatch):
    from backend.domains.iris import actions
    from backend.domains.linkbuilding import outreach as lb_outreach
    from backend.shared.database import get_conn

    calls = []

    async def fake_batch(count=0, site_id=""):
        calls.append(count)
        return {"drafted": count, "skipped": 0, "prospects": []}
    monkeypatch.setattr(lb_outreach, "prepare_linkbuilding_batch", fake_batch)

    done = await actions.linkbuilding_run(99, "SEO-hefboom")
    assert done and "concept(en) klaargezet" in done
    # Klem: nooit meer dan het maximum (10), ook al vraagt de LLM om 99.
    assert calls == [10]
    with get_conn() as c:
        row = c.execute(
            "SELECT artifact FROM activity_log WHERE action='iris_actie' "
            "AND project='Linkbuilding'"
        ).fetchone()
    assert row and "linkbuilding/funnel" in row["artifact"]

    # Dedupe: tweede run dezelfde dag → benigne skip, geen tweede batch.
    again = await actions.linkbuilding_run(5, "nogmaals")
    assert again and "draaide vandaag al" in again
    assert len(calls) == 1


def test_bottleneck_linkkansen_die_liggen():
    from backend.domains.iris import metrics
    snap = {
        "projects": [],
        "global": {
            "linkbuilding": {"by_status": {"qualified": 5, "outreach_review": 0}},
            "funnel": {}, "inputs_7d": {},
        },
    }
    hits = [b for b in metrics.bottlenecks(snap) if b["issue"] == "linkkansen_liggen"]
    assert len(hits) == 1
    sug = hits[0]["suggestion"]
    assert sug["type"] == "linkbuilding_run" and sug["payload"]["aantal"] == 5

    # Staan er al concepten in review, dan is er geen knelpunt.
    snap["global"]["linkbuilding"]["by_status"]["outreach_review"] = 2
    assert not [b for b in metrics.bottlenecks(snap) if b["issue"] == "linkkansen_liggen"]


def test_seo_pillar_telt_live_backlinks(conn, lb_clean):
    from backend.domains.iris.metrics import _seo_pillar
    from backend.domains.linkbuilding import service
    from backend.shared.database import get_conn
    _seed_site(conn)
    p = service.create_prospect("lbsite", {"url": "https://partner.nl",
                                           "status": "link_live"})
    with get_conn() as c:
        c.execute(
            "INSERT INTO link_placements (id, prospect_id, site_id, source_url, "
            "target_url, rel, status, created_at, updated_at) "
            "VALUES (?, ?, 'lbsite', 'https://partner.nl', 'https://lbtest.nl', "
            "'', 'live', datetime('now'), datetime('now'))",
            (str(uuid.uuid4()), p["id"]),
        )
        pillar = _seo_pillar(c, "lbsite", gsc_configured=False)
    assert pillar["backlinks_live"] == 1
    assert pillar["backlinks_dofollow"] == 1


def test_funnel_stats_formule(conn, lb_clean):
    from backend.domains.linkbuilding import service
    _seed_site(conn)
    for i, dom in enumerate(("a.nl", "b.nl", "c.nl")):
        p = service.create_prospect("lbsite", {"url": f"https://{dom}",
                                               "status": "qualified"})
        service.advance_prospect(p["id"], "contacted")
        if i == 0:
            service.advance_prospect(p["id"], "link_live")
    stats = service.funnel_stats("lbsite")
    assert stats["reached"]["contacted"] == 3
    assert stats["reached"]["link_live"] == 1
    assert "3 verstuurde mails → 1 link live" in stats["formula"]
