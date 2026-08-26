"""
Facturatie-service.

Drie stromen, drie betrouwbaarheidsniveaus (zie models.py-docstring voor de
reden: DigiBoox heeft geen API):

  1. Bonnetjes            volautomatisch doorgestuurd naar DigiBoox' eigen
                           OCR-mailadres. Wij lezen het bedrag hier NIET —
                           dat zou een tweede, minder betrouwbare bewering
                           naast DigiBoox' eigen ScanPilot zetten.
  2. Uren -> factuur       agenda-uren zijn een aanname (geblokkeerd ≠
                           gewerkt), dus altijd een concept tot Vincent hem
                           bevestigt; export is een bestand dat hij zelf in
                           DigiBoox importeert, nooit een beweerde boeking.
  3. Debiteuren            gebouwd op een periodieke snapshot (Vincent
                           exporteert zelf uit DigiBoox), nooit op een live
                           stand. Een herinnering op een snapshot ouder dan
                           BILLING_DEBTOR_SNAPSHOT_STALE_DAYS is verboden —
                           dat zou een al betaalde factuur kunnen aanmanen.

Niets in dit bestand verstuurt of exporteert ooit zonder een expliciete
goedkeur-aanroep vanuit de UI (Wachtrij-gate, net als content en outreach).
"""
from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...shared.config import (
    DATA_DIR,
    DIGIBOOX_RECEIPT_EMAIL,
    BILLING_DEBTOR_SNAPSHOT_STALE_DAYS,
)
from ...shared.database import get_conn
from ...shared.outcomes import log_outcome
from ...shared.projects import squash_project
from .models import ensure_schema

log = logging.getLogger(__name__)

RECEIPTS_DIR = DATA_DIR / "uploads" / "billing_receipts"
EXPORTS_DIR = DATA_DIR / "billing_exports"

TONE_VRIENDELIJK = "vriendelijk"
TONE_DRINGEND = "dringend"
TONE_AANMANING = "aanmaning"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row(r) -> Dict[str, Any]:
    return dict(r) if r is not None else {}


def digiboox_forwarding_enabled() -> bool:
    return bool(DIGIBOOX_RECEIPT_EMAIL)


# ── 1. Bonnetjes / inkoopfacturen ───────────────────────────────────────

def ontvang_bonnetje(filename: str, content: bytes, *, source: str = "upload") -> Dict[str, Any]:
    """Sla het bestand op en probeer het meteen door te sturen naar DigiBoox.

    Mislukt het versturen (geen adres ingesteld, Resend-fout), dan blijft de
    rij op 'mislukt' staan — zichtbaar in het Actiecentrum via de invariant
    `bonnetje_niet_doorgestuurd`, nooit stil weggegooid."""
    ensure_schema()
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    rid = str(uuid.uuid4())
    ext = Path(filename or "bonnetje").suffix or ".bin"
    dest = RECEIPTS_DIR / f"{rid}{ext}"
    dest.write_bytes(content)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO billing_receipts (id, source, filename, file_path, status, created_at) "
            "VALUES (?, ?, ?, ?, 'nieuw', ?)",
            (rid, source, filename or dest.name, str(dest), now),
        )
    return forward_bonnetje(rid)


def forward_bonnetje(receipt_id: str) -> Dict[str, Any]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM billing_receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not row:
        raise ValueError("Bonnetje niet gevonden")
    receipt = _row(row)

    if not digiboox_forwarding_enabled():
        # Nog niet geconfigureerd is geen storing (Vincent heeft het adres
        # simpelweg nog niet ingevuld) — het bonnetje blijft op 'nieuw' staan
        # i.p.v. een rode kaart te worden. Zodra DIGIBOOX_RECEIPT_EMAIL wordt
        # ingevuld, pakt `retry_failed_receipts()` 'm niet automatisch op
        # (status is geen 'mislukt'); de lijst in de Facturatie-tab toont 'm
        # gewoon als wachtend, met een handmatige verstuurknop.
        return get_receipt(receipt_id)

    from ...shared import resend_service
    path = Path(receipt["file_path"])
    if not path.exists():
        _mark_receipt_failed(receipt_id, "Bestand ontbreekt op schijf")
        return get_receipt(receipt_id)

    ok = resend_service.send_html(
        subject=f"Bonnetje: {receipt['filename']}",
        html=f"<p>Automatisch doorgestuurd vanuit Impact OS.</p><p>Bestand: {receipt['filename']}</p>",
        to=DIGIBOOX_RECEIPT_EMAIL,
        attachments=[{"filename": receipt["filename"], "content": path.read_bytes()}],
    )
    if ok:
        with get_conn() as conn:
            conn.execute(
                "UPDATE billing_receipts SET status = 'doorgestuurd', forwarded_at = ?, "
                "forward_error = '' WHERE id = ?",
                (_now(), receipt_id),
            )
        log_outcome(
            "WeAreImpact", "billing_bonnetje_doorgestuurd",
            f"Bonnetje '{receipt['filename']}' doorgestuurd naar DigiBoox.",
            artifact=receipt["file_path"],
        )
    else:
        _mark_receipt_failed(receipt_id, "Versturen via Resend mislukt (zie logs)")
    return get_receipt(receipt_id)


def _mark_receipt_failed(receipt_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE billing_receipts SET status = 'mislukt', forward_error = ? WHERE id = ?",
            (error, receipt_id),
        )
        row = conn.execute("SELECT filename FROM billing_receipts WHERE id = ?", (receipt_id,)).fetchone()
    log_outcome(
        "WeAreImpact", "billing_bonnetje_doorsturen_mislukt",
        f"Bonnetje '{row['filename'] if row else receipt_id}' kon niet worden doorgestuurd: {error}",
        status="error",
        next_step="Open Facturatie en probeer opnieuw, of los de oorzaak op.",
    )


def get_receipt(receipt_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM billing_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()) or None


def list_receipts(status: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM billing_receipts WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM billing_receipts ORDER BY created_at DESC"
            ).fetchall()
        return [_row(r) for r in rows]


def retry_failed_receipts() -> int:
    """Wordt door de scheduler aangeroepen — probeert mislukte bonnetjes
    opnieuw te versturen (bv. nadat het mailadres alsnog is ingevuld)."""
    ensure_schema()
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM billing_receipts WHERE status = 'mislukt'"
        ).fetchall()]
    for rid in ids:
        forward_bonnetje(rid)
    return len(ids)


# ── 2. Uren -> conceptfactuur ────────────────────────────────────────────

def _matcht_klant(klant_key: str, tekst: str) -> bool:
    tekst_key = squash_project(tekst or "")
    return bool(klant_key) and klant_key in tekst_key


async def genereer_uren_factuur_concept(
    client_name: str, period_start: str, period_end: str, *,
    hourly_rate_cents: int = 0, vat_percent: int = 21,
) -> Dict[str, Any]:
    """Agenda-events in de periode die op klantnaam matchen -> conceptregels.

    Puur een aanname op basis van geblokkeerde tijd — nooit een factuur, altijd
    een `concept` die Vincent moet nalopen (regels uitsluiten kan) vóór hij 'm
    goedkeurt en exporteert."""
    from ..calendar import service as calendar_service

    ensure_schema()
    start_dt = datetime.fromisoformat(period_start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(period_end).replace(tzinfo=timezone.utc) + timedelta(days=1)

    klant_key = squash_project(client_name)
    lines: List[Dict[str, Any]] = []
    if calendar_service.is_configured():
        data = await calendar_service.get_events_range(start_dt, end_dt)
        for ev in data.get("events", []):
            if ev.get("all_day"):
                continue
            titel = ev.get("summary") or ""
            if not _matcht_klant(klant_key, titel) and not _matcht_klant(klant_key, ev.get("description") or ""):
                continue
            try:
                s = datetime.fromisoformat((ev["start"] or "").replace("Z", "+00:00"))
                e = datetime.fromisoformat((ev["end"] or "").replace("Z", "+00:00"))
                uren = round((e - s).total_seconds() / 3600, 2)
            except (ValueError, TypeError, KeyError):
                continue
            if uren <= 0:
                continue
            lines.append({
                "description": titel,
                "event_date": (ev.get("start") or "")[:10],
                "hours": uren,
                "calendar_event_id": ev.get("id") or "",
            })

    did = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO billing_invoice_drafts (id, client_name, period_start, period_end, "
            "hourly_rate_cents, vat_percent, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'concept', ?)",
            (did, client_name, period_start, period_end, hourly_rate_cents, vat_percent, now),
        )
        for line in lines:
            conn.execute(
                "INSERT INTO billing_invoice_lines (id, draft_id, description, event_date, "
                "hours, calendar_event_id, excluded) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (str(uuid.uuid4()), did, line["description"], line["event_date"],
                 line["hours"], line["calendar_event_id"]),
            )
    return get_invoice_draft(did)


def get_invoice_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        draft = _row(conn.execute(
            "SELECT * FROM billing_invoice_drafts WHERE id = ?", (draft_id,)
        ).fetchone())
        if not draft:
            return None
        lines = [_row(r) for r in conn.execute(
            "SELECT * FROM billing_invoice_lines WHERE draft_id = ? ORDER BY event_date",
            (draft_id,),
        ).fetchall()]
    draft["lines"] = lines
    draft["total_hours"] = round(sum(l["hours"] for l in lines if not l["excluded"]), 2)
    draft["total_amount_cents"] = round(draft["total_hours"] * draft["hourly_rate_cents"])
    return draft


def list_invoice_drafts(status: str = "") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT id FROM billing_invoice_drafts WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM billing_invoice_drafts ORDER BY created_at DESC"
            ).fetchall()
    return [get_invoice_draft(r["id"]) for r in rows]


def set_line_excluded(line_id: str, excluded: bool) -> None:
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            "UPDATE billing_invoice_lines SET excluded = ? WHERE id = ?",
            (1 if excluded else 0, line_id),
        )


def keur_factuur_goed(draft_id: str) -> Dict[str, Any]:
    """Genereert een CSV-exportbestand in DigiBoox-vriendelijke kolommen.

    Dit is GEEN bevestiging dat de factuur in DigiBoox staat — dat weten we
    zonder API niet. Het is het bestand dat Vincent zelf met de Excel-
    importwizard invoert; `status` gaat naar 'geexporteerd', nooit 'geboekt'."""
    ensure_schema()
    draft = get_invoice_draft(draft_id)
    if not draft:
        raise ValueError("Conceptfactuur niet gevonden")
    if draft["status"] != "concept":
        raise ValueError(f"Factuur staat al op '{draft['status']}'")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    export_path = EXPORTS_DIR / f"factuur-{squash_project(draft['client_name'])}-{draft['period_start']}.csv"
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Klant", "Omschrijving", "Datum", "Aantal", "Eenheid", "Tarief", "Bedrag", "BTW%"])
    for line in draft["lines"]:
        if line["excluded"]:
            continue
        bedrag = round(line["hours"] * draft["hourly_rate_cents"]) / 100
        writer.writerow([
            draft["client_name"], line["description"], line["event_date"],
            line["hours"], "uur", draft["hourly_rate_cents"] / 100, bedrag, draft["vat_percent"],
        ])
    export_path.write_text(buf.getvalue(), encoding="utf-8-sig")

    now = _now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE billing_invoice_drafts SET status = 'geexporteerd', approved_at = ?, "
            "export_path = ? WHERE id = ?",
            (now, str(export_path), draft_id),
        )
    log_outcome(
        "WeAreImpact", "billing_factuur_export",
        f"Factuurconcept voor {draft['client_name']} ({draft['period_start']} t/m "
        f"{draft['period_end']}) geexporteerd: {draft['total_hours']} uur, "
        f"EUR {draft['total_amount_cents'] / 100:.2f}.",
        artifact=str(export_path),
        next_step="Importeer dit CSV-bestand in DigiBoox via de Excel-importwizard.",
    )
    return get_invoice_draft(draft_id)


def verwerp_factuur(draft_id: str) -> None:
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM billing_invoice_lines WHERE draft_id = ?", (draft_id,)
        )
        conn.execute("DELETE FROM billing_invoice_drafts WHERE id = ?", (draft_id,))


# ── 3. Debiteurenbeheer ───────────────────────────────────────────────────

_KOLOM_ALIASSEN = {
    "client_name": ["klant", "client", "debiteur", "naam", "customer"],
    "invoice_number": ["factuurnummer", "factuur", "invoice", "invoicenumber"],
    "invoice_date": ["factuurdatum", "datum", "invoicedate"],
    "due_date": ["vervaldatum", "duedate", "due"],
    "amount_cents": ["bedrag", "openstaand", "amount", "openstaandbedrag"],
    "email": ["email", "e-mail", "mailadres"],
}


def _vind_kolom(headers: List[str], veld: str) -> Optional[int]:
    genormaliseerd = [squash_project(h) for h in headers]
    for alias in _KOLOM_ALIASSEN[veld]:
        alias_n = squash_project(alias)
        if alias_n in genormaliseerd:
            return genormaliseerd.index(alias_n)
    return None


def importeer_debiteuren_snapshot(filename: str, content: bytes) -> Dict[str, Any]:
    """Leest een door Vincent uit DigiBoox geëxporteerde CSV van openstaande
    posten. Kolomnamen worden flexibel herkend (NL/EN aliassen), want het
    exacte DigiBoox-exportformaat ligt niet vast zonder API-documentatie."""
    ensure_schema()
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = list(reader)
    if not rows:
        raise ValueError("Leeg bestand")
    headers = rows[0]
    idx = {v: _vind_kolom(headers, v) for v in _KOLOM_ALIASSEN}
    if idx["client_name"] is None or idx["amount_cents"] is None:
        raise ValueError(
            "Kon geen klant- en/of bedragkolom herkennen. Verwachte kolomnamen "
            "(één van elk): klant/naam, bedrag/openstaand."
        )

    sid = str(uuid.uuid4())
    now = _now()
    parsed = 0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO billing_debtor_snapshots (id, filename, row_count, imported_at) "
            "VALUES (?, ?, 0, ?)",
            (sid, filename, now),
        )
        for row in rows[1:]:
            if not row or not any(c.strip() for c in row):
                continue
            def get(field):
                i = idx[field]
                return row[i].strip() if i is not None and i < len(row) else ""
            klant = get("client_name")
            if not klant:
                continue
            bedrag_raw = get("amount_cents").replace("€", "").replace(".", "").replace(",", ".").strip()
            try:
                bedrag_cents = round(float(bedrag_raw) * 100) if bedrag_raw else 0
            except ValueError:
                bedrag_cents = 0
            conn.execute(
                "INSERT INTO billing_debtor_rows (id, snapshot_id, client_name, invoice_number, "
                "invoice_date, due_date, amount_cents, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), sid, klant, get("invoice_number"), get("invoice_date"),
                 get("due_date"), bedrag_cents, get("email")),
            )
            parsed += 1
        conn.execute(
            "UPDATE billing_debtor_snapshots SET row_count = ? WHERE id = ?",
            (parsed, sid),
        )
    log_outcome(
        "WeAreImpact", "billing_debiteuren_import",
        f"Debiteuren-snapshot geimporteerd: {parsed} openstaande posten uit '{filename}'.",
        artifact=filename,
    )
    return get_latest_snapshot()


def get_latest_snapshot() -> Optional[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        snap = _row(conn.execute(
            "SELECT * FROM billing_debtor_snapshots ORDER BY imported_at DESC LIMIT 1"
        ).fetchone())
        if not snap:
            return None
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM billing_debtor_rows WHERE snapshot_id = ? ORDER BY due_date",
            (snap["id"],),
        ).fetchall()]
    snap["rows"] = rows
    return snap


def snapshot_stale_days() -> Optional[int]:
    """Hoeveel dagen oud is de laatste debiteuren-snapshot? None = er is er
    nog nooit een geïmporteerd."""
    snap = get_latest_snapshot()
    if not snap:
        return None
    imported = datetime.fromisoformat(snap["imported_at"])
    return (datetime.now(timezone.utc) - imported).days


def snapshot_is_stale() -> bool:
    days = snapshot_stale_days()
    return days is None or days > BILLING_DEBTOR_SNAPSHOT_STALE_DAYS


def _tone_voor(dagen_te_laat: int) -> str:
    if dagen_te_laat > 30:
        return TONE_AANMANING
    if dagen_te_laat > 14:
        return TONE_DRINGEND
    return TONE_VRIENDELIJK


def _reminder_tekst(klant: str, tone: str, bedrag_eur: float, factuurnummer: str, dagen: int) -> tuple:
    bedrag = f"EUR {bedrag_eur:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if tone == TONE_VRIENDELIJK:
        subject = f"Even een vriendelijke herinnering, factuur {factuurnummer}"
        body = (
            f"Beste {klant},\n\n"
            f"Waarschijnlijk over het hoofd gezien: factuur {factuurnummer} ter hoogte van {bedrag} "
            f"staat nog open sinds {dagen} dagen. Zou je die deze week kunnen voldoen?\n\n"
            f"Bij vragen hoor ik het graag.\n\nMet vriendelijke groet,\nVincent van Munster"
        )
    elif tone == TONE_DRINGEND:
        subject = f"Openstaande factuur {factuurnummer}, graag deze week regelen"
        body = (
            f"Beste {klant},\n\n"
            f"Factuur {factuurnummer} ({bedrag}) staat inmiddels {dagen} dagen open. Ik vraag je "
            f"vriendelijk maar dringend om deze deze week te voldoen.\n\n"
            f"Loopt er iets waardoor betaling niet lukt, laat het me dan weten.\n\n"
            f"Met vriendelijke groet,\nVincent van Munster"
        )
    else:
        subject = f"Aanmaning factuur {factuurnummer}"
        body = (
            f"Beste {klant},\n\n"
            f"Ondanks eerdere herinneringen staat factuur {factuurnummer} ({bedrag}) nog steeds open, "
            f"inmiddels {dagen} dagen. Dit is een aanmaning: ik verzoek je het bedrag binnen 7 dagen "
            f"te voldoen.\n\nNeem bij vragen contact op.\n\nMet vriendelijke groet,\nVincent van Munster"
        )
    return subject, body


def genereer_herinneringen() -> List[Dict[str, Any]]:
    """Maakt herinnerings-/aanmaningsconcepten voor elke openstaande post
    voorbij de vervaldatum in de laatste snapshot.

    Blokkerend als de snapshot te oud is (`snapshot_is_stale`) — een
    herinnering op een verouderde stand kan een al betaalde factuur aanmanen."""
    ensure_schema()
    if snapshot_is_stale():
        raise ValueError(
            "Debiteuren-snapshot ontbreekt of is ouder dan "
            f"{BILLING_DEBTOR_SNAPSHOT_STALE_DAYS} dagen. Importeer eerst een verse export "
            "uit DigiBoox voordat er herinneringen worden gemaakt."
        )
    snap = get_latest_snapshot()
    today = datetime.now(timezone.utc).date()
    gemaakt: List[Dict[str, Any]] = []
    with get_conn() as conn:
        bestaande = {
            r["debtor_row_id"] for r in conn.execute(
                "SELECT debtor_row_id FROM billing_reminders WHERE status != 'overgeslagen'"
            ).fetchall()
        }
        for row in snap["rows"]:
            if row["id"] in bestaande or not row["due_date"] or row["amount_cents"] <= 0:
                continue
            try:
                vervaldatum = datetime.fromisoformat(row["due_date"]).date()
            except ValueError:
                continue
            dagen_te_laat = (today - vervaldatum).days
            if dagen_te_laat <= 0:
                continue
            tone = _tone_voor(dagen_te_laat)
            subject, body = _reminder_tekst(
                row["client_name"], tone, row["amount_cents"] / 100,
                row["invoice_number"] or "onbekend", dagen_te_laat,
            )
            rid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO billing_reminders (id, debtor_row_id, client_name, days_overdue, "
                "tone, subject, draft, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'review', ?)",
                (rid, row["id"], row["client_name"], dagen_te_laat, tone, subject, body, _now()),
            )
            gemaakt.append(_row(conn.execute(
                "SELECT * FROM billing_reminders WHERE id = ?", (rid,)
            ).fetchone()))
    return gemaakt


def list_reminders(status: str = "review") -> List[Dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM billing_reminders WHERE status = ? ORDER BY days_overdue DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM billing_reminders ORDER BY days_overdue DESC"
            ).fetchall()
        return [_row(r) for r in rows]


def keur_herinnering_goed(reminder_id: str) -> Dict[str, Any]:
    ensure_schema()
    with get_conn() as conn:
        reminder = _row(conn.execute(
            "SELECT * FROM billing_reminders WHERE id = ?", (reminder_id,)
        ).fetchone())
        if not reminder:
            raise ValueError("Herinnering niet gevonden")
        debtor_row = _row(conn.execute(
            "SELECT * FROM billing_debtor_rows WHERE id = ?", (reminder["debtor_row_id"],)
        ).fetchone())
    email = (debtor_row or {}).get("email") or ""
    if not email:
        raise ValueError("Geen e-mailadres bekend voor deze debiteur, kan niet versturen")

    from ...shared import resend_service
    ok = resend_service.send_html(
        subject=reminder["subject"],
        html="<pre style='font-family:inherit;white-space:pre-wrap'>" + reminder["draft"] + "</pre>",
        to=email,
        text=reminder["draft"],
    )
    if not ok:
        raise ValueError("Versturen mislukt (Resend niet geconfigureerd of foutmelding, zie logs)")
    with get_conn() as conn:
        conn.execute(
            "UPDATE billing_reminders SET status = 'verstuurd', sent_at = ? WHERE id = ?",
            (_now(), reminder_id),
        )
    log_outcome(
        "WeAreImpact", "billing_herinnering_verstuurd",
        f"Herinnering ({reminder['tone']}) verstuurd aan {reminder['client_name']}.",
        artifact=f"mailto:{email}",
    )
    return list_reminders("verstuurd")[0]


def sla_herinnering_over(reminder_id: str) -> None:
    ensure_schema()
    with get_conn() as conn:
        conn.execute(
            "UPDATE billing_reminders SET status = 'overgeslagen' WHERE id = ?",
            (reminder_id,),
        )
