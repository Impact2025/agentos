"""Offertes-service — zie models.py voor waarom hier geen e-sign en geen
door een LLM verzonnen bedragen zitten."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from .models import ensure_schema

log = logging.getLogger(__name__)

STATUSES = ["concept", "verstuurd", "geaccepteerd", "afgewezen"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r) -> Dict[str, Any]:
    return dict(r) if r is not None else {}


def _validate_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not items:
        raise ValueError("Een offerte heeft minstens één regel nodig")
    clean = []
    for it in items:
        desc = (it.get("description") or "").strip()
        qty = it.get("quantity")
        price = it.get("unit_price_cents")
        if not desc:
            raise ValueError("Elke regel heeft een omschrijving nodig")
        try:
            qty = float(qty)
            price = int(price)
        except (TypeError, ValueError):
            raise ValueError(f"Ongeldig aantal/prijs op regel '{desc}'")
        if qty <= 0 or price < 0:
            raise ValueError(f"Aantal moet > 0 en prijs mag niet negatief zijn ('{desc}')")
        clean.append({"description": desc, "quantity": qty, "unit_price_cents": price})
    return clean


def _totals(items: List[Dict[str, Any]], vat_percent: int) -> Dict[str, int]:
    subtotal = round(sum(it["quantity"] * it["unit_price_cents"] for it in items))
    vat = round(subtotal * vat_percent / 100)
    return {"subtotal_cents": subtotal, "vat_cents": vat, "total_cents": subtotal + vat}


def create_quote(client_name: str, title: str, items: List[Dict[str, Any]], *,
                  company_id: str = "", deal_id: str = "", client_email: str = "",
                  intro: str = "", vat_percent: int = 21, valid_days: int = 30) -> Dict[str, Any]:
    ensure_schema()
    client_name = (client_name or "").strip()
    title = (title or "").strip()
    if not client_name or not title:
        raise ValueError("Klantnaam en titel zijn verplicht")
    clean_items = _validate_items(items)

    qid = str(uuid.uuid4())
    now = _now()
    valid_until = (datetime.now(timezone.utc) + timedelta(days=valid_days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO quotes (id, company_id, deal_id, client_name, client_email, title, "
            "intro, items, vat_percent, valid_until, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'concept', ?)",
            (qid, company_id, deal_id, client_name, client_email, title, intro,
             json.dumps(clean_items), vat_percent, valid_until, now),
        )
    return get_quote(qid)


def get_quote(quote_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = _row(conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone())
    if not row:
        return None
    row["items"] = json.loads(row["items"] or "[]")
    row.update(_totals(row["items"], row["vat_percent"]))
    return row


def list_quotes(status: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM quotes WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()]
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM quotes ORDER BY created_at DESC"
            ).fetchall()]
    return [get_quote(i) for i in ids]


def delete_quote(quote_id: str) -> None:
    ensure_schema()
    quote = get_quote(quote_id)
    if quote and quote["status"] != "concept":
        raise ValueError("Alleen conceptoffertes kunnen worden verwijderd")
    with get_conn() as conn:
        conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))


def render_quote_html(quote: Dict[str, Any]) -> str:
    """Zelfstandig, printbaar HTML-document — geen externe assets, dus werkt
    ook als bijlage of losstaand bestand zonder internetverbinding."""
    def esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    rows = "".join(
        f"<tr><td>{esc(it['description'])}</td><td style='text-align:right'>{it['quantity']:g}</td>"
        f"<td style='text-align:right'>€{it['unit_price_cents'] / 100:,.2f}</td>"
        f"<td style='text-align:right'>€{it['quantity'] * it['unit_price_cents'] / 100:,.2f}</td></tr>"
        for it in quote["items"]
    )
    intro_html = f"<p>{esc(quote['intro'])}</p>" if quote.get("intro") else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Offerte {esc(quote['title'])}</title>
<style>
body {{ font-family: Arial, sans-serif; color: #1e293b; max-width: 700px; margin: 40px auto; }}
h1 {{ font-size: 22px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
th, td {{ padding: 8px 6px; border-bottom: 1px solid #e2e8f0; font-size: 13px; }}
th {{ text-align: left; color: #64748b; font-size: 11px; text-transform: uppercase; }}
.totals td {{ font-weight: 600; border-top: 2px solid #1e293b; border-bottom: none; }}
.meta {{ color: #64748b; font-size: 12px; }}
</style></head><body>
<h1>Offerte: {esc(quote['title'])}</h1>
<p class="meta">Voor: {esc(quote['client_name'])} &middot; Geldig tot: {esc(quote['valid_until'])}</p>
{intro_html}
<table><thead><tr><th>Omschrijving</th><th style="text-align:right">Aantal</th>
<th style="text-align:right">Prijs</th><th style="text-align:right">Bedrag</th></tr></thead>
<tbody>{rows}
<tr><td colspan="3" style="text-align:right">Subtotaal</td><td style="text-align:right">€{quote['subtotal_cents'] / 100:,.2f}</td></tr>
<tr><td colspan="3" style="text-align:right">BTW ({quote['vat_percent']}%)</td><td style="text-align:right">€{quote['vat_cents'] / 100:,.2f}</td></tr>
<tr class="totals"><td colspan="3" style="text-align:right">Totaal</td><td style="text-align:right">€{quote['total_cents'] / 100:,.2f}</td></tr>
</tbody></table>
<p class="meta" style="margin-top:30px">WeAreImpact &middot; Vincent van Munster &middot; v.munster@weareimpact.nl &middot; 06-14470977</p>
</body></html>"""


def send_quote(quote_id: str, to_email: str = "") -> Dict[str, Any]:
    ensure_schema()
    quote = get_quote(quote_id)
    if not quote:
        raise ValueError("Offerte niet gevonden")
    email = (to_email or quote.get("client_email") or "").strip()
    if not email:
        raise ValueError("Geen e-mailadres bekend voor deze offerte")

    from ...shared import resend_service
    html = render_quote_html(quote)
    ok = resend_service.send_html(
        subject=f"Offerte: {quote['title']}", html=html, to=email,
    )
    if not ok:
        raise ValueError("Versturen mislukt (Resend niet geconfigureerd of foutmelding, zie logs)")
    with get_conn() as conn:
        conn.execute(
            "UPDATE quotes SET status = 'verstuurd', sent_at = ?, client_email = ? WHERE id = ?",
            (_now(), email, quote_id),
        )
    log_outcome(
        "WeAreImpact", "quote_verstuurd",
        f"Offerte '{quote['title']}' verstuurd aan {quote['client_name']} ({email}), "
        f"totaal EUR {quote['total_cents'] / 100:.2f}.",
        artifact=f"/api/quotes/{quote_id}/html",
    )
    return get_quote(quote_id)


def markeer_beslissing(quote_id: str, status: str) -> Dict[str, Any]:
    """Vincent zet dit zelf op basis van het antwoord van de klant — er is
    geen e-sign-koppeling die dit kan waarnemen (zie models.py)."""
    ensure_schema()
    if status not in ("geaccepteerd", "afgewezen"):
        raise ValueError(f"Onbekende beslissing: {status}")
    quote = get_quote(quote_id)
    if not quote:
        raise ValueError("Offerte niet gevonden")
    with get_conn() as conn:
        conn.execute(
            "UPDATE quotes SET status = ?, decided_at = ? WHERE id = ?",
            (status, _now(), quote_id),
        )
    if status == "geaccepteerd" and quote.get("deal_id"):
        try:
            from ..crm import service as crm_service
            crm_service.update_deal_stage(quote["deal_id"], "gewonnen")
        except Exception:  # noqa: BLE001 — de offerte-beslissing zelf mag nooit falen op de CRM-koppeling
            log.exception("[quotes] Kon gekoppelde deal niet bijwerken na acceptatie")
    log_outcome(
        "WeAreImpact", "quote_beslissing",
        f"Offerte '{quote['title']}' voor {quote['client_name']}: {status}.",
    )
    return get_quote(quote_id)
