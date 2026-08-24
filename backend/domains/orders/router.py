"""API voor het bestellingen-/inkoop-dashboard van Bewaard voor Jou.

Alles is lezen behalve de voorraad-PATCHes (Vincents eigen handmatige
telling) en de handmatige refresh-knop. ImpactOS schrijft nooit terug naar
life-journey-backend — orders wijzigen blijft exclusief het admin-panel daar.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...shared.database import get_conn
from . import procurement, service
from .models import ensure_schema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class StockUpdate(BaseModel):
    on_hand: int


class ThresholdUpdate(BaseModel):
    min_qty: int


@router.get("/dashboard")
def get_dashboard() -> Dict[str, Any]:
    """Eén ophaal-call voor de hele UI: KPI's, voorraadstaat, recente
    bestellingen en de configuratiestand — zodat "nog niet gekoppeld" nooit
    als een lege, rustige tabel oogt."""
    ensure_schema()
    with get_conn() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price_paid + discount_cents), 0) AS omzet "
            "FROM bvj_orders WHERE status IN ('PAID','FULFILLED')"
        ).fetchone()
        pending_fulfillment = conn.execute(
            "SELECT COUNT(*) AS n FROM bvj_orders WHERE status = 'PAID'"
        ).fetchone()["n"]
        last_sync = conn.execute(
            "SELECT MAX(synced_at) AS t FROM bvj_orders"
        ).fetchone()["t"]

    return {
        "config_state": service.config_state(),
        "last_sync": last_sync,
        "kpi": {
            "orders_totaal": totals["n"],
            "omzet_cents": totals["omzet"],
            "pending_fulfillment": pending_fulfillment,
        },
        "voorraad": procurement.stock_state(),
    }


@router.get("")
def list_orders(
    status: Optional[str] = Query(default=None),
    package_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> Dict[str, Any]:
    ensure_schema()
    sql = "SELECT * FROM bvj_orders WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status.upper())
    if package_type:
        sql += " AND package_type = ?"
        params.append(package_type.upper())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        try:
            r["addons"] = json.loads(r.get("addons") or "[]")
        except (TypeError, ValueError):
            r["addons"] = []
    return {"orders": rows, "total": len(rows)}


@router.post("/sync")
async def trigger_sync() -> Dict[str, Any]:
    """Handmatige refresh: haalt bestellingen op en herberekent de inkoopstaat."""
    result = await service.sync_once()
    if result.get("ok"):
        procurement.evaluate()
    return result


@router.patch("/stock/{item}")
def update_stock(item: str, body: StockUpdate) -> Dict[str, Any]:
    if body.on_hand < 0:
        raise HTTPException(status_code=400, detail="on_hand mag niet negatief zijn")
    ensure_schema()
    from datetime import datetime, timezone
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bvj_stock (item, on_hand, updated_at, updated_by)
               VALUES (?, ?, ?, 'vincent')
               ON CONFLICT(item) DO UPDATE SET on_hand=excluded.on_hand, updated_at=excluded.updated_at""",
            (item, body.on_hand, datetime.now(timezone.utc).isoformat()),
        )
    return {"item": item, "on_hand": body.on_hand, "voorraad": procurement.stock_state()}


@router.patch("/stock/{item}/threshold")
def update_threshold(item: str, body: ThresholdUpdate) -> Dict[str, Any]:
    if body.min_qty < 0:
        raise HTTPException(status_code=400, detail="min_qty mag niet negatief zijn")
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bvj_stock_thresholds (item, min_qty) VALUES (?, ?)
               ON CONFLICT(item) DO UPDATE SET min_qty=excluded.min_qty""",
            (item, body.min_qty),
        )
    return {"item": item, "min_qty": body.min_qty, "voorraad": procurement.stock_state()}
