"""
Per-klant Microsoft OAuth — de tegenhanger van domains/outlook/service.py.

outlook/service.py gebruikt een MSAL *public* client met device-code-flow:
één globaal account voor het hele systeem, ontworpen voor Vincent die zelf
inlogt op een terminal. Per-klant koppelen loopt via de Iris-onboarding-
wizard in Iris Remote (Vercel) — de authorization-code-uitwisseling zelf
gebeurt daar (remote/api/oauth.js, géén MSAL beschikbaar in Node), en komt
via het Bridge-commando `oauth_token_relay` hier binnen als een kale
{access_token, refresh_token, expiry, scopes}-dict (zie resolve.py:
store_relayed_token). Dit bestand doet daarom **geen** MSAL-cache-beheer
meer voor per-klant accounts — alleen een rechtstreekse refresh-call naar
Microsofts token-endpoint, exact zoals oauth_google.py dat al deed. Tokens
landen in `oauth_accounts` (provider='microsoft'), nooit in `outlook_tokens`
— die tabel blijft het globale account van outlook/service.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from ...shared.config import OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID
from ...shared.database import get_conn
from ...domains.outlook.service import GRAPH_SCOPES

log = logging.getLogger(__name__)

TOKEN_ENDPOINT = f"https://login.microsoftonline.com/{OUTLOOK_TENANT_ID}/oauth2/v2.0/token"
# offline_access is vereist voor een refresh_token bij een kale HTTP-code-
# exchange — MSAL voegt dit voor je toe, een handmatige POST niet.
RELAY_SCOPES = GRAPH_SCOPES + ["offline_access"]


def is_configured() -> bool:
    return bool(OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET)


def _refresh(site_id: str, creds: dict) -> Optional[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": OUTLOOK_CLIENT_ID,
                "client_secret": OUTLOOK_CLIENT_SECRET,
                "refresh_token": creds["refresh_token"],
                "grant_type": "refresh_token",
                "scope": " ".join(creds.get("scopes", RELAY_SCOPES)),
            },
        )
        payload = resp.json()
    if resp.status_code != 200 or "access_token" not in payload:
        log.warning(f"Microsoft-token verversen mislukt voor site {site_id}: {payload}")
        return None
    creds["access_token"] = payload["access_token"]
    # Microsoft geeft niet altijd een nieuwe refresh_token terug — behoud de oude als dat zo is.
    creds["refresh_token"] = payload.get("refresh_token", creds["refresh_token"])
    creds["expiry"] = (
        datetime.now(timezone.utc) + timedelta(seconds=payload.get("expires_in", 3600))
    ).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE oauth_accounts SET credentials_json = ?, updated_at = ? "
            "WHERE site_id = ? AND provider = 'microsoft'",
            (json.dumps(creds), datetime.now(timezone.utc).isoformat(), site_id),
        )
    return creds


def get_valid_token_for_site(site_id: str) -> Optional[str]:
    """Access-token voor déze klant, auto-refreshed. None = geen koppeling of mislukt."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT credentials_json FROM oauth_accounts WHERE site_id = ? AND provider = 'microsoft'",
            (site_id,),
        ).fetchone()
    if not row:
        return None
    try:
        creds = json.loads(row["credentials_json"])
        expiry = datetime.fromisoformat(creds["expiry"])
        if expiry <= datetime.now(timezone.utc) + timedelta(seconds=60):
            refreshed = _refresh(site_id, creds)
            if refreshed is None:
                return None
            creds = refreshed
        return creds["access_token"]
    except Exception as e:
        log.warning(f"Per-klant Microsoft-token voor site {site_id} ophalen mislukt: {e}")
        return None


def account_info(site_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT account_email, updated_at FROM oauth_accounts WHERE site_id = ? AND provider = 'microsoft'",
            (site_id,),
        ).fetchone()
    return dict(row) if row else None


def disconnect(site_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM oauth_accounts WHERE site_id = ? AND provider = 'microsoft'", (site_id,),
        )
    return cur.rowcount > 0
