"""
Sites — portfolio van websites voor de Demand Engine.

Elke site koppelt een Search Console-property (databron) aan een publicatie-doel
(jouw eigen blog-admin, ingevuld in Fase 4). Voor Fase 1 zijn alleen `name` en
`gsc_property` nodig.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn

_FIELDS = ("name", "base_url", "gsc_property", "publish_api_url", "publish_api_key", "default_author",
           "linkedin_token", "linkedin_user_urn",
           "facebook_page_id", "facebook_page_token", "instagram_business_id",
           "twitter_api_key", "twitter_api_secret", "twitter_access_token", "twitter_access_secret",
           "auto_content_enabled", "external_db_url", "ga4_property_id",
           "profile", "ctas", "content_batch_size", "indexnow_key")

# Secret velden die nooit kaal naar de frontend mogen — elk krijgt i.p.v. de waarde
# een "<veld>_set" boolean terug (zelfde patroon als publish_api_key/linkedin_token).
_SECRET_FIELDS = (
    "publish_api_key", "linkedin_token",
    "facebook_page_token", "twitter_api_key", "twitter_api_secret",
    "twitter_access_token", "twitter_access_secret", "external_db_url",
    "indexnow_key",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(row: Dict) -> Dict:
    """Stuur secret-velden (publicatie-/platform-tokens) nooit kaal naar de frontend."""
    d = dict(row)
    for field in _SECRET_FIELDS:
        val = d.get(field) or ""
        d[f"{field}_set"] = bool(val)
        d.pop(field, None)
    return d


def list_sites() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY created_at ASC").fetchall()
    return [_redact(r) for r in rows]


def get_site(site_id: str) -> Optional[Dict]:
    """Volledige rij (incl. sleutel) — voor interne services zoals de Demand Engine."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return dict(row) if row else None


def create_site(data: Dict) -> Dict:
    site_id = str(uuid.uuid4())
    now = _now()
    values = {f: (data.get(f) or "") for f in _FIELDS}
    if not values["name"].strip():
        raise ValueError("Veld 'name' is verplicht.")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sites
               (id, name, base_url, gsc_property, publish_api_url, publish_api_key,
                default_author, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (site_id, values["name"], values["base_url"], values["gsc_property"],
             values["publish_api_url"], values["publish_api_key"],
             values["default_author"], now),
        )
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return _redact(row)


def update_site(site_id: str, data: Dict) -> Optional[Dict]:
    updates, params = [], []
    for f in _FIELDS:
        if f in data and data[f] is not None:
            # Lege secret-velden = niet overschrijven (behoud bestaande waarde).
            if f in _SECRET_FIELDS and not str(data[f]).strip():
                continue
            updates.append(f"{f} = ?")
            params.append(data[f])
    if not updates:
        return get_site(site_id) and _redact(get_site(site_id))
    params.append(site_id)
    with get_conn() as conn:
        cur = conn.execute(f"UPDATE sites SET {', '.join(updates)} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return _redact(row)


def delete_site(site_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return cur.rowcount > 0
