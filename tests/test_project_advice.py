"""Demping van cijfer-gedreven dashboard-alerts (positie/CTR).

GSC-cijfers reageren dagen later op werk. Zonder demping blijft "positie te
laag" / "CTR is laag" bovenaan het dashboard staan terwijl er al een doel voor
loopt — precies wat 'Oplossen' zinloos zou maken.
"""
from datetime import datetime, timedelta, timezone

from backend.domains.projects.router import _goal_addresses


def _goal(objective, status="running", days_ago=0, title=""):
    return {
        "objective": objective,
        "title": title or ("Actiepunt: " + objective[:60]),
        "status": status,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    }


def test_recent_running_goal_dempt_alert():
    goals = [_goal("Verbeter de CTR van WeAreImpact door meta descriptions en titels te herschrijven")]
    assert _goal_addresses(goals, "Verbeter de CTR van WeAreImpact")


def test_afgerond_doel_dempt_ook():
    goals = [_goal("Optimaliseer de bestaande content van WeAreImpact voor betere zoekposities",
                   status="completed", days_ago=3)]
    assert _goal_addresses(goals, "Optimaliseer de bestaande content van WeAreImpact")


def test_mislukt_doel_dempt_niet():
    goals = [_goal("Verbeter de CTR van WeAreImpact door meta descriptions", status="failed")]
    assert not _goal_addresses(goals, "Verbeter de CTR van WeAreImpact")


def test_oud_doel_dempt_niet_meer():
    goals = [_goal("Verbeter de CTR van WeAreImpact door meta descriptions",
                   status="completed", days_ago=20)]
    assert not _goal_addresses(goals, "Verbeter de CTR van WeAreImpact")


def test_ander_project_of_onderwerp_dempt_niet():
    goals = [_goal("Verbeter de CTR van Bijeen door meta descriptions")]
    assert not _goal_addresses(goals, "Verbeter de CTR van WeAreImpact")


def test_kapotte_created_at_valt_stil_weg():
    goals = [{"objective": "Verbeter de CTR van WeAreImpact", "title": "", "status": "running",
              "created_at": "geen-datum"}]
    assert not _goal_addresses(goals, "Verbeter de CTR van WeAreImpact")


def test_meerdere_zinsdelen_een_match_volstaat():
    goals = [_goal("Werk de striking-distance zoekwoorden van WeAreImpact bij (posities 10-20)")]
    assert _goal_addresses(
        goals,
        "Optimaliseer de bestaande content van WeAreImpact",
        "Werk de striking-distance zoekwoorden van WeAreImpact",
    )
