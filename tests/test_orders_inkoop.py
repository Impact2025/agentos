"""Inkoopvoorstellen (Bewaard voor Jou): voorstel-dan-klik, nooit automatisch
bestellen. Zie CLAUDE.md: "een order is de meest onomkeerbare verzending die
er is" — dit toetst dat de agent alleen voorstelt en niets zelf uitvoert.
"""
from datetime import datetime, timezone

import pytest

from backend.domains.orders import inkoop, procurement
from backend.domains.orders.models import ensure_schema
from backend.shared.database import get_conn


@pytest.fixture(autouse=True)
def _clean():
    ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM bvj_orders WHERE id LIKE 'test-inkoop-%'")
        conn.execute("DELETE FROM bvj_stock WHERE item = 'test-item'")
        conn.execute("DELETE FROM bvj_stock_thresholds WHERE item = 'test-item'")
        conn.execute("DELETE FROM bvj_purchase_proposals WHERE item = 'test-item'")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM bvj_orders WHERE id LIKE 'test-inkoop-%'")
        conn.execute("DELETE FROM bvj_stock WHERE item = 'test-item'")
        conn.execute("DELETE FROM bvj_stock_thresholds WHERE item = 'test-item'")
        conn.execute("DELETE FROM bvj_purchase_proposals WHERE item = 'test-item'")


def _set_stock(on_hand, min_qty, order_url="", reorder_qty=0, unit_cost_cents=0):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bvj_stock (item, on_hand, order_url, reorder_qty, unit_cost_cents, updated_at, updated_by)
               VALUES ('test-item', ?, ?, ?, ?, ?, 'test')
               ON CONFLICT(item) DO UPDATE SET on_hand=excluded.on_hand, order_url=excluded.order_url,
                 reorder_qty=excluded.reorder_qty, unit_cost_cents=excluded.unit_cost_cents""",
            (on_hand, order_url, reorder_qty, unit_cost_cents, now),
        )
        conn.execute(
            """INSERT INTO bvj_stock_thresholds (item, min_qty) VALUES ('test-item', ?)
               ON CONFLICT(item) DO UPDATE SET min_qty=excluded.min_qty""",
            (min_qty,),
        )


def test_evaluate_maakt_geen_voorstel_als_voorraad_ok_is():
    _set_stock(on_hand=100, min_qty=5)
    created = inkoop.evaluate()
    assert not [c for c in created if c["item"] == "test-item"]


def test_evaluate_maakt_voorstel_bij_tekort_met_geschatte_kosten():
    _set_stock(on_hand=0, min_qty=5, unit_cost_cents=250, reorder_qty=10)
    created = inkoop.evaluate()
    match = [c for c in created if c["item"] == "test-item"]
    assert match

    voorstellen = inkoop.list_open()
    v = next(v for v in voorstellen if v["item"] == "test-item")
    assert v["qty"] == 10
    assert v["estimated_cost_cents"] == 2500
    assert v["status"] == "open"


def test_evaluate_maakt_niet_twee_keer_hetzelfde_voorstel():
    _set_stock(on_hand=0, min_qty=5)
    inkoop.evaluate()
    n_before = len(inkoop.list_open())
    inkoop.evaluate()
    n_after = len(inkoop.list_open())
    assert n_before == n_after == 1


def test_bestellen_sluit_het_voorstel_en_is_niet_dubbel_te_doen():
    _set_stock(on_hand=0, min_qty=5)
    inkoop.evaluate()
    v = next(v for v in inkoop.list_open() if v["item"] == "test-item")

    resolved = inkoop.mark_ordered(v["id"])
    assert resolved["status"] == "besteld"
    assert not [x for x in inkoop.list_open() if x["item"] == "test-item"]

    with pytest.raises(ValueError):
        inkoop.mark_ordered(v["id"])


def test_bestelde_voorraad_blokkeert_een_nieuw_voorstel_binnen_de_cooldown():
    _set_stock(on_hand=0, min_qty=5)
    inkoop.evaluate()
    v = next(v for v in inkoop.list_open() if v["item"] == "test-item")
    inkoop.mark_ordered(v["id"])

    # Tekort blijft bestaan (levering is onderweg) — geen tweede voorstel.
    inkoop.evaluate()
    assert not [x for x in inkoop.list_open() if x["item"] == "test-item"]


def test_negeren_sluit_het_voorstel_af():
    _set_stock(on_hand=0, min_qty=5)
    inkoop.evaluate()
    v = next(v for v in inkoop.list_open() if v["item"] == "test-item")
    resolved = inkoop.dismiss(v["id"])
    assert resolved["status"] == "genegeerd"
    assert not [x for x in inkoop.list_open() if x["item"] == "test-item"]
