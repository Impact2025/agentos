"""Seed-demo content voor het Bewaard voor Jou Verkoop-dashboard.

De echte sync (`backend/domains/orders/service.py`) leest bestellingen
verbatim over uit life-journey-backend. Bij de laatste sync bleek dat
`price_paid` voor alle PAID-orders 0 binnenkomt (upstream databug: het
dashboard toonde 21 betaalde orders voor EUR0,00) - dat is een apart, echt
probleem en dit script lost het niet op.

Dit script vult het Verkoop-dashboard voor een demo met nieuwe, duidelijk
gemarkeerde orders (id-prefix 'demo-seed-') die WEL een correcte
price_paid dragen, zodat de KPI's, de omzettrend, de pakket-mix en de
fulfillment-doorlooptijd iets tonen om te laten zien. Idempotent: een
her-run verwijdert eerst alle rijen met dat prefix en zet ze opnieuw neer.

Gebruik:
  cd D:/APPS/agentos
  .venv/Scripts/python.exe seed_demo_orders_bvj.py           # seed/ herzet demo-orders
  .venv/Scripts/python.exe seed_demo_orders_bvj.py --clean   # alleen demo-orders verwijderen
"""
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

from backend.domains.orders.models import ensure_schema
from backend.shared.database import get_conn, init_db

DEMO_PREFIX = "demo-seed-"

# Prijzen in eurocenten, gebaseerd op wat er al in de echte (betaalde)
# BewaardVoorJou-orders staat.
PACKAGES = {
    "VERHAAL": 8900,
    "BABY_GIFT": 5900,
    "BEGIN": 10900,
    "DIGITAAL": 5900,
    "ERFGOED": 16900,
}

NAMES = [
    "Anouk Dekker", "Piet Jansen", "Marieke van den Berg", "Thomas de Vries",
    "Sarah Willems", "Jan Bakker", "Lotte Smit", "Rick Verhoeven",
    "Femke de Boer", "Daan Mulder", "Iris Peters", "Bram Hendriks",
    "Noa Visser", "Sanne Kok", "Milan de Jong", "Eva Vermeer",
    "Sem Bakker", "Julia Willemsen", "Lars Groot", "Fleur Dijkstra",
]

PROMO_CODES = ["", "", "", "", "WELKOM10", "LENTE2026"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def clean(conn) -> int:
    cur = conn.execute("DELETE FROM bvj_orders WHERE id LIKE ?", (DEMO_PREFIX + "%",))
    return cur.rowcount


def seed(conn) -> None:
    rng = random.Random(42)  # vaste seed: elke run levert dezelfde demo op
    now = datetime.now(timezone.utc)
    n_paid, n_pending, n_cancelled = 22, 6, 2

    def make(status: str, days_ago_range, fulfilled: bool):
        package = rng.choice(list(PACKAGES.keys()))
        price = PACKAGES[package]
        promo = rng.choice(PROMO_CODES)
        discount = 1000 if promo else 0
        created = now - timedelta(days=rng.uniform(*days_ago_range),
                                   hours=rng.uniform(0, 23))
        paid_at = ""
        fulfilled_at = ""
        usb_burned_at = ""
        if status in ("PAID", "FULFILLED"):
            paid_at = _iso(created + timedelta(hours=rng.uniform(0.1, 6)))
        if status == "FULFILLED" or (status == "PAID" and fulfilled):
            fulfilled_at = _iso(created + timedelta(days=rng.uniform(1, 4)))
            if package != "VERHAAL" and package != "DIGITAAL":
                usb_burned_at = fulfilled_at
        return (
            DEMO_PREFIX + uuid.uuid4().hex[:12],
            status, package, json.dumps([]),
            price - discount, discount, promo,
            rng.choice(NAMES),
            _iso(created), paid_at, fulfilled_at, usb_burned_at,
            _iso(now),
        )

    rows = []
    for i in range(n_paid):
        rows.append(make("PAID", (0.5, 28), fulfilled=(i % 3 == 0)))
    for _ in range(n_pending):
        rows.append(make("PENDING", (0.1, 3), fulfilled=False))
    for _ in range(n_cancelled):
        r = make("CANCELLED", (2, 20), fulfilled=False)
        rows.append(r)

    conn.executemany(
        """INSERT INTO bvj_orders
           (id, status, package_type, addons, price_paid, discount_cents,
            promo_code_used, recipient_name, created_at, paid_at,
            fulfilled_at, usb_burned_at, synced_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    print(f"[OK] {len(rows)} demo-orders geschreven "
          f"({n_paid} PAID, {n_pending} PENDING, {n_cancelled} CANCELLED)")


def main() -> None:
    init_db()
    ensure_schema()
    with get_conn() as conn:
        removed = clean(conn)
        if removed:
            print(f"[CLEAN] {removed} eerdere demo-orders verwijderd")
        if "--clean" in sys.argv:
            conn.commit()
            print("[DONE] Alleen opgeruimd, geen nieuwe demo-orders gezet.")
            return
        seed(conn)
        conn.commit()
        totals = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price_paid),0) AS omzet "
            "FROM bvj_orders WHERE status IN ('PAID','FULFILLED')"
        ).fetchone()
        print(f"[DONE] Verkoop-dashboard toont nu {totals['n']} betaalde orders "
              f"voor EUR{totals['omzet']/100:.2f} (incl. je echte data).")
        print("       Opruimen na de demo: "
              ".venv/Scripts/python.exe seed_demo_orders_bvj.py --clean")


if __name__ == "__main__":
    main()
