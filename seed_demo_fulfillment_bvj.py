"""Seed-demo voor de dagbesteding-fulfillmentflow (Bewaard voor Jou).

Zet twee volledige ERFGOED-orders neer (adres, kaartjestekst, alles) om de
hele flow te kunnen laten zien in de Verkoop-tab:

  - "erfgoed-a": gisteren betaald, nog NIET naar de dagbesteding gestuurd
    -> knop "Maken & versturen naar dagbesteding" is te klikken (print
       adressticker + kaartjestekst, verbruikt usb/giftbox uit voorraad).
  - "erfgoed-b": vorige week betaald én vorige week al naar de dagbesteding
    gestuurd, nog NIET verzonden
    -> knop "Verzonden door dagbesteding" is te klikken (rondt de order af).

Vult ook de voorraad (usb/giftbox/fotoboek_voucher) zodat de flow niet
meteen vastloopt op een tekort-melding.

Idempotent: id-prefix 'demo-seed-erfgoed-' wordt eerst verwijderd (zelfde
opruim-conventie als seed_demo_orders_bvj.py --clean, die alle
'demo-seed-%'-orders opruimt).

Gebruik:
  cd D:/APPS/agentos
  .venv/Scripts/python.exe seed_demo_fulfillment_bvj.py
"""
import json
from datetime import datetime, timedelta, timezone

from backend.domains.orders.models import ensure_schema
from backend.shared.database import get_conn

ORDERS = [
    {
        "id": "demo-seed-erfgoed-a",
        "status": "PAID",
        "package_type": "ERFGOED",
        "price_paid": 14900,
        "discount_cents": 0,
        "promo_code_used": "",
        "recipient_name": "Riet Dijkstra",
        "recipient_relation": "oma",
        "card_message": "Lieve oma,\n\nVoor al je verhalen die we nooit willen vergeten.\nDank je voor alles.\n\nLiefs, Sanne & Tom",
        "personal_message": "Lieve oma, dit erfgoedboek is voor jouw levensverhaal — vertel het op je eigen tempo, wij bewaren het voor altijd.",
        "shipping_address": {
            "full_name": "Riet Dijkstra",
            "street": "Kerkstraat",
            "house_number": "12",
            "postal_code": "2011 AB",
            "city": "Haarlem",
            "country": "NL",
        },
        "created_days_ago": 1,
        "paid_hours_after_created": 0.1,
        "dagbesteding_days_ago": None,   # nog niet gestuurd
        "shipped_days_ago": None,
    },
    {
        "id": "demo-seed-erfgoed-b",
        "status": "PAID",
        "package_type": "ERFGOED",
        "price_paid": 14900,
        "discount_cents": 0,
        "promo_code_used": "",
        "recipient_name": "Piet Hendriks",
        "recipient_relation": "vader",
        "card_message": "Lieve pap,\n\nJij hebt altijd de mooiste verhalen verteld aan tafel.\nNu mag jij ze eindelijk opschrijven voor ons allemaal.\n\nLiefs, je kinderen",
        "personal_message": "Voor pap — vertel ons alles wat we nog niet weten.",
        "shipping_address": {
            "full_name": "Piet Hendriks",
            "street": "Molenweg",
            "house_number": "48",
            "postal_code": "3811 BC",
            "city": "Amersfoort",
            "country": "NL",
        },
        "created_days_ago": 8,
        "paid_hours_after_created": 0.2,
        "dagbesteding_days_ago": 7,      # vorige week al gestuurd
        "shipped_days_ago": None,        # vandaag te verzenden
    },
]

STOCK = {"usb": 20, "giftbox": 15, "fotoboek_voucher": 10}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def main() -> None:
    # De draaiende server heeft de DB al geïnitialiseerd; alleen de
    # (idempotente, additieve) orders-schema-migratie is hier nodig.
    ensure_schema()
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        removed = conn.execute(
            "DELETE FROM bvj_orders WHERE id LIKE 'demo-seed-erfgoed-%'"
        ).rowcount
        if removed:
            print(f"[CLEAN] {removed} eerdere demo-erfgoed-orders verwijderd")

        for o in ORDERS:
            created = now - timedelta(days=o["created_days_ago"], hours=2)
            paid = created + timedelta(hours=o["paid_hours_after_created"])
            dagbesteding = (
                now - timedelta(days=o["dagbesteding_days_ago"], hours=3)
                if o["dagbesteding_days_ago"] is not None else None
            )
            shipped = (
                now - timedelta(days=o["shipped_days_ago"])
                if o["shipped_days_ago"] is not None else None
            )
            conn.execute(
                """INSERT INTO bvj_orders
                   (id, status, package_type, addons, price_paid, discount_cents,
                    promo_code_used, recipient_name, recipient_relation,
                    card_message, personal_message, shipping_address,
                    gift_card_code, created_at, paid_at, fulfilled_at,
                    usb_burned_at, dagbesteding_sent_at, shipped_at, synced_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    o["id"], o["status"], o["package_type"], json.dumps([]),
                    o["price_paid"], o["discount_cents"], o["promo_code_used"],
                    o["recipient_name"], o["recipient_relation"],
                    o["card_message"], o["personal_message"],
                    json.dumps(o["shipping_address"]), "",
                    _iso(created), _iso(paid), "", "",
                    _iso(dagbesteding) if dagbesteding else "",
                    _iso(shipped) if shipped else "",
                    _iso(now),
                ),
            )
            print(f"[OK] {o['id']} ({o['recipient_name']}) "
                  f"{'al bij de dagbesteding' if dagbesteding else 'wacht op versturen'}")

        for item, qty in STOCK.items():
            conn.execute(
                """INSERT INTO bvj_stock (item, on_hand, updated_at, updated_by)
                   VALUES (?, ?, ?, 'demo-seed')
                   ON CONFLICT(item) DO UPDATE SET on_hand=excluded.on_hand, updated_at=excluded.updated_at""",
                (item, qty, _iso(now)),
            )
        print(f"[OK] voorraad gezet: {STOCK}")

        conn.commit()

    print("[DONE] Ga naar de Verkoop-tab: order van Riet Dijkstra (gisteren) heeft de knop "
          "'Maken & versturen naar dagbesteding'; order van Piet Hendriks (vorige week, al bij "
          "de dagbesteding) heeft de knop 'Verzonden door dagbesteding'.")
    print("       Opruimen: DELETE FROM bvj_orders WHERE id LIKE 'demo-seed-erfgoed-%' "
          "(of her-run seed_demo_orders_bvj.py --clean, die ruimt alle demo-orders op).")


if __name__ == "__main__":
    main()
