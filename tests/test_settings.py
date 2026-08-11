"""instance_settings key/value store + Google Calendar DB-override.

Deze twee stukken zijn kleine, generieke bouwstenen die blijven staan naast
de latere, veel completere Iris-onboarding-wizard (backend/domains/onboarding)
die daadwerkelijk per-klant OAuth doet. Het los-aggregerende "Instellingen"-
overzicht dat hier eerder stond is bewust ingetrokken — het dupliceerde
GET /api/onboarding/{site_id} (dezelfde "wat mist er nog"-vraag, twee
antwoorden)."""
import pytest


# ── instance_settings key/value store ───────────────────────────────────────

def test_setting_roundtrip_and_env_fallback():
    from backend.shared import settings_store

    assert settings_store.get_setting("een_nieuwe_sleutel", default="fallback") == "fallback"
    settings_store.set_setting("een_nieuwe_sleutel", "echte-waarde")
    assert settings_store.get_setting("een_nieuwe_sleutel") == "echte-waarde"
    settings_store.clear_setting("een_nieuwe_sleutel")
    assert settings_store.get_setting("een_nieuwe_sleutel", default="fallback") == "fallback"


def test_setting_overwrite_updates_cache():
    from backend.shared import settings_store

    settings_store.set_setting("dubbel_gezet", "eerst")
    settings_store.set_setting("dubbel_gezet", "daarna")
    assert settings_store.get_setting("dubbel_gezet") == "daarna"
    settings_store.clear_setting("dubbel_gezet")


# ── Google Calendar: DB-override wint van env ───────────────────────────────

def test_calendar_id_db_override_wins_over_env(monkeypatch):
    from backend.domains.calendar import service_google as cal
    from backend.shared import settings_store

    monkeypatch.setattr(cal, "CALENDAR_CALENDAR_ID", "env-agenda@example.com")
    settings_store.clear_setting("calendar_calendar_id")
    assert cal._cal_id() == "env-agenda@example.com"

    settings_store.set_setting("calendar_calendar_id", "nicole-agenda@example.com")
    assert cal._cal_id() == "nicole-agenda@example.com"
    settings_store.clear_setting("calendar_calendar_id")


def test_busy_ids_db_override_wins_over_env(monkeypatch):
    from backend.domains.calendar import service_google as cal
    from backend.shared import settings_store

    monkeypatch.setattr(cal, "CALENDAR_BUSY_CALENDAR_IDS", ["env-a", "env-b"])
    settings_store.clear_setting("calendar_busy_ids")
    assert cal._busy_cal_ids() == ["env-a", "env-b"]

    settings_store.set_setting("calendar_busy_ids", "nicole-a@example.com, nicole-b@example.com")
    assert cal._busy_cal_ids() == ["nicole-a@example.com", "nicole-b@example.com"]
    settings_store.clear_setting("calendar_busy_ids")
