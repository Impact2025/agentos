"""Dagbesteding-fulfillmentflow (Bewaard voor Jou): versturen -> verzonden,
en het effect daarvan op de voorraadberekening.
"""
import json
from datetime import datetime, timezone

import pytest

from backend.domains.orders import fulfillment, procurement
from backend.domains.orders.models import ensure_schema
from backend.shared.database import get_conn


def _insert_order(order_id, status="PAID", package_type="ERFGOED",
                   dagbesteding_sent_at="", shipped_at="", paid_at=None):
    ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bvj_orders
               (id, status, package_type, addons, price_paid, discount_cents,
                promo_code_used, recipient_name, recipient_relation,
                card_message, personal_message, shipping_address,
                gift_card_code, created_at, paid_at, fulfilled_at,
                usb_burned_at, dagbesteding_sent_at, shipped_at, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order_id, status, package_type, json.dumps([]), 14900, 0, "",
                "Test Ontvanger", "oma", "Lieve oma, ...", "",
                json.dumps({"full_name": "Test Ontvanger", "street": "Teststraat",
                            "house_number": "1", "postal_code": "1234 AB",
                            "city": "Teststad", "country": "NL"}),
                "", now, paid_at or now, "", "", dagbesteding_sent_at, shipped_at, now,
            ),
        )


@pytest.fixture(autouse=True)
def _clean_orders():
    ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM bvj_orders WHERE id LIKE 'test-order-%'")
        conn.execute("DELETE FROM bvj_stock")
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM bvj_orders WHERE id LIKE 'test-order-%'")
        conn.execute("DELETE FROM bvj_stock")


def test_materiaal_bevat_sticker_en_kaartjestekst():
    order = {
        "package_type": "ERFGOED",
        "recipient_name": "Riet",
        "card_message": "Lieve oma",
        "personal_message": "",
        "shipping_address": {"full_name": "Riet Dijkstra", "street": "Kerkstraat",
                              "house_number": "12", "postal_code": "2011 AB",
                              "city": "Haarlem", "country": "NL"},
    }
    m = fulfillment.materiaal(order)
    assert "Riet Dijkstra" in m["sticker"]
    assert "Kerkstraat 12" in m["sticker"]
    assert "2011 AB Haarlem" in m["sticker"]
    assert m["kaartje"] == "Lieve oma"


def test_send_to_dagbesteding_zet_timestamp_en_is_niet_dubbel_te_doen():
    _insert_order("test-order-a")
    result = fulfillment.send_to_dagbesteding("test-order-a")
    assert result["order"]["dagbesteding_sent_at"]

    with pytest.raises(ValueError):
        fulfillment.send_to_dagbesteding("test-order-a")


def test_mark_shipped_vergt_eerst_dagbesteding_verstuurd():
    _insert_order("test-order-b")
    with pytest.raises(ValueError):
        fulfillment.mark_shipped("test-order-b")

    fulfillment.send_to_dagbesteding("test-order-b")
    result = fulfillment.mark_shipped("test-order-b")
    assert result["order"]["shipped_at"]

    with pytest.raises(ValueError):
        fulfillment.mark_shipped("test-order-b")


def test_demand_telt_order_niet_mee_zodra_naar_dagbesteding_gestuurd():
    _insert_order("test-order-c")
    need_before = procurement.demand()
    assert need_before.get("usb", 0) >= 1
    assert need_before.get("giftbox", 0) >= 1

    fulfillment.send_to_dagbesteding("test-order-c")
    need_after = procurement.demand()
    # Deze order draagt niet meer bij aan de vraag; er kunnen nog andere
    # openstaande orders in de (gedeelde) testdatabase zitten, dus vergelijk
    # relatief i.p.v. op een absolute 0.
    assert need_after.get("usb", 0) < need_before.get("usb", 0)
    assert need_after.get("giftbox", 0) < need_before.get("giftbox", 0)
