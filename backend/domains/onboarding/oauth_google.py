"""
Per-klant Google OAuth — de tegenhanger van het gedeelde GSC/Calendar
service-account in backend/shared/config.py (GSC_SERVICE_ACCOUNT_PATH).

Dat service-account is één identiteit voor het hele systeem, die Vincent
handmatig als gebruiker toevoegt aan elke Search Console-property — geen
koppelflow, geen klant-eigen account. Voor Iris-onboarding koppelt een klant
zelf zijn eigen Google-account via een browser-consentscherm die via Iris
Remote loopt (remote/api/oauth.js, Vercel — publiek bereikbaar, de lokale
instance hoeft dat niet te zijn). De authorization-code-uitwisseling gebeurt
dáár; hier komt alleen het resultaat binnen via het Bridge-commando
`oauth_token_relay` (zie resolve.py:store_relayed_token). Dit bestand doet
dus alleen nog opslag-lezen + het ververs-token-endpoint van Google
aanroepen — geen authorize-URL/code-exchange meer.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ...shared.config import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
from ...shared.database import get_conn

log = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def is_configured() -> bool:
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)


def _refresh(site_id: str, creds: dict) -> Optional[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "refresh_token": creds["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        payload = resp.json()
    if resp.status_code != 200 or "access_token" not in payload:
        log.warning(f"Google-token verversen mislukt voor site {site_id}: {payload}")
        return None
    creds["access_token"] = payload["access_token"]
    creds["expiry"] = (
        datetime.now(timezone.utc) + timedelta(seconds=payload.get("expires_in", 3600))
    ).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE oauth_accounts SET credentials_json = ?, updated_at = ? "
            "WHERE site_id = ? AND provider = 'google'",
            (json.dumps(creds), datetime.now(timezone.utc).isoformat(), site_id),
        )
    return creds


def get_credentials_for_site(site_id: str):
    """Auto-refreshende google.oauth2.credentials.Credentials, of None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT credentials_json FROM oauth_accounts WHERE site_id = ? AND provider = 'google'",
            (site_id,),
        ).fetchone()
    if not row:
        return None
    creds = json.loads(row["credentials_json"])
    expiry = datetime.fromisoformat(creds["expiry"])
    if expiry <= datetime.now(timezone.utc) + timedelta(seconds=60):
        refreshed = _refresh(site_id, creds)
        if refreshed is None:
            return None
        creds = refreshed

    from google.oauth2.credentials import Credentials
    return Credentials(
        token=creds["access_token"],
        refresh_token=creds["refresh_token"],
        token_uri=TOKEN_ENDPOINT,
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=creds.get("scopes", []),
    )


def account_info(site_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT account_email, updated_at FROM oauth_accounts WHERE site_id = ? AND provider = 'google'",
            (site_id,),
        ).fetchone()
    return dict(row) if row else None


def disconnect(site_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM oauth_accounts WHERE site_id = ? AND provider = 'google'", (site_id,),
        )
    return cur.rowcount > 0
