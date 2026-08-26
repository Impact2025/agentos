"""filter_cross_project_mentions: knelpunten/advies die een ander project noemen
mogen niet in Iris Remote belanden zodra BRIDGE_VISIBLE_PROJECTS is ingesteld.

Aanleiding (26 aug 2026): de Cijfers-tab in Remote toonde "Fix de scheduler —
5 job(s) faalden: Mail helpdesk bewaardvoorjou; Mail helpdesk bijeen; Mail
helpdesk skillkaart" terwijl BRIDGE_VISIBLE_PROJECTS alleen WeAreImpact hoorde
te tonen — dat item draagt geen `project`-veld, dus een filter die alleen op
die kolom let laat de tekst gewoon door.
"""
import backend.shared.config as config
from backend.shared.projects import filter_cross_project_mentions


def test_geen_filter_zonder_bridge_visible_projects(monkeypatch):
    monkeypatch.setattr(config, "BRIDGE_VISIBLE_PROJECTS", "")
    items = [{"actie": "Fix de scheduler — Mail helpdesk bijeen"}]
    assert filter_cross_project_mentions(items) == items


def test_tekst_die_een_ander_project_noemt_sneuvelt(monkeypatch):
    monkeypatch.setattr(config, "BRIDGE_VISIBLE_PROJECTS", "WeAreImpact")
    monkeypatch.setattr(
        "backend.shared.projects._site_namen",
        lambda: {"weareimpact": "WeAreImpact", "bijeen": "Bijeen"},
    )
    items = [
        {"actie": "Fix de scheduler", "waarom": "Mail helpdesk bijeen faalt"},
        {"actie": "Til WeAreImpact omhoog", "waarom": "zwakste project"},
    ]
    out = filter_cross_project_mentions(items)
    assert out == [items[1]]


def test_expliciete_scope_wint_van_tekst(monkeypatch):
    monkeypatch.setattr(config, "BRIDGE_VISIBLE_PROJECTS", "WeAreImpact")
    monkeypatch.setattr(
        "backend.shared.projects._site_namen",
        lambda: {"weareimpact": "WeAreImpact", "bijeen": "Bijeen"},
    )
    items = [
        {"actie": "Schrijf artikel", "suggestion": {"scope": "Bijeen"}},
        {"actie": "Schrijf artikel", "suggestion": {"scope": "WeAreImpact"}},
        {"project": "Bijeen", "actie": "Los iets op"},
    ]
    out = filter_cross_project_mentions(items)
    assert out == [items[1]]


def test_generieke_regel_zonder_projectnaam_blijft_staan(monkeypatch):
    monkeypatch.setattr(config, "BRIDGE_VISIBLE_PROJECTS", "WeAreImpact")
    monkeypatch.setattr(
        "backend.shared.projects._site_namen",
        lambda: {"weareimpact": "WeAreImpact", "bijeen": "Bijeen"},
    )
    items = [{"actie": "Keur de Wachtrij goed", "waarom": "16 stuks wachten"}]
    assert filter_cross_project_mentions(items) == items
