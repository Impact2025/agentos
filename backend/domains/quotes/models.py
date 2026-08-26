"""
Offertes-schema.

Geen e-signature-koppeling: er is geen account/API-key voor een e-sign-
provider (SignRequest/PandaDoc e.d.) beschikbaar, en een gefingeerde
integratie is erger dan geen integratie. Een offerte wordt hier als
zelfstandig HTML-document gerenderd (downloadbaar, printbaar naar PDF) en per
mail verstuurd; 'geaccepteerd'/'afgewezen' zet Vincent zelf op basis van het
antwoord van de klant — dezelfde discipline als `mark_posted_manually` bij
social (14e/social_campaign.py): een status die niet geautomatiseerd kán
worden, hoort een expliciete menselijke knop te zijn, geen giswerk.

Bedragen komen NOOIT uit een LLM: `items` (omschrijving/aantal/prijs) wordt
altijd met de hand ingevuld — een verzonnen bedrag in een offerte is geld dat
een klant kan tegenkomen, precies het soort fout die de Beursmeester en de
Gauntlet elders in deze codebase hard uitsluiten.

Schema leeft in dit domein zodat het zelfstandig te verwijderen is.
ensure_schema() is idempotent.
"""
from ...shared.database import get_conn

DDL = """
CREATE TABLE IF NOT EXISTS quotes (
    id             TEXT PRIMARY KEY,
    company_id     TEXT DEFAULT '',
    deal_id        TEXT DEFAULT '',
    client_name    TEXT NOT NULL,
    client_email   TEXT DEFAULT '',
    title          TEXT NOT NULL,
    intro          TEXT DEFAULT '',
    items          TEXT NOT NULL DEFAULT '[]',
    vat_percent    INTEGER NOT NULL DEFAULT 21,
    valid_until    TEXT DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'concept',
    created_at     TEXT NOT NULL,
    sent_at        TEXT DEFAULT '',
    decided_at     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);
CREATE INDEX IF NOT EXISTS idx_quotes_deal ON quotes(deal_id);
"""

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn:
        conn.executescript(DDL)
    _schema_ready = True
