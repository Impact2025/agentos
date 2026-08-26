"""
Bewaard voor Jou — inkoop-signalering (deterministisch, geen LLM).

Zelfde afweging als de radar-signaalpoort en de kansen-gate: dit is een
telling, geen mening, en een toets die zelf een gateway nodig heeft valt stil
precies wanneer je hem nodig hebt.

ITEM_REQUIREMENTS koppelt een package_type/addon-code aan de fysieke items die
ervoor nodig zijn. Dit is een lezing van de code-commentaren in
life-journey-backend (app/schemas/orders.py) — ERFGOED noemt daar expliciet
"doos inbegrepen", VERHAAL expliciet "digitaal", BABY_GIFT expliciet een
"fotoboek-voucher". NALATENSCHAP en de legacy-namen (BEGIN/VOOR_ALTIJD) zijn
een beredeneerde aanname, GEEN bevestigd feit — Vincent moet dit nalopen
voordat er blind op vertrouwd wordt. Wijzig gewoon deze tabel; de rekenlogica
eronder hoeft niet aangepast te worden.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome

PROJECT = "Bewaard voor Jou"

# item -> welke kolom op de order aangeeft dat dit item al "verbruikt" is
# (en dus niet meer meetelt in de openstaande vraag).
CONSUMED_WHEN: Dict[str, str] = {
    "usb": "usb_burned_at",
    "giftbox": "fulfilled_at",
    "fotoboek": "fulfilled_at",
    "fotoboek_voucher": "fulfilled_at",
}

# package_type / "addon:<CODE>" -> {item: aantal}.
ITEM_REQUIREMENTS: Dict[str, Dict[str, int]] = {
    # Huidige pakketten (life-journey-backend app/schemas/orders.py PackageType)
    "VERHAAL": {},                             # digitaal, geen fysiek
    "ERFGOED": {"usb": 1, "giftbox": 1},        # "doos inbegrepen"
    "NALATENSCHAP": {"usb": 1, "giftbox": 1},   # AANNAME — nalopen bij Vincent
    "BABY_GIFT": {"fotoboek_voucher": 1},       # "fotoboek-voucher"
    # Legacy pakketten (backward compat bestaande orders)
    "BEGIN": {"usb": 1, "giftbox": 1},
    "VOOR_ALTIJD": {"usb": 1, "giftbox": 1},
    "DIGITAAL": {},                             # legacy gift-card-pad, geen fysiek
    # Add-ons
    "addon:GIFT_BOX": {"giftbox": 1},
    "addon:EXTRA_USB": {"usb": 1},
    "addon:PHOTO_BOOK": {"fotoboek": 1},
    "addon:EXTRA_STORAGE": {},                  # digitaal
    "addon:VIDEO_INTRO": {},                    # digitaal
}


def _requirements_for_order(package_type: str, addons: list) -> Dict[str, int]:
    need: Dict[str, int] = {}
    for item, qty in ITEM_REQUIREMENTS.get(package_type, {}).items():
        need[item] = need.get(item, 0) + qty
    for code in addons or []:
        for item, qty in ITEM_REQUIREMENTS.get(f"addon:{code}", {}).items():
            need[item] = need.get(item, 0) + qty
    return need


def demand() -> Dict[str, int]:
    """Hoeveel van elk fysiek item er nog nodig is voor betaalde, nog niet
    verwerkte orders.

    Een item is verbruikt zodra óf life-journey-backend het meldt
    (usb_burned_at/fulfilled_at — het admin-panel daar, mens-only) óf Vincent
    de order via Impact OS naar de dagbesteding heeft gestuurd
    (dagbesteding_sent_at — dat is waar de fysieke assemblage in de praktijk
    wordt bijgehouden). Eén van beide is genoeg: het gaat om of het item
    fysiek is gebruikt, niet om welk systeem dat het eerst wist."""
    totals: Dict[str, int] = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT package_type, addons, usb_burned_at, fulfilled_at, dagbesteding_sent_at "
            "FROM bvj_orders WHERE status = 'PAID'"
        ).fetchall()
    for r in rows:
        try:
            addons = json.loads(r["addons"] or "[]")
        except (TypeError, ValueError):
            addons = []
        need = _requirements_for_order(r["package_type"], addons)
        if r["dagbesteding_sent_at"]:
            continue
        for item, qty in need.items():
            consumed_col = CONSUMED_WHEN.get(item)
            if consumed_col == "usb_burned_at" and r["usb_burned_at"]:
                continue
            if consumed_col == "fulfilled_at" and r["fulfilled_at"]:
                continue
            totals[item] = totals.get(item, 0) + qty
    return totals


def stock_state() -> List[dict]:
    """Voorraadstaat per item: on_hand, drempel, openstaande vraag, status,
    plus leverancier-info (order_url/reorder_qty/unit_cost_cents) voor de
    inkoopvoorstellen in inkoop.py."""
    need = demand()
    with get_conn() as conn:
        stock_rows = {
            r["item"]: r for r in conn.execute(
                "SELECT item, on_hand, order_url, reorder_qty, unit_cost_cents FROM bvj_stock"
            )
        }
        threshold_rows = {r["item"]: r["min_qty"] for r in conn.execute("SELECT item, min_qty FROM bvj_stock_thresholds")}

    items = sorted(set(need) | set(stock_rows) | set(threshold_rows))
    out = []
    for item in items:
        stock_row = stock_rows.get(item)
        on_hand = stock_row["on_hand"] if stock_row else 0
        min_qty = threshold_rows.get(item, 0)
        item_demand = need.get(item, 0)
        remaining = on_hand - item_demand
        if on_hand < item_demand:
            status = "tekort_nu"
        elif remaining < min_qty:
            status = "onder_drempel"
        else:
            status = "ok"
        out.append({
            "item": item,
            "on_hand": on_hand,
            "demand": item_demand,
            "min_qty": min_qty,
            "remaining_after_demand": remaining,
            "status": status,
            "order_url": stock_row["order_url"] if stock_row else "",
            "reorder_qty": stock_row["reorder_qty"] if stock_row else 0,
            "unit_cost_cents": stock_row["unit_cost_cents"] if stock_row else 0,
        })
    return out


def _already_logged_today(action: str, item: str) -> bool:
    """Zelfde inkoop-signaal voor hetzelfde item al gemeld vandaag? Dan
    overslaan — een tekort dat blijft bestaan hoeft niet elke sync-ronde
    opnieuw een kaart te openen."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM activity_log WHERE action = ? AND project = ? "
            "AND detail LIKE ? AND date(created_at) = date('now', 'localtime') LIMIT 1",
            (action, PROJECT, f"{item}:%"),
        ).fetchone()
    return row is not None


def evaluate() -> List[dict]:
    """Berekent de voorraadstaat en meldt tekorten in het Actiecentrum."""
    state = stock_state()
    for row in state:
        if row["status"] == "ok":
            continue
        item = row["item"]
        action = "inkoop_tekort" if row["status"] == "tekort_nu" else "inkoop_drempel"
        if _already_logged_today(action, item):
            continue
        if row["status"] == "tekort_nu":
            detail = (f"{item}: {row['demand']} nodig voor betaalde orders, maar "
                      f"{row['on_hand']} op voorraad — dit blokkeert fulfillment.")
            next_step = f"Bestel {item} bij bij de leverancier — de huidige voorraad dekt de betaalde orders niet."
            status = "error"
        else:
            detail = (f"{item}: nog {row['remaining_after_demand']} over na de "
                      f"openstaande vraag ({row['demand']}), onder de drempel van {row['min_qty']}.")
            next_step = f"Bestel op tijd {item} bij — de voorraad zakt binnenkort onder de gewenste marge."
            status = "ok"
        log_outcome(PROJECT, action, detail, next_step=next_step, status=status)
    return state
