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
from . import fulfillment, inkoop, procurement, service
from .models import ensure_schema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class StockUpdate(BaseModel):
    on_hand: int


class ThresholdUpdate(BaseModel):
    min_qty: int


class LeverancierUpdate(BaseModel):
    order_url: str = ""
    reorder_qty: int = 0
    unit_cost_cents: int = 0


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


@router.get("/analytics")
def get_analytics() -> Dict[str, Any]:
    """Verkoop- en inkoopoverzicht in één call voor de Verkoop & Inkoop-pagina:
    omzettrend, pakket-mix, fulfillment-doorlooptijd en promo-gebruik. Alles
    deterministische SQL op de lokale `bvj_orders`-cache — geen LLM, dezelfde
    afweging als `procurement.py`."""
    ensure_schema()
    with get_conn() as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n, COALESCE(SUM(price_paid),0) AS omzet_cents "
            "FROM bvj_orders GROUP BY status ORDER BY n DESC"
        ).fetchall()
        package_rows = conn.execute(
            "SELECT package_type, COUNT(*) AS n, COALESCE(SUM(price_paid),0) AS omzet_cents "
            "FROM bvj_orders WHERE status IN ('PAID','FULFILLED') "
            "GROUP BY package_type ORDER BY omzet_cents DESC"
        ).fetchall()
        promo_rows = conn.execute(
            "SELECT promo_code_used AS code, COUNT(*) AS n, COALESCE(SUM(discount_cents),0) AS korting_cents "
            "FROM bvj_orders WHERE COALESCE(promo_code_used,'') != '' "
            "GROUP BY promo_code_used ORDER BY n DESC LIMIT 10"
        ).fetchall()
        revenue_rows = conn.execute(
            "SELECT date(CASE WHEN COALESCE(paid_at,'')!='' THEN paid_at ELSE created_at END) AS dag, "
            "COALESCE(SUM(price_paid),0) AS omzet_cents, COUNT(*) AS n FROM bvj_orders "
            "WHERE status IN ('PAID','FULFILLED') "
            "AND COALESCE(CASE WHEN COALESCE(paid_at,'')!='' THEN paid_at ELSE created_at END,'') != '' "
            "AND dag >= date('now','-30 days') GROUP BY dag ORDER BY dag"
        ).fetchall()
        fulfill_row = conn.execute(
            "SELECT AVG(julianday(fulfilled_at) - julianday(paid_at)) AS gem_dagen, COUNT(*) AS n "
            "FROM bvj_orders WHERE COALESCE(fulfilled_at,'') != '' AND COALESCE(paid_at,'') != ''"
        ).fetchone()
        totals = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price_paid),0) AS omzet, COALESCE(AVG(price_paid),0) AS gem "
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
        "pending_fulfillment": pending_fulfillment,
        "status_breakdown": [dict(r) for r in status_rows],
        "package_breakdown": [dict(r) for r in package_rows],
        "promo_usage": [dict(r) for r in promo_rows],
        "omzet_by_day": [{"date": r["dag"], "omzet": r["omzet_cents"] / 100.0, "orders": r["n"]}
                          for r in revenue_rows],
        "fulfillment": {
            "gemiddelde_dagen": round(fulfill_row["gem_dagen"], 1)
                                if fulfill_row and fulfill_row["gem_dagen"] is not None else None,
            "n": fulfill_row["n"] if fulfill_row else 0,
        },
        "gemiddelde_orderwaarde_cents": round(totals["gem"]) if totals else 0,
        "orders_totaal": totals["n"] if totals else 0,
        "omzet_totaal_cents": totals["omzet"] if totals else 0,
        "voorraad": procurement.stock_state(),
    }


@router.post("/sync")
async def trigger_sync() -> Dict[str, Any]:
    """Handmatige refresh: haalt bestellingen op en herberekent de inkoopstaat."""
    result = await service.sync_once()
    if result.get("ok"):
        procurement.evaluate()
        inkoop.evaluate()
    return result


@router.get("/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    order = fulfillment.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order niet gevonden")
    return {"order": order, "materiaal": fulfillment.materiaal(order)}


@router.post("/{order_id}/dagbesteding/versturen")
def send_to_dagbesteding(order_id: str) -> Dict[str, Any]:
    """Order (incl. adressticker + kaartjestekst) naar de dagbesteding sturen
    om gemaakt te worden. Verbruikt meteen usb/giftbox uit de voorraad."""
    try:
        result = fulfillment.send_to_dagbesteding(order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    procurement.evaluate()
    inkoop.evaluate()
    return result


@router.post("/{order_id}/dagbesteding/verzonden")
def mark_shipped(order_id: str) -> Dict[str, Any]:
    """De dagbesteding is klaar en het pakket is de deur uit."""
    try:
        return fulfillment.mark_shipped(order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/inkoop/voorstellen")
def list_inkoop_voorstellen() -> Dict[str, Any]:
    return {"voorstellen": inkoop.list_open()}


@router.post("/inkoop/voorstellen/{proposal_id}/bestel")
def bestel_voorstel(proposal_id: str) -> Dict[str, Any]:
    """Vincent heeft de bestelling zelf bij de leverancier geplaatst; dit
    sluit alleen het voorstel af. Impact OS plaatst nooit zelf een order."""
    try:
        return inkoop.mark_ordered(proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/inkoop/voorstellen/{proposal_id}/negeer")
def negeer_voorstel(proposal_id: str) -> Dict[str, Any]:
    try:
        return inkoop.dismiss(proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/stock/{item}/leverancier")
def update_leverancier(item: str, body: LeverancierUpdate) -> Dict[str, Any]:
    if body.reorder_qty < 0 or body.unit_cost_cents < 0:
        raise HTTPException(status_code=400, detail="Aantal en kosten mogen niet negatief zijn")
    ensure_schema()
    from datetime import datetime, timezone
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bvj_stock (item, on_hand, order_url, reorder_qty, unit_cost_cents, updated_at, updated_by)
               VALUES (?, 0, ?, ?, ?, ?, 'vincent')
               ON CONFLICT(item) DO UPDATE SET
                 order_url=excluded.order_url, reorder_qty=excluded.reorder_qty,
                 unit_cost_cents=excluded.unit_cost_cents, updated_at=excluded.updated_at""",
            (item, body.order_url.strip(), body.reorder_qty, body.unit_cost_cents,
             datetime.now(timezone.utc).isoformat()),
        )
    return {"item": item, "voorraad": procurement.stock_state()}


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
