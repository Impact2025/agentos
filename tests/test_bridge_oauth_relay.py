"""Bridge-commando's voor de naar Iris Remote verhuisde onboarding-wizard.

`oauth_token_relay` is de gevoeligste: hij draagt een refresh-token die
`remote/api/oauth.js` na een geslaagde Google/Microsoft-consent-redirect als
Bridge-decision aanlevert (nooit een tik op de telefoon zelf, zie
backend/domains/bridge/actions.py). De onboarding_step*-commando's zijn de
kale doorgeefluiken naar `backend/domains/onboarding/service.py` — dezelfde
functies die de oude lokale wizard al aanriep, nu alleen via het
commando-pad i.p.v. een directe HTTP-call.
"""
import pytest


@pytest.fixture()
def site(clean_tables):
    from backend.domains.seo import sites as sites_service
    s = sites_service.create_site({"name": "BridgeOAuthTestklant"})
    yield s
    from backend.shared.database import get_conn
    with get_conn() as c:
        c.execute("DELETE FROM sites WHERE id = ?", (s["id"],))
        c.execute("DELETE FROM oauth_accounts WHERE site_id = ?", (s["id"],))
        c.execute("DELETE FROM project_autonomy WHERE project = ?", (s["name"],))
        c.execute("DELETE FROM iris_knowledge WHERE lower(scope) = lower(?)", (s["name"],))


# ── oauth_token_relay ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_token_relay_upserts_oauth_accounts(site):
    from backend.domains.bridge import actions
    from backend.shared.database import get_conn

    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "oauth_token_relay",
        "payload": {
            "site_id": site["id"], "provider": "google", "account_email": "nicole@example.com",
            "credentials": {"access_token": "at", "refresh_token": "rt", "expiry": "2026-08-11T00:00:00+00:00"},
            "scopes": ["openid", "email"],
        },
    })
    assert ok
    assert "google" in msg.lower()

    with get_conn() as c:
        row = c.execute(
            "SELECT account_email, credentials_json FROM oauth_accounts WHERE site_id = ? AND provider = 'google'",
            (site["id"],),
        ).fetchone()
    assert row["account_email"] == "nicole@example.com"
    assert "rt" in row["credentials_json"]


@pytest.mark.asyncio
async def test_oauth_token_relay_reconnect_overwrites(site):
    from backend.domains.bridge import actions
    from backend.shared.database import get_conn

    payload_base = {
        "site_id": site["id"], "provider": "google", "account_email": "eerste@example.com",
        "credentials": {"access_token": "at1", "refresh_token": "rt1", "expiry": "2026-08-11T00:00:00+00:00"},
        "scopes": ["openid"],
    }
    await actions.apply_decision({"item_kind": "command", "action": "oauth_token_relay", "payload": payload_base})

    payload_new = {**payload_base, "account_email": "tweede@example.com",
                   "credentials": {"access_token": "at2", "refresh_token": "rt2", "expiry": "2026-08-11T00:00:00+00:00"}}
    await actions.apply_decision({"item_kind": "command", "action": "oauth_token_relay", "payload": payload_new})

    with get_conn() as c:
        rows = c.execute(
            "SELECT account_email FROM oauth_accounts WHERE site_id = ? AND provider = 'google'", (site["id"],),
        ).fetchall()
    assert len(rows) == 1  # geen tweede rij — ON CONFLICT overschrijft
    assert rows[0]["account_email"] == "tweede@example.com"


@pytest.mark.asyncio
async def test_oauth_token_relay_zonder_refresh_token_geweigerd(site):
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "oauth_token_relay",
        "payload": {
            "site_id": site["id"], "provider": "google", "account_email": "x@example.com",
            "credentials": {"access_token": "at"}, "scopes": [],
        },
    })
    assert not ok
    assert "tokens" in msg.lower()


@pytest.mark.asyncio
async def test_oauth_token_relay_onbekende_provider_geweigerd(site):
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "oauth_token_relay",
        "payload": {
            "site_id": site["id"], "provider": "facebook", "account_email": "x@example.com",
            "credentials": {"access_token": "at", "refresh_token": "rt"}, "scopes": [],
        },
    })
    assert not ok


# ── onboarding_step1/2/4/complete + new_client ──────────────────────────────

@pytest.mark.asyncio
async def test_onboarding_step1_command_schrijft_naar_sites_profile(site):
    from backend.domains.bridge import actions
    from backend.domains.seo import sites as sites_service

    ok, _ = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_step1",
        "payload": {"site_id": site["id"], "profile": "Wij helpen coaches aan hun eerste 10 klanten."},
    })
    assert ok
    assert sites_service.get_site(site["id"])["profile"] == "Wij helpen coaches aan hun eerste 10 klanten."


@pytest.mark.asyncio
async def test_onboarding_step1_command_zonder_site_id_geweigerd():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_step1", "payload": {"profile": "iets"},
    })
    assert not ok
    assert "site_id" in msg.lower()


@pytest.mark.asyncio
async def test_onboarding_step4_en_complete_ronden_af(site, monkeypatch):
    from backend.domains.bridge import actions
    from backend.domains.iris import service as iris_service
    import json

    # Stap 2 vergt de LLM-distiller — mock 'm net als test_onboarding.py, we
    # testen hier het commando-pad, niet de distillatie zelf.
    async def fake_llm(system, prompt, max_tokens=1500):
        return json.dumps({"samenvatting": "kort", "principes": ["iets"], "tags": [], "scope": "all"})
    monkeypatch.setattr(iris_service, "_llm", fake_llm)

    await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_step1",
        "payload": {"site_id": site["id"], "profile": "Genoeg tekst om over de drempel van 40 tekens te komen."},
    })
    await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_step2",
        "payload": {"site_id": site["id"], "tone_text": "Informeel maar deskundig, korte zinnen."},
    })
    ok4, _ = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_step4",
        "payload": {"site_id": site["id"], "preset": "laag"},
    })
    assert ok4

    ok_complete, msg = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_complete",
        "payload": {"site_id": site["id"]},
    })
    assert ok_complete
    assert site["name"] in msg

    from backend.domains.onboarding import service as onboarding_service
    assert onboarding_service.get_status(site["id"])["onboarded_at"]


@pytest.mark.asyncio
async def test_onboarding_new_client_command_maakt_site_aan():
    from backend.domains.bridge import actions
    from backend.domains.seo import sites as sites_service

    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_new_client",
        "payload": {"name": "Verse Klant via Iris Remote"},
    })
    assert ok
    assert "Verse Klant via Iris Remote" in msg

    sites = [s for s in sites_service.list_sites() if s["name"] == "Verse Klant via Iris Remote"]
    assert len(sites) == 1
    sites_service.delete_site(sites[0]["id"])


@pytest.mark.asyncio
async def test_onboarding_new_client_zonder_naam_geweigerd():
    from backend.domains.bridge import actions
    ok, msg = await actions.apply_decision({
        "item_kind": "command", "action": "onboarding_new_client", "payload": {},
    })
    assert not ok
    assert "naam" in msg.lower()
