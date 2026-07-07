"""Kennisbank: casestudy-CRUD, keyword-matching en site-kennis (profiel/CTA's)."""
import json

import pytest


@pytest.fixture()
def site():
    from backend.domains.seo import sites as sites_service
    s = sites_service.create_site({"name": f"KennisbankTest"})
    yield sites_service.get_site(s["id"])
    sites_service.delete_site(s["id"])  # CASCADE ruimt case_studies mee op


def test_case_study_crud(site):
    from backend.domains.seo import knowledge

    cs = knowledge.create_case_study(site["id"], {
        "title": "Webshop X: +140% organisch verkeer",
        "summary": "SEO-traject van 6 maanden",
        "tags": "seo, webshop",
    })
    assert cs["status"] == "active"
    assert knowledge.get_case_study(cs["id"])["title"].startswith("Webshop X")

    updated = knowledge.update_case_study(cs["id"], {"status": "archived"})
    assert updated["status"] == "archived"
    assert knowledge.list_case_studies(site["id"], status="active") == []
    assert len(knowledge.list_case_studies(site["id"], status=None)) == 1

    assert knowledge.delete_case_study(cs["id"]) is True
    assert knowledge.get_case_study(cs["id"]) is None


def test_create_requires_title(site):
    from backend.domains.seo import knowledge
    with pytest.raises(ValueError):
        knowledge.create_case_study(site["id"], {"title": "  "})


def test_match_prefers_tag_overlap(site):
    from backend.domains.seo import knowledge

    knowledge.create_case_study(site["id"], {
        "title": "Bakkerij Y: lokale vindbaarheid",
        "tags": "lokale seo, google maps",
    })
    hit = knowledge.create_case_study(site["id"], {
        "title": "Webshop X: +140% organisch verkeer",
        "tags": "webshop, linkbuilding",
    })
    best = knowledge.match_case_study(site["id"], "linkbuilding voor webshops")
    assert best["id"] == hit["id"]


def test_match_falls_back_to_most_recent(site):
    from backend.domains.seo import knowledge

    knowledge.create_case_study(site["id"], {"title": "Oudere case", "tags": "iets"})
    newest = knowledge.create_case_study(site["id"], {"title": "Nieuwste case", "tags": "anders"})
    best = knowledge.match_case_study(site["id"], "totaal ongerelateerd onderwerp qqq")
    assert best["id"] == newest["id"]


def test_match_without_studies_is_none(site):
    from backend.domains.seo import knowledge
    assert knowledge.match_case_study(site["id"], "wat dan ook") is None


def test_get_site_knowledge_parses_ctas(site):
    from backend.domains.seo import knowledge, sites as sites_service

    sites_service.update_site(site["id"], {
        "profile": "Keepsake-merk voor 65+",
        "ctas": json.dumps(["Plan een gratis kennismaking → /contact", "  "]),
    })
    k = knowledge.get_site_knowledge(sites_service.get_site(site["id"]))
    assert k["profile"] == "Keepsake-merk voor 65+"
    assert k["ctas"] == ["Plan een gratis kennismaking → /contact"]

    # Kapotte JSON mag nooit een crash geven — gewoon geen CTA's.
    assert knowledge.get_site_knowledge({"ctas": "geen json"})["ctas"] == []


def test_indexnow_key_is_redacted(site):
    """De IndexNow-key is een secret-veld: nooit kaal naar de frontend."""
    from backend.domains.seo import sites as sites_service

    sites_service.update_site(site["id"], {"indexnow_key": "abc123"})
    listed = [s for s in sites_service.list_sites() if s["id"] == site["id"]][0]
    assert "indexnow_key" not in listed
    assert listed["indexnow_key_set"] is True
