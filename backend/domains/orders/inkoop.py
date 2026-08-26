"""
Bewaard voor Jou — inkoopvoorstellen (deterministisch, geen LLM, zelfde
afweging als procurement.py: een tekort is een telling, geen mening).

Vincent koos expliciet voor "voorstel + 1 klik goedkeuren" i.p.v. volledig
automatisch bestellen (25 aug 2026) — zelfde regel als overal in Impact OS:
een order is de meest onomkeerbare verzending die er is (zie de Beursmeester
en de Wachtrij-gate). Er is bovendien geen leverancier-API: Vincent bestelt
zelf via zijn eigen webshop-link, en klikt daarna 'Ik heb besteld'.

evaluate() draait mee in de orders_sync-scheduler-job (net als
procurement.evaluate()) en zet, per item met status 'tekort_nu' of
'onder_drempel', een open voorstel klaar — of laat een al openstaand/recent
besteld voorstel met rust, zodat dezelfde tekort-situatie niet elke ronde
opnieuw om een klik vraagt.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from . import procurement
from .models import ensure_schema

PROJECT = "Bewaard voor Jou"

# Hoeveel dagen een 'besteld'-voorstel de agent stil houdt voordat hij voor
# hetzelfde item weer een nieuw voorstel mag maken — een reële leverancier
# heeft een levertijd, en zonder deze rem zou een tekort dat blijft bestaan
# (want de bestelling is onderweg) er elke ronde weer om vragen.
_BESTELD_COOLDOWN_DAYS = 14


def _default_reorder_qty(item: str, row: Dict[str, Any]) -> int:
    """Als Vincent geen vaste reorder_qty heeft ingesteld: vul aan tot 2x de
    drempel boven het tekort — een simpele, uitlegbare vuistregel, geen
    voorspelmodel (daarvoor is de historie in bvj_orders te dun)."""
    if row.get("reorder_qty"):
        return int(row["reorder_qty"])
    target = max(row["min_qty"] * 2, 5)
    missing = target - row["remaining_after_demand"]
    return max(missing, 1)


def _open_or_recent_proposal(conn, item: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM bvj_purchase_proposals WHERE item = ? "
        "AND (status = 'open' OR "
        "     (status = 'besteld' AND julianday('now') - julianday(resolved_at) < ?)) "
        "ORDER BY created_at DESC LIMIT 1",
        (item, _BESTELD_COOLDOWN_DAYS),
    ).fetchone()
    return dict(row) if row else None


def evaluate() -> List[Dict[str, Any]]:
    """log_outcome() opent zijn eigen get_conn() — nooit aanroepen terwijl de
    schrijftransactie hieronder nog open is, anders wacht die tweede
    connectie op de eerste (zelfde faalmodus als het db-lock-incident van
    17 jul 2026: een langgehouden write-lock over ander werk heen). Vandaar
    de outcomes-lijst: eerst alles wegschrijven en de connectie sluiten, dán
    pas loggen."""
    ensure_schema()
    state = procurement.stock_state()
    created = []
    to_log = []
    with get_conn() as conn:
        for row in state:
            if row["status"] not in ("tekort_nu", "onder_drempel"):
                continue
            item = row["item"]
            if _open_or_recent_proposal(conn, item):
                continue
            qty = _default_reorder_qty(item, row)
            cost = row["unit_cost_cents"] * qty if row["unit_cost_cents"] else None
            reden = (
                f"Tekort nu: {row['demand']} nodig voor betaalde orders, {row['on_hand']} op voorraad."
                if row["status"] == "tekort_nu"
                else f"Nog {row['remaining_after_demand']} over na de openstaande vraag, onder de drempel van {row['min_qty']}."
            )
            pid = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO bvj_purchase_proposals
                   (id, item, qty, reden, estimated_cost_cents, order_url, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
                (pid, item, qty, reden, cost, row["order_url"] or "", now),
            )
            created.append({"id": pid, "item": item, "qty": qty, "reden": reden})
            next_step = (
                f"Bestel {qty} {item} bij je leverancier en klik daarna 'Ik heb besteld'."
                if row["order_url"]
                else f"Bestel {qty} {item} bij je leverancier (nog geen bestellink ingesteld — "
                     "vul die in bij Voorraad > Leverancier) en klik daarna 'Ik heb besteld'."
            )
            to_log.append((reden, next_step, "error" if row["status"] == "tekort_nu" else "ok", qty, item))

    for reden, next_step, status, qty, item in to_log:
        log_outcome(
            PROJECT, "inkoopvoorstel",
            f"Inkoopvoorstel: {qty}x {item} — {reden}",
            next_step=next_step,
            status=status,
        )
    return created


def list_open() -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bvj_purchase_proposals WHERE status = 'open' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_ordered(proposal_id: str) -> Dict[str, Any]:
    return _resolve(proposal_id, "besteld", "bvj_purchase_besteld",
                     "besteld bij de leverancier")


def dismiss(proposal_id: str) -> Dict[str, Any]:
    return _resolve(proposal_id, "genegeerd", "bvj_purchase_genegeerd",
                     "genegeerd door Vincent")


def _resolve(proposal_id: str, status: str, action: str, label: str) -> Dict[str, Any]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bvj_purchase_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            raise ValueError("Inkoopvoorstel niet gevonden")
        if row["status"] != "open":
            raise ValueError(f"Dit voorstel staat al op '{row['status']}'")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE bvj_purchase_proposals SET status = ?, resolved_at = ? WHERE id = ?",
            (status, now, proposal_id),
        )
    proposal = dict(row)
    proposal["status"] = status
    proposal["resolved_at"] = now
    if status == "besteld":
        log_outcome(
            PROJECT, action,
            f"{proposal['qty']}x {proposal['item']} {label}.",
            next_step="Werk de voorraad bij (Voorraad-knop) zodra de levering binnen is.",
        )
    return proposal
