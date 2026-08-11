"""
De enige plek die weet dat er twee bronnen van waarheid zijn voor
mail/agenda/GSC-credentials: het bestaande globale account (device-flow
Outlook / gedeeld Google service-account) en een eventueel per-klant
gekoppeld account uit `oauth_accounts` (Iris-onboarding).

Elke resolver-functie probeert eerst het per-site account; ontbreekt dat (of
is er geen `site_id` bekend bij de aanroeper), dan valt hij terug op het
bestaande pad. Bestaande aanroepen die geen `site_id` doorgeven — vandaag alle
~20 call sites van `outlook.service.get_valid_token()` — gedragen zich dus
exact zoals vóór dit bestand bestond. Alleen aanroepers die een `site_id`
doorgeven (bijv. de dagelijkse GSC-sync, die al per site itereert) krijgen
per-klant credentials zodra die er zijn.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...shared.database import get_conn

log = logging.getLogger(__name__)


def store_relayed_token(
    site_id: str, provider: str, account_email: str,
    credentials: Dict, scopes: List[str],
) -> None:
    """Enige schrijver van `oauth_accounts` — of het nu de Bridge-relay is
    (Iris Remote deed de OAuth-uitwisseling, zie remote/api/oauth.js) of een
    toekomstige lokale flow. `credentials` is de kale token-dict
    ({access_token, refresh_token, expiry, scopes}) die oauth_google.py en
    oauth_microsoft.py ook gebruiken voor hun eigen ververs-logica — dezelfde
    vorm voor beide providers, zodat er maar één opslagformaat is."""
    if provider not in ("google", "microsoft"):
        raise ValueError(f"Onbekende provider: {provider}")
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO oauth_accounts
               (id, site_id, provider, account_email, credentials_json, scopes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(site_id, provider) DO UPDATE SET
                   account_email    = excluded.account_email,
                   credentials_json = excluded.credentials_json,
                   scopes           = excluded.scopes,
                   updated_at       = excluded.updated_at""",
            (str(uuid.uuid4()), site_id, provider, account_email,
             json.dumps(credentials), " ".join(scopes), now, now),
        )


def _has_account(site_id: Optional[str], provider: str) -> bool:
    if not site_id:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM oauth_accounts WHERE site_id = ? AND provider = ?",
            (site_id, provider),
        ).fetchone()
    return bool(row)


def microsoft_token_for(site_id: Optional[str] = None) -> Optional[str]:
    """Access-token: per-site account als dat bestaat, anders het globale."""
    if _has_account(site_id, "microsoft"):
        from . import oauth_microsoft
        token = oauth_microsoft.get_valid_token_for_site(site_id)
        if token:
            return token
        log.warning(
            f"Site {site_id} heeft een Microsoft-koppeling maar levert geen geldig "
            "token — val terug op het globale account."
        )
    from ...domains.outlook import service as outlook_service
    return outlook_service.get_valid_token()


def google_credentials_for(site_id: Optional[str] = None):
    """google.oauth2-credentials: per-site OAuth als dat bestaat, anders het
    globale service-account (via de aanroepende module's eigen `_creds()`/
    `_get_service()` — die blijft de terugval-bron, dit geeft alleen het
    per-site alternatief terug of None."""
    if _has_account(site_id, "google"):
        from . import oauth_google
        creds = oauth_google.get_credentials_for_site(site_id)
        if creds:
            return creds
        log.warning(
            f"Site {site_id} heeft een Google-koppeling maar levert geen geldige "
            "credentials — val terug op het gedeelde service-account."
        )
    return None
