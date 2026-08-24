#!/usr/bin/env python3
"""
Onboard GSC properties (daarwebsite, vrijwilligersmatch) into the ImpactOS
`sites` table so the Demand Engine (POST /api/demand/scan) can scan them.

Design:
  * No domain guessing. It asks the Search Console API which properties the
    service account (hermes-analytics@weareimpact-482912) can actually see,
    and registers only the ones we target.
  * Idempotent: skips sites already present (matched by gsc_property) and
    skips targets whose property the SA is not (yet) Owner of.

Run:
    cd /d/apps/impactos
    .venv/Scripts/python scripts/onboard_gsc_sites.py
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # .../impactos
DB_PATH = ROOT / "data" / "impactos.db"
CREDS = ROOT / "google-credentials.json"               # hermes-analytics SA
SCOPES = ["https://www.googleapis.com/auth/webmasters"]

# Targets we want onboarded, keyed by domain substring -> site metadata.
# base_url is filled where known; for vrijwilligersmatch it is discovered
# from the property string if/when the SA is Owner.
TARGETS = {
    "daar.nl": {"name": "Daarwebsite", "base_url": "https://daar.nl"},
    "vrijwilligersmatch.nl": {"name": "Vrijwilligersmatch", "base_url": "https://vrijwilligersmatch.nl"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(CREDS), scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def list_gsc_properties():
    resp = get_service().sites().list().execute()
    return [e["siteUrl"] for e in resp.get("siteEntry", [])]


def existing_properties(conn) -> set:
    rows = conn.execute(
        "SELECT gsc_property FROM sites WHERE gsc_property IS NOT NULL AND gsc_property != ''"
    ).fetchall()
    return {r[0] for r in rows}


def _domain_of(prop: str) -> str:
    return prop.replace("sc-domain:", "").replace("https://", "").replace("http://", "").rstrip("/")


def main():
    props = list_gsc_properties()
    print(f"GSC-properties zichtbaar voor service account ({len(props)}):")
    for p in props:
        print("  -", p)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    have = existing_properties(conn)

    prop_by_domain = {_domain_of(p): p for p in props}

    for domain, meta in TARGETS.items():
        prop = prop_by_domain.get(domain)
        if not prop:
            print(f"[skip] {meta['name']}: geen GSC-property voor '{domain}' "
                  f"(SA nog geen Owner, of domein heet anders?)")
            continue
        if prop in have:
            print(f"[skip] {meta['name']}: al geregistreerd ({prop})")
            continue
        base_url = meta["base_url"] or (prop if prop.startswith("http") else f"https://{domain}")
        conn.execute(
            "INSERT INTO sites (id, name, base_url, gsc_property, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), meta["name"], base_url, prop, _now()),
        )
        conn.commit()
        print(f"[ok]   {meta['name']} -> {prop} geregistreerd (base_url={base_url})")

    conn.close()
    print("Klaar.")


if __name__ == "__main__":
    main()
