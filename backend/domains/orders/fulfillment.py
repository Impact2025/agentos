"""
Bewaard voor Jou — fysieke fulfillment via de dagbesteding.

Twee stappen na betaling, allebei een menselijke klik in Impact OS: (1)
Vincent stuurt de order naar de dagbesteding om gemaakt te worden — dat
verbruikt usb/giftbox uit de voorraad (zie procurement.demand(), die
`dagbesteding_sent_at` als verbruiksmoment leest); (2) de dagbesteding is
klaar en het pakket gaat de deur uit. Nooit automatisch: dit is fysieke post
naar een echt adres, en Impact OS grijpt zelf nooit in de wereld in (zelfde
regel als "publiceer/verstuur nooit automatisch buiten de Wachtrij-gate om",
hier toegepast op een fysiek pakket in plaats van een artikel).

De adressticker en de kaartjestekst zijn deterministisch samengesteld — geen
LLM, want er valt niets te bedenken: het adres komt uit shipping_address, de
kaartjestekst is letterlijk wat de klant bij checkout heeft geschreven
(card_message). Zelfde afweging als de radar-signaalpoort en de kansen-gate.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from .models import ensure_schema

PROJECT = "Bewaard voor Jou"

STUCK_AFTER_DAYS = 2       # betaald, nog niet naar de dagbesteding
SHIPPING_AFTER_DAYS = 5    # bij de dagbesteding, nog niet verzonden


def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bvj_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return None
    o = dict(row)
    try:
        o["addons"] = json.loads(o.get("addons") or "[]")
    except (TypeError, ValueError):
        o["addons"] = []
    try:
        o["shipping_address"] = json.loads(o["shipping_address"]) if o.get("shipping_address") else None
    except (TypeError, ValueError):
        o["shipping_address"] = None
    return o


def _address_sticker_text(order: Dict[str, Any]) -> str:
    addr = order.get("shipping_address") or {}
    name = (addr.get("full_name") or order.get("recipient_name") or "").strip()
    lines = [name] if name else []
    street_line = " ".join(p for p in [(addr.get("street") or "").strip(),
                                        (addr.get("house_number") or "").strip()] if p)
    if street_line:
        lines.append(street_line)
    city_line = " ".join(p for p in [(addr.get("postal_code") or "").strip(),
                                      (addr.get("city") or "").strip()] if p)
    if city_line:
        lines.append(city_line)
    country = (addr.get("country") or "NL").strip().upper()
    if country and country != "NL":
        lines.append(country)
    return "\n".join(lines) if lines else "Geen verzendadres bekend"


def materiaal(order: Dict[str, Any]) -> Dict[str, str]:
    """Print-klare tekst: adressticker + kaartjestekst. Geen generatie, alleen
    opmaak van wat de klant al heeft aangeleverd."""
    return {
        "sticker": _address_sticker_text(order),
        "kaartje": order.get("card_message") or order.get("personal_message") or "",
        "pakket": order.get("package_type") or "",
        "ontvanger": order.get("recipient_name")
                     or (order.get("shipping_address") or {}).get("full_name") or "",
    }


def send_to_dagbesteding(order_id: str) -> Dict[str, Any]:
    order = get_order(order_id)
    if not order:
        raise ValueError("Order niet gevonden")
    if order["status"] not in ("PAID", "FULFILLED"):
        raise ValueError(f"Order staat op status {order['status']}, niet betaald")
    if order.get("dagbesteding_sent_at"):
        raise ValueError("Deze order is al naar de dagbesteding gestuurd")
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE bvj_orders SET dagbesteding_sent_at=? WHERE id=?", (now, order_id))
    log_outcome(
        PROJECT, "dagbesteding_verstuurd",
        f"Order {order_id[:8]} ({order['package_type']}, "
        f"{order.get('recipient_name') or 'onbekende ontvanger'}) naar de dagbesteding "
        "gestuurd om gemaakt te worden.",
        next_step="Print de adressticker en de kaartjestekst mee, en klik 'Verzonden' "
                   "zodra het pakket de deur uit is.",
    )
    order["dagbesteding_sent_at"] = now
    return {"order": order, "materiaal": materiaal(order)}


def mark_shipped(order_id: str) -> Dict[str, Any]:
    order = get_order(order_id)
    if not order:
        raise ValueError("Order niet gevonden")
    if not order.get("dagbesteding_sent_at"):
        raise ValueError("Deze order is nog niet naar de dagbesteding gestuurd")
    if order.get("shipped_at"):
        raise ValueError("Deze order staat al als verzonden")
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE bvj_orders SET shipped_at=? WHERE id=?", (now, order_id))
    log_outcome(
        PROJECT, "dagbesteding_pakket_verzonden",
        f"Pakket voor order {order_id[:8]} ({order['package_type']}, "
        f"{order.get('recipient_name') or 'onbekende ontvanger'}) is door de dagbesteding "
        "verzonden.",
        next_step="Niets — de bestelling is afgerond.",
    )
    order["shipped_at"] = now
    return {"order": order}


def _already_logged_today(action: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM activity_log WHERE action = ? AND project = ? "
            "AND date(created_at) = date('now', 'localtime') LIMIT 1",
            (action, PROJECT),
        ).fetchone()
    return row is not None


def _rows_to_detail(rows: List[Any], limit: int = 5) -> str:
    detail = ", ".join(f"{r['package_type']} voor {r['recipient_name'] or 'onbekend'}" for r in rows[:limit])
    if len(rows) > limit:
        detail += f" (+{len(rows) - limit} meer)"
    return detail


def check_fulfillment_backlog(stuck_after_days: int = STUCK_AFTER_DAYS,
                               shipping_after_days: int = SHIPPING_AFTER_DAYS) -> None:
    """Deterministische signalering (geen LLM, zelfde afweging als
    procurement.evaluate()): betaalde orders die te lang wachten op
    verzending naar de dagbesteding, of te lang bij de dagbesteding liggen
    zonder dat het pakket de deur uit is. Meldt, grijpt nooit zelf in."""
    ensure_schema()
    with get_conn() as conn:
        wachten = conn.execute(
            "SELECT id, package_type, recipient_name FROM bvj_orders "
            "WHERE status='PAID' AND COALESCE(dagbesteding_sent_at,'')='' "
            "AND COALESCE(paid_at,'') != '' "
            "AND julianday('now') - julianday(paid_at) >= ?",
            (stuck_after_days,),
        ).fetchall()
        bij_dagbesteding = conn.execute(
            "SELECT id, package_type, recipient_name FROM bvj_orders "
            "WHERE status='PAID' AND COALESCE(dagbesteding_sent_at,'') != '' "
            "AND COALESCE(shipped_at,'')='' "
            "AND julianday('now') - julianday(dagbesteding_sent_at) >= ?",
            (shipping_after_days,),
        ).fetchall()

    if wachten and not _already_logged_today("dagbesteding_wachtrij"):
        log_outcome(
            PROJECT, "dagbesteding_wachtrij",
            f"{len(wachten)} betaalde order(s) wachten al {stuck_after_days}+ dagen op "
            f"verzending naar de dagbesteding: {_rows_to_detail(wachten)}.",
            next_step="Stuur ze naar de dagbesteding via de Verkoop-tab (knop bij elke order).",
            status="error",
        )
    if bij_dagbesteding and not _already_logged_today("dagbesteding_verzending_achterstand"):
        log_outcome(
            PROJECT, "dagbesteding_verzending_achterstand",
            f"{len(bij_dagbesteding)} order(s) liggen al {shipping_after_days}+ dagen bij de "
            f"dagbesteding zonder dat het pakket verzonden is: {_rows_to_detail(bij_dagbesteding)}.",
            next_step="Check bij de dagbesteding waar het op vastloopt, of klik 'Verzonden' "
                       "als het al onderweg is.",
            status="error",
        )
