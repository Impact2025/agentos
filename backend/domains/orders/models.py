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

-- Extra kolommen (25 aug 2026, ALTER TABLE want de tabel bestaat al met data —
-- CREATE TABLE IF NOT EXISTS raakt bestaande tabellen niet aan, zie _migrate()
-- hieronder). recipient_relation/card_message/personal_message/
-- shipping_address/gift_card_code komen uit de sync (life-journey-backend
-- levert ze sinds 25 aug 2026 mee) en worden bij elke sync overschreven —
-- net als fulfilled_at/usb_burned_at.
--
-- dagbesteding_sent_at/shipped_at zijn LOKAAL en staan bewust NIET in de
-- upsert-SET-clause van _upsert_orders(): dit is de fysieke fulfillment die
-- Vincent via Impact OS bijhoudt (niet via het admin-panel van
-- life-journey-backend), dus een volgende sync mag deze twee velden nooit
-- overschrijven met de (lege) upstream-waarde — anders verdwijnt "al naar de
-- dagbesteding gestuurd" bij de eerstvolgende ronde stilletjes weer.

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

-- Inkoopvoorstellen (25 aug 2026, inkoop.py). De agent stelt voor, Vincent
-- bestelt zelf via de leverancier-link en klikt daarna 'Ik heb besteld' —
-- zelfde voorstel-dan-klik-gate als de Beursmeester en de Wachtrij. Impact OS
-- plaatst nooit zelf een bestelling: er is geen leverancier-API, en zelfs als
-- die er was zou een geplaatste order (echt geld) achter dezelfde
-- menselijke-klik-regel moeten blijven als alle andere onomkeerbare acties.
CREATE TABLE IF NOT EXISTS bvj_purchase_proposals (
    id                  TEXT PRIMARY KEY,
    item                TEXT NOT NULL,
    qty                 INTEGER NOT NULL,
    reden               TEXT NOT NULL DEFAULT '',
    estimated_cost_cents INTEGER DEFAULT NULL,
    order_url           TEXT DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'open',  -- open | besteld | genegeerd
    created_at          TEXT NOT NULL,
    resolved_at         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bvj_purchase_item_status ON bvj_purchase_proposals(item, status);
"""

_schema_ready = False


_NEW_COLUMNS = [
    ("bvj_orders", "recipient_relation", "ALTER TABLE bvj_orders ADD COLUMN recipient_relation TEXT DEFAULT ''"),
    ("bvj_orders", "card_message", "ALTER TABLE bvj_orders ADD COLUMN card_message TEXT DEFAULT ''"),
    ("bvj_orders", "personal_message", "ALTER TABLE bvj_orders ADD COLUMN personal_message TEXT DEFAULT ''"),
    ("bvj_orders", "shipping_address", "ALTER TABLE bvj_orders ADD COLUMN shipping_address TEXT DEFAULT ''"),
    ("bvj_orders", "gift_card_code", "ALTER TABLE bvj_orders ADD COLUMN gift_card_code TEXT DEFAULT ''"),
    ("bvj_orders", "dagbesteding_sent_at", "ALTER TABLE bvj_orders ADD COLUMN dagbesteding_sent_at TEXT DEFAULT ''"),
    ("bvj_orders", "shipped_at", "ALTER TABLE bvj_orders ADD COLUMN shipped_at TEXT DEFAULT ''"),
    # Leverancier-info per item (25 aug 2026, inkoop.py) — handmatig door
    # Vincent ingevuld, nooit geraden: een verzonnen bestellink is erger dan
    # geen link.
    ("bvj_stock", "order_url", "ALTER TABLE bvj_stock ADD COLUMN order_url TEXT DEFAULT ''"),
    ("bvj_stock", "reorder_qty", "ALTER TABLE bvj_stock ADD COLUMN reorder_qty INTEGER DEFAULT 0"),
    ("bvj_stock", "unit_cost_cents", "ALTER TABLE bvj_stock ADD COLUMN unit_cost_cents INTEGER DEFAULT 0"),
]


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
        for table, name, ddl in _NEW_COLUMNS:
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if name not in cols:
                conn.execute(ddl)
    _schema_ready = True
