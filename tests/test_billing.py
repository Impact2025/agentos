"""Facturatie — tests voor de drie stromen (bonnetjes, uren-conceptfactuur,
debiteurenbeheer) zonder DigiBoox-API of echte mail te raken.

Kern die getoetst wordt:
  - een bonnetje zonder ingesteld DigiBoox-adres blijft 'nieuw', wordt nooit
    stil als 'mislukt' gemeld voor iets wat gewoon nog niet is ingesteld
  - agenda-uren zijn altijd een concept, nooit een boeking
  - een conceptfactuur kan maar één keer goedgekeurd worden
  - een herinnering kan nooit gemaakt worden op een ontbrekende/verouderde
    debiteuren-snapshot
  - de toon van een herinnering trapt op met het aantal dagen te laat
"""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest


def test_ontvang_bonnetje_without_config_stays_nieuw(clean_tables, monkeypatch):
    from backend.domains.billing import service as billing_service
    monkeypatch.setattr(billing_service, "DIGIBOOX_RECEIPT_EMAIL", "")

    receipt = billing_service.ontvang_bonnetje("bon.png", b"fake-image-bytes")
    assert receipt["status"] == "nieuw"
    assert receipt["forward_error"] == ""


def test_forward_bonnetje_marks_failed_and_logs_error_on_missing_file(clean_tables, monkeypatch):
    from backend.domains.billing import service as billing_service
    from backend.shared.database import get_conn
    monkeypatch.setattr(billing_service, "DIGIBOOX_RECEIPT_EMAIL", "boekhouding@digiboox.example")

    receipt = billing_service.ontvang_bonnetje("bon.png", b"fake-image-bytes")
    # Bestand handmatig weghalen om de mislukte-pad te forceren.
    import os
    os.remove(receipt["file_path"])

    result = billing_service.forward_bonnetje(receipt["id"])
    assert result["status"] == "mislukt"
    assert "ontbreekt" in result["forward_error"]

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM activity_log WHERE action = 'billing_bonnetje_doorsturen_mislukt'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "error"


def test_matcht_klant_is_substring_on_squashed_text():
    from backend.domains.billing.service import _matcht_klant
    from backend.shared.projects import squash_project

    key = squash_project("Bedrijf B.V.")
    assert _matcht_klant(key, "Call met Bedrijf B.V. over project X")
    assert not _matcht_klant(key, "Iets heel anders")


@pytest.mark.asyncio
async def test_genereer_uren_factuur_concept_zonder_agenda_is_leeg_concept(clean_tables):
    from backend.domains.billing import service as billing_service

    draft = await billing_service.genereer_uren_factuur_concept(
        "Testklant", "2026-08-01", "2026-08-31", hourly_rate_cents=12500,
    )
    assert draft["status"] == "concept"
    assert draft["lines"] == []
    assert draft["total_hours"] == 0
    assert draft["total_amount_cents"] == 0


@pytest.mark.asyncio
async def test_factuur_goedkeuren_exporteert_csv_en_is_eenmalig(clean_tables, tmp_path, monkeypatch):
    from backend.domains.billing import service as billing_service
    monkeypatch.setattr(billing_service, "EXPORTS_DIR", tmp_path)

    draft = await billing_service.genereer_uren_factuur_concept(
        "Testklant", "2026-08-01", "2026-08-31", hourly_rate_cents=10000,
    )
    approved = billing_service.keur_factuur_goed(draft["id"])
    assert approved["status"] == "geexporteerd"
    assert approved["export_path"]
    import os
    assert os.path.exists(approved["export_path"])

    with pytest.raises(ValueError):
        billing_service.keur_factuur_goed(draft["id"])


def test_importeer_debiteuren_snapshot_herkent_nederlandse_kolommen(clean_tables):
    from backend.domains.billing import service as billing_service

    csv_content = (
        "Klant;Factuurnummer;Vervaldatum;Bedrag;E-mail\n"
        "Acme BV;2026-042;2026-08-01;1.234,56;debiteur@acme.nl\n"
    ).encode("utf-8-sig")
    snap = billing_service.importeer_debiteuren_snapshot("export.csv", csv_content)
    assert snap["row_count"] == 1
    assert snap["rows"][0]["client_name"] == "Acme BV"
    assert snap["rows"][0]["amount_cents"] == 123456
    assert snap["rows"][0]["email"] == "debiteur@acme.nl"


def test_importeer_debiteuren_snapshot_zonder_herkenbare_kolommen_faalt(clean_tables):
    from backend.domains.billing import service as billing_service
    with pytest.raises(ValueError):
        billing_service.importeer_debiteuren_snapshot(
            "raar.csv", "Foo;Bar\n1;2\n".encode("utf-8"),
        )


def test_snapshot_is_stale_zonder_import(clean_tables):
    from backend.domains.billing import service as billing_service
    assert billing_service.snapshot_stale_days() is None
    assert billing_service.snapshot_is_stale() is True


def test_genereer_herinneringen_blokkeert_zonder_snapshot(clean_tables):
    from backend.domains.billing import service as billing_service
    with pytest.raises(ValueError):
        billing_service.genereer_herinneringen()


def _importeer_met_vervaldatum(billing_service, dagen_te_laat: int, klant: str = "Acme BV"):
    vervaldatum = (datetime.now(timezone.utc).date() - timedelta(days=dagen_te_laat)).isoformat()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["klant", "factuurnummer", "vervaldatum", "bedrag", "email"])
    writer.writerow([klant, "F-1", vervaldatum, "500,00", "klant@acme.nl"])
    return billing_service.importeer_debiteuren_snapshot("export.csv", buf.getvalue().encode("utf-8"))


def test_genereer_herinneringen_toon_trapt_op_met_dagen_te_laat(clean_tables):
    from backend.domains.billing import service as billing_service

    _importeer_met_vervaldatum(billing_service, 5, klant="Vriendelijk BV")
    reminders = billing_service.genereer_herinneringen()
    assert len(reminders) == 1
    assert reminders[0]["tone"] == billing_service.TONE_VRIENDELIJK

    billing_service.sla_herinnering_over(reminders[0]["id"])
    _importeer_met_vervaldatum(billing_service, 20, klant="Dringend BV")
    reminders2 = billing_service.genereer_herinneringen()
    dringend = [r for r in reminders2 if r["client_name"] == "Dringend BV"]
    assert dringend and dringend[0]["tone"] == billing_service.TONE_DRINGEND

    _importeer_met_vervaldatum(billing_service, 40, klant="Aanmaning BV")
    reminders3 = billing_service.genereer_herinneringen()
    aanmaning = [r for r in reminders3 if r["client_name"] == "Aanmaning BV"]
    assert aanmaning and aanmaning[0]["tone"] == billing_service.TONE_AANMANING


def test_genereer_herinneringen_is_idempotent_per_debtor_row(clean_tables):
    from backend.domains.billing import service as billing_service

    _importeer_met_vervaldatum(billing_service, 10)
    first = billing_service.genereer_herinneringen()
    assert len(first) == 1
    second = billing_service.genereer_herinneringen()
    assert second == []


def test_genereer_herinneringen_geen_reminder_binnen_betaaltermijn(clean_tables):
    from backend.domains.billing import service as billing_service
    _importeer_met_vervaldatum(billing_service, -3)  # vervaldatum in de toekomst
    assert billing_service.genereer_herinneringen() == []


def test_keur_herinnering_goed_faalt_zonder_email(clean_tables):
    from backend.domains.billing import service as billing_service
    from backend.shared.database import get_conn

    _importeer_met_vervaldatum(billing_service, 10)
    with get_conn() as conn:
        conn.execute("UPDATE billing_debtor_rows SET email = ''")
    reminders = billing_service.genereer_herinneringen()
    with pytest.raises(ValueError):
        billing_service.keur_herinnering_goed(reminders[0]["id"])


def test_action_center_shows_billing_items(clean_tables):
    from backend.domains.billing import service as billing_service
    from backend.domains.action_center.service import build_inbox

    _importeer_met_vervaldatum(billing_service, 10)
    reminders = billing_service.genereer_herinneringen()
    assert reminders

    items = build_inbox()["items"]
    reminder_items = [i for i in items if i["kind"] == "billing_reminder_review"]
    assert len(reminder_items) == 1
    action_types = {a["type"] for a in reminder_items[0]["actions"]}
    assert {"billing_reminder_send", "billing_reminder_skip"} <= action_types


def test_debiteuren_snapshot_verouderd_invariant(clean_tables, monkeypatch):
    from backend.domains.billing import service as billing_service
    from backend.domains.iris import integrity

    monkeypatch.setattr(billing_service, "BILLING_DEBTOR_SNAPSHOT_STALE_DAYS", 1)
    _importeer_met_vervaldatum(billing_service, 5)

    from backend.shared.database import get_conn
    oud = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE billing_debtor_snapshots SET imported_at = ?", (oud,))

    bevindingen = integrity._check_debiteuren_snapshot_verouderd()
    assert len(bevindingen) == 1
    assert bevindingen[0].subject == "debiteuren_snapshot"
