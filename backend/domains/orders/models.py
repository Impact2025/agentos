"""
Bewaard voor Jou — bestellingen + voorraad-schema.

Drie tabellen. `bvj_orders` is een lokale, query-bare cache van de orders uit
life-journey-backend (het "memories"-project) — ververst door `service.sync_once()`,
nooit rechtstreeks beschreven vanuit de UI, want de waarheid over een order
blijft bij memories. `bvj_stock`/`bvj_stock_thresholds` bestaan uitsluitend in
ImpactOS: memories kent geen voorraad- of leveranciersmodel, dus dit is de
enige plek waar op-voorraad/drempel wordt bijgehouden (handmatig door Vincent).

Het schema leeft bewust in dit domein (i.p.v. shared/database.py) zodat het
zelfstandig te verwijderen is. ensure_schema() is idempotent.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS bvj_orders (
    id                TEXT PRIMARY KEY,
    status            TEXT NOT NULL DEFAULT '',
    package_type      TEXT NOT NULL DEFAULT '',
    addons            TEXT NOT NULL DEFAULT '[]',   -- JSON-lijst
    price_paid        INTEGER NOT NULL DEFAULT 0,   -- eurocenten
    discount_cents    INTEGER NOT NULL DEFAULT 0,
    promo_code_used   TEXT DEFAULT '',
    recipient_name    TEXT DEFAULT '',
    created_at        TEXT DEFAULT '',
    paid_at           TEXT DEFAULT '',
    fulfilled_at      TEXT DEFAULT '',
    usb_burned_at     TEXT DEFAULT '',
    synced_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bvj_orders_status ON bvj_orders(status);
CREATE INDEX IF NOT EXISTS idx_bvj_orders_created ON bvj_orders(created_at);

-- Handmatig bijgehouden fysieke voorraad. `item` is de sleutel uit
-- procurement.ITEM_REQUIREMENTS (bijv. 'usb', 'giftbox', 'fotoboek').
CREATE TABLE IF NOT EXISTS bvj_stock (
    item        TEXT PRIMARY KEY,
    on_hand     INTEGER NOT NULL DEFAULT 0,
    unit        TEXT NOT NULL DEFAULT 'stuks',
    updated_at  TEXT NOT NULL,
    updated_by  TEXT DEFAULT ''
);

-- Reorder-drempel per item: onder hoeveel stuks (na aftrek van open vraag)
-- Vincent tijdig wil bijbestellen.
CREATE TABLE IF NOT EXISTS bvj_stock_thresholds (
    item      TEXT PRIMARY KEY,
    min_qty   INTEGER NOT NULL DEFAULT 0
);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True
