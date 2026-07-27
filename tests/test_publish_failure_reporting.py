"""Waarom een publicatie mislukte moet zichtbaar zijn — en een titel is een kop.

Twee dingen die het Actiecentrum onbruikbaar maakten:
  1. de oorzaak stond alleen in `publish_result`, dus elke kaart meldde
     "Onbekende fout";
  2. een job zonder <h1> kreeg het `angle`-veld als titel — een hele alinea.
"""
from backend.domains.publish.content_pipeline import (
    _clean_title, _extract_title, publish_failure_reason)


# ── Titel ───────────────────────────────────────────────────────────────────

def test_h1_wins():
    assert _extract_title("<h1>Relatie verdiepen in 5 stappen</h1><p>x</p>",
                          fallback="iets anders") == "Relatie verdiepen in 5 stappen"


def test_h2_when_no_h1():
    assert _extract_title("<div><h2>Microgewoontes die werken</h2></div>",
                          fallback="iets") == "Microgewoontes die werken"


def test_paragraph_fallback_is_cut_to_a_headline():
    alinea = ("In tegenstelling tot het advies om 'gewoon te praten over je "
              "gevoelens', richt dit artikel zich op concrete, haalbare "
              "microgewoontes. Het legt de nadruk op verbinding.")
    title = _extract_title("<div><p>geen kop</p></div>", fallback=alinea)
    assert len(title) <= 90
    assert "\n" not in title
    assert title.startswith("In tegenstelling tot het advies")


def test_clean_title_strips_html_and_whitespace():
    assert _clean_title("  <em>Zo\n doe je dat</em> ") == "Zo doe je dat"


def test_short_title_untouched():
    assert _clean_title("Ouder inzicht zonder druk") == "Ouder inzicht zonder druk"


# ── Foutoorzaak ─────────────────────────────────────────────────────────────

def test_reason_prefers_live_check():
    reden = publish_failure_reason({
        "live_check": "live-controle: URL gaf HTTP 404",
        "site": {"error": "HTTP 500: iets"}})
    assert reden.startswith("live-controle")


def test_404_is_explained_as_missing_endpoint():
    reden = publish_failure_reason({"site": {"success": False, "error": "HTTP 404: NOT_FOUND"}})
    assert "endpoint" in reden.lower()
    assert "404" in reden


def test_500_is_explained_as_server_error():
    reden = publish_failure_reason(
        {"site": {"success": False, "error": 'HTTP 500: {"error":"Interne fout"}'}})
    assert "serverfout" in reden.lower()


def test_netlify_error_used_when_site_absent():
    assert "kapot" in publish_failure_reason({"netlify": {"error": "kapot"}})


def test_no_result_gives_empty_string():
    assert publish_failure_reason(None) == ""
    assert publish_failure_reason({}) == ""
