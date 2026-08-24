"""
Bewaard voor Jou — bestellingen-sync.

Haalt periodiek een read-only export op bij life-journey-backend (het
"memories"-project) via een eigen, smal endpoint
(/api/v1/admin/orders/impactos-sync) en schrijft ze idempotent weg in de
lokale cache (bvj_orders). Geen directe DB-koppeling tussen de twee systemen,
en géén schrijftoegang terug naar memories — ImpactOS leest en signaleert,
het grijpt nooit in bij een ander systeem (zelfde regel als "ImpactOS
publiceert/verstuurt zelf nooit", hier toegepast op een extern systeem).

Zelfde vorm als bridge/service.py: config_state() (off/partial/on) en
failures.py voor de faal-classificatie/escalatie.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import httpx

from ...shared import failures
from ...shared.config import BEWAARDVOORJOU_ORDERS_URL, BEWAARDVOORJOU_ORDERS_KEY
from ...shared.database import get_conn
from .models import ensure_schema

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_FAIL_KEY = "orders:sync"


def enabled() -> bool:
    return bool(BEWAARDVOORJOU_ORDERS_URL and BEWAARDVOORJOU_ORDERS_KEY)


def config_state() -> str:
    """`off` | `partial` | `on` — zelfde onderscheid als bridge.config_state():
    beide leeg is een verse installatie (stil overslaan is dan juist), één
    van de twee ingevuld betekent dat iemand dit wilde en halverwege bleef
    steken (meteen melden, niet stil wachten)."""
    url, key = bool(BEWAARDVOORJOU_ORDERS_URL), bool(BEWAARDVOORJOU_ORDERS_KEY)
    if url and key:
        return "on"
    return "partial" if (url or key) else "off"


def _missing_setting() -> str:
    if not BEWAARDVOORJOU_ORDERS_URL:
        return "BEWAARDVOORJOU_ORDERS_URL"
    return "BEWAARDVOORJOU_ORDERS_KEY" if not BEWAARDVOORJOU_ORDERS_KEY else ""


def _base() -> str:
    url = BEWAARDVOORJOU_ORDERS_URL.rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


async def sync_once() -> Dict[str, Any]:
    """Eén sync-cyclus: orders ophalen en idempotent upserten."""
    ensure_schema()
    if not enabled():
        return {"ok": False, "detail": "Orders-sync niet geconfigureerd "
                                        "(BEWAARDVOORJOU_ORDERS_URL/BEWAARDVOORJOU_ORDERS_KEY)"}
    try:
        headers = {"Authorization": f"Bearer {BEWAARDVOORJOU_ORDERS_KEY}"}
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            r = await client.get(f"{_base()}/api/v1/admin/orders/impactos-sync")
            r.raise_for_status()
            payload = r.json()
        orders = payload.get("orders", [])
        _upsert_orders(orders)
        _note_sync_ok()
        return {"ok": True, "orders": len(orders),
                "at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.warning("Bestellingen-sync mislukt: %s", failures.describe_exception(e))
        _note_sync_failed(e)
        return {"ok": False, "detail": failures.describe_exception(e)[:300],
                "failure_class": failures.classify(e),
                "at": datetime.now(timezone.utc).isoformat()}


def _upsert_orders(orders: list) -> None:
    import json

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for o in orders:
            conn.execute(
                """INSERT INTO bvj_orders
                   (id, status, package_type, addons, price_paid, discount_cents,
                    promo_code_used, recipient_name, created_at, paid_at,
                    fulfilled_at, usb_burned_at, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, package_type=excluded.package_type,
                     addons=excluded.addons, price_paid=excluded.price_paid,
                     discount_cents=excluded.discount_cents,
                     promo_code_used=excluded.promo_code_used,
                     recipient_name=excluded.recipient_name,
                     paid_at=excluded.paid_at, fulfilled_at=excluded.fulfilled_at,
                     usb_burned_at=excluded.usb_burned_at, synced_at=excluded.synced_at""",
                (
                    o.get("id"), o.get("status") or "", o.get("package_type") or "",
                    json.dumps(o.get("addons") or []), o.get("price_paid") or 0,
                    o.get("discount_cents") or 0, o.get("promo_code_used") or "",
                    o.get("recipient_name") or "", o.get("created_at") or "",
                    o.get("paid_at") or "", o.get("fulfilled_at") or "",
                    o.get("usb_burned_at") or "", now,
                ),
            )


def _note_sync_ok() -> None:
    had = failures.note_success(_FAIL_KEY)
    if had:
        from ...shared.outcomes import log_outcome
        log_outcome(
            "Bewaard voor Jou", "orders_sync_hersteld",
            f"Bestellingen-sync werkt weer na {had} mislukte pogingen op rij.",
            artifact=BEWAARDVOORJOU_ORDERS_URL,
            next_step="Niets — het bestellingen-dashboard toont weer de actuele stand.",
        )


def _note_sync_failed(exc: BaseException) -> None:
    detail = failures.describe_exception(exc)
    klass = failures.classify(exc)
    failures.note_failure(_FAIL_KEY, detail, klass)
    if not failures.should_escalate(_FAIL_KEY, exc):
        return
    steps = {
        failures.CLASS_AUTH: "Controleer of BEWAARDVOORJOU_ORDERS_KEY in ImpactOS' .env exact "
                             "gelijk is aan ORDERS_API_KEY in de env van life-journey-backend.",
        failures.CLASS_CONFIG: f"Controleer BEWAARDVOORJOU_ORDERS_URL "
                               f"({BEWAARDVOORJOU_ORDERS_URL or 'leeg'}) en of de Railway-deploy nog leeft.",
    }
    from ...shared.outcomes import log_outcome
    log_outcome(
        "Bewaard voor Jou", "orders_sync_failed",
        f"Bestellingen-sync naar life-journey-backend mislukt ({klass}): {detail}",
        artifact=BEWAARDVOORJOU_ORDERS_URL,
        next_step=steps.get(klass, "Test handmatig via POST /api/orders/sync en controleer "
                                   "de Railway-logs van life-journey-backend."),
        status="error",
    )
    failures.mark_escalated(_FAIL_KEY)


def report_misconfiguration() -> None:
    """Half ingevulde config: iemand wilde dit aanzetten en bleef steken."""
    missing = _missing_setting()
    if not missing:
        return
    key = f"orders:config:{missing}"
    failures.note_failure(key, f"{missing} ontbreekt", failures.CLASS_CONFIG)
    if failures.streak(key).get("escalated"):
        return
    from ...shared.outcomes import log_outcome
    log_outcome(
        "Bewaard voor Jou", "orders_niet_geconfigureerd",
        f"Bestellingen-sync staat half ingesteld: {missing} ontbreekt in .env, "
        "dus de sync slaat elke ronde over en het dashboard toont een verouderde stand.",
        next_step=f"Zet {missing} in .env en herstart ImpactOS (impactos_service.cmd).",
        status="error",
    )
    failures.mark_escalated(key)
