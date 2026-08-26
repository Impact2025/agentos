"""Offertes — geen e-sign-koppeling (geen provider beschikbaar), dus wordt
'geaccepteerd'/'afgewezen' altijd handmatig gezet. Bedragen komen nooit uit
een LLM: create_quote valideert wat er met de hand wordt aangeleverd."""
from datetime import datetime, timedelta, timezone

import pytest


def test_create_quote_rejects_empty_items(clean_tables):
    from backend.domains.quotes import service as quotes
    with pytest.raises(ValueError):
        quotes.create_quote("Klant BV", "Testofferte", [])


def test_create_quote_rejects_invalid_price(clean_tables):
    from backend.domains.quotes import service as quotes
    with pytest.raises(ValueError):
        quotes.create_quote("Klant BV", "Testofferte", [
            {"description": "Advies", "quantity": 1, "unit_price_cents": -100},
        ])


def test_create_quote_computes_totals(clean_tables):
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant BV", "AI-strategietraject", [
        {"description": "Strategiesessie", "quantity": 2, "unit_price_cents": 50000},
        {"description": "Rapportage", "quantity": 1, "unit_price_cents": 25000},
    ], vat_percent=21)
    assert quote["subtotal_cents"] == 125000
    assert quote["vat_cents"] == round(125000 * 0.21)
    assert quote["total_cents"] == quote["subtotal_cents"] + quote["vat_cents"]
    assert quote["status"] == "concept"


def test_delete_quote_only_allowed_for_concept(clean_tables):
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant BV", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ])
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE quotes SET status = 'verstuurd' WHERE id = ?", (quote["id"],))
    with pytest.raises(ValueError):
        quotes.delete_quote(quote["id"])


def test_send_quote_requires_email(clean_tables):
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant BV", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ])
    with pytest.raises(ValueError, match="e-mailadres"):
        quotes.send_quote(quote["id"])


def test_send_quote_fails_loudly_without_resend_config(clean_tables):
    """RESEND_API_KEY staat in de testomgeving expliciet leeg (conftest) —
    send_quote moet dat als fout teruggeven, nooit stil 'gelukt' claimen."""
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant BV", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ], client_email="klant@bedrijf.nl")
    with pytest.raises(ValueError, match="Versturen mislukt"):
        quotes.send_quote(quote["id"])


def test_markeer_beslissing_rejects_unknown_status(clean_tables):
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant BV", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ])
    with pytest.raises(ValueError):
        quotes.markeer_beslissing(quote["id"], "misschien")


def test_markeer_beslissing_geaccepteerd_advances_linked_deal(clean_tables):
    from backend.domains.quotes import service as quotes
    from backend.domains.crm import service as crm

    company = crm.create_company("DealBedrijf")
    deal = crm.create_deal(company["id"], "Testdeal", stage="voorstel", probability=50)
    quote = quotes.create_quote("DealBedrijf", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ], deal_id=deal["id"])

    updated = quotes.markeer_beslissing(quote["id"], "geaccepteerd")
    assert updated["status"] == "geaccepteerd"
    assert updated["decided_at"]

    herladen_deal = crm.get_deal(deal["id"])
    assert herladen_deal["stage"] == "gewonnen"


def test_render_quote_html_contains_client_and_total(clean_tables):
    from backend.domains.quotes import service as quotes
    quote = quotes.create_quote("Klant B.V.", "AI-scan", [
        {"description": "AI-scan", "quantity": 1, "unit_price_cents": 150000},
    ])
    html = quotes.render_quote_html(quote)
    assert "Klant B.V." in html
    assert "AI-scan" in html
    assert f"{quote['total_cents'] / 100:,.2f}" in html


def test_offerte_zonder_beslissing_invariant(clean_tables):
    from backend.domains.quotes import service as quotes
    from backend.domains.iris import integrity
    from backend.shared.database import get_conn

    quote = quotes.create_quote("StilBedrijf", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ])
    oud = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'verstuurd', sent_at = ? WHERE id = ?",
            (oud, quote["id"]),
        )
    bevindingen = integrity._check_offerte_zonder_beslissing()
    assert any(b.subject == f"quote:{quote['id']}" for b in bevindingen)


def test_offerte_recent_verstuurd_geen_bevinding(clean_tables):
    from backend.domains.quotes import service as quotes
    from backend.domains.iris import integrity

    quote = quotes.create_quote("VersBedrijf", "Test", [
        {"description": "Iets", "quantity": 1, "unit_price_cents": 1000},
    ], client_email="klant@bedrijf.nl")
    from backend.shared.database import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'verstuurd', sent_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), quote["id"]),
        )
    bevindingen = integrity._check_offerte_zonder_beslissing()
    assert not any(b.subject == f"quote:{quote['id']}" for b in bevindingen)
