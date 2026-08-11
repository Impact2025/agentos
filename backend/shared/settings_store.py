"""
Instance-brede instellingen die self-service via de Instellingen-hub gezet
worden (bv. agenda-ID) i.p.v. via .env+herstart. Alleen voor niet-geheime,
niet-installatietijd config — API-keys en service-account-credentials
blijven .env, die vergen sowieso een herstart om in te laden.

Kleine in-process cache: instellingen wijzigen zelden en de aanroepers
(bv. `_cal_id()` op elke agenda-call) mogen niet elke keer een DB-round-trip
doen. Wordt geïnvalideerd bij `set_setting`/`clear_setting`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .database import get_conn

log = logging.getLogger(__name__)

_cache: dict[str, Optional[str]] = {}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    if key in _cache:
        cached = _cache[key]
        return cached if cached is not None else default
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM instance_settings WHERE key = ?", (key,)
            ).fetchone()
    except Exception:
        log.warning("instance_settings lezen mislukt voor '%s'", key, exc_info=True)
        return default
    value = row["value"] if row else None
    _cache[key] = value
    return value if value is not None else default


def set_setting(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO instance_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, now),
        )
    _cache[key] = value


def clear_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM instance_settings WHERE key = ?", (key,))
    _cache.pop(key, None)
